"""作業台の読み取りを、一時フォルダの本物の git repo で確かめる。"""

import os
import shutil
import subprocess
import time

import pytest

from app.localgit import read_local_state


@pytest.fixture(scope="module", autouse=True)
def git_identity():
    mp = pytest.MonkeyPatch()
    for k in ("AUTHOR", "COMMITTER"):
        mp.setenv(f"GIT_{k}_NAME", "t")
        mp.setenv(f"GIT_{k}_EMAIL", "t@example.com")
    yield
    mp.undo()


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def commit(cwd, name, text):
    (cwd / name).write_text(text, encoding="utf-8")
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", f"{name}: {text}")
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def lab_template(tmp_path_factory, git_identity):
    """origin（bare）と作業台 lab/git/tool。main と feat は push 済み、feat に未送電 1、local-only に 2、未コミット 1。
    git の起動が遅い環境があるので1度だけ作り、テストごとに複製する。"""
    tmp_path = tmp_path_factory.mktemp("lab")
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    dev = tmp_path / "git" / "tool"
    git(tmp_path, "clone", "-q", str(origin), str(dev))
    git(dev, "switch", "-q", "-c", "main")
    main = commit(dev, "a.txt", "1")
    git(dev, "push", "-q", "-u", "origin", "main")
    git(dev, "switch", "-q", "-c", "feat")
    feat_pushed = commit(dev, "b.txt", "1")
    git(dev, "push", "-q", "-u", "origin", "feat")
    commit(dev, "b.txt", "2")
    git(dev, "switch", "-q", "-c", "local-only", "main")
    commit(dev, "c.txt", "1")
    commit(dev, "c.txt", "日本語の見出し")  # Windows の既定コードページで読むと壊れる文字
    git(dev, "switch", "-q", "feat")
    (dev / "b.txt").write_text("作業中", encoding="utf-8")
    return tmp_path, {"main": main, "feat": feat_pushed}


@pytest.fixture
def lab(lab_template, tmp_path):
    src, heads = lab_template
    shutil.copytree(src, tmp_path / "lab")
    return tmp_path / "lab", heads


def test_reads_uncommitted_and_unpushed_per_branch(lab):
    root, heads = lab
    st = read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main")
    assert st.dev_exists and st.dev_branch == "feat"
    assert st.dev_uncommitted == 1 and st.dev_unpushed == 1
    by = {b.name: b for b in st.branches}
    assert "main" not in by  # 幹線は分岐の一覧に入れない
    assert by["feat"].on_github and by["feat"].unpushed == 1 and not by["feat"].github_ahead
    assert [c.headline for c in by["feat"].commits] == ["b.txt: 2"]
    lo = by["local-only"]
    assert not lo.on_github and lo.unpushed == 2 and lo.base == heads["main"]
    assert [c.headline for c in lo.commits] == ["c.txt: 1", "c.txt: 日本語の見出し"]  # 古い順・UTF-8 で読む


def test_github_ahead_when_remote_sha_is_unknown_locally(lab):
    root, heads = lab
    st = read_local_state(root / "git", root / "run", "tool", remote_heads={**heads, "feat": "f" * 40}, default_branch="main")
    feat = next(b for b in st.branches if b.name == "feat")
    assert feat.github_ahead and feat.unpushed == 1  # どの origin/* にも無いはんだ点を数える


def test_falls_back_to_origin_refs_without_github_heads(lab):
    root, _ = lab
    st = read_local_state(root / "git", root / "run", "tool", default_branch="main")
    by = {b.name: b for b in st.branches}
    assert by["feat"].on_github and by["feat"].unpushed == 1
    assert not by["local-only"].on_github


def test_on_main_counts_unpushed_against_github(lab):
    root, heads = lab
    dev = root / "git" / "tool"
    git(dev, "stash", "-q")
    git(dev, "switch", "-q", "main")
    commit(dev, "d.txt", "1")
    st = read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main")
    assert st.dev_branch == "main" and st.dev_unpushed == 1 and st.dev_uncommitted == 0


def test_reading_does_not_write_the_index(lab):
    root, heads = lab
    dev = root / "git" / "tool"
    index = dev / ".git" / "index"
    # 内容は同じまま更新時刻だけ変える。普通の git status なら index を書き直す状況
    past = time.time() - 100
    os.utime(dev / "a.txt", (past, past))
    before = index.stat().st_mtime_ns
    read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main")
    assert index.stat().st_mtime_ns == before


def test_branch_missing_from_github_list_falls_back_to_origin_ref(lab):
    root, heads = lab
    # GitHub から渡る分岐の一覧は上限で切られることがある。feat が漏れても origin/feat があれば push 済みと分かる
    st = read_local_state(root / "git", root / "run", "tool", remote_heads={"main": heads["main"]}, default_branch="main")
    feat = next(b for b in st.branches if b.name == "feat")
    assert feat.on_github and feat.unpushed == 1


def test_light_read_for_panel_counts_totals_without_branch_details(lab):
    root, heads = lab
    st = read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main", detail=False)
    assert st.dev_branch == "feat" and st.dev_uncommitted == 1
    assert st.unpushed_total == 3  # feat の 1 ＋ local-only の 2
    assert st.branches == []  # 分岐ごとには読まない


def _tidy_lab(lab):
    """main を1つ進めて push し、幹線に入りきった分岐を3本作る（old-done / just-made / busy）。busy は別の作業場所で使用中。"""
    root, heads = lab
    dev = root / "git" / "tool"
    git(dev, "stash", "-q")
    git(dev, "switch", "-q", "main")
    commit(dev, "d.txt", "1")
    # push はしない（複製元の origin を他のテストと共有しているため）。fetch 済みの状態を、手元の origin/main を進めて再現する
    git(dev, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(dev, "branch", "old-done", heads["main"])  # 幹線に入りきり済み。先端は幹線の先端と別
    git(dev, "branch", "just-made", "main")  # 先端が幹線の先端と同じ = 作ったばかり
    git(dev, "branch", "busy", heads["main"])
    git(dev, "worktree", "add", "-q", str(root / "wt"), "busy")
    return root, heads, dev


def test_merged_leftovers_are_listed_for_cleanup_but_not_drawn(lab):
    root, heads, _ = _tidy_lab(lab)
    st = read_local_state(root / "git", root / "run", "tool", remote_heads={**heads, "old-done": heads["main"]}, default_branch="main")
    by = {m.name: m for m in st.merged}
    # 除外: 既定分岐と今いる分岐（main）、別の作業場所で使用中（busy）、幹線に入りきっていない分岐（feat / local-only）
    assert set(by) == {"old-done", "just-made"}
    assert by["just-made"].fresh and not by["old-done"].fresh
    assert by["old-done"].on_github and not by["just-made"].on_github
    assert not {"old-done", "just-made"} & {b.name for b in st.branches}  # 回路図の分岐には入れない


def test_current_branch_is_never_a_cleanup_candidate(lab):
    root, heads, dev = _tidy_lab(lab)
    git(dev, "switch", "-q", "old-done")
    st = read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main")
    assert "old-done" not in {m.name for m in st.merged} and st.dev_branch == "old-done"


def test_cleanup_reading_changes_nothing(lab):
    root, heads, dev = _tidy_lab(lab)
    before = (git(dev, "for-each-ref", "refs"), git(dev, "worktree", "list"), git(dev, "rev-parse", "HEAD"))
    read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main")
    assert before == (git(dev, "for-each-ref", "refs"), git(dev, "worktree", "list"), git(dev, "rev-parse", "HEAD"))


def test_stale_origin_refs_are_those_github_no_longer_has(lab):
    root, heads = lab
    read = lambda names: read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main", github_names=names).stale_origin
    assert read({"main"}) == ["feat"]  # 手元の origin/feat は、GitHub にもう無い
    assert read({"main", "feat"}) == []
    assert read(set()) == ["feat", "main"] and "HEAD" not in read(set())
    assert read(None) == []  # GitHub の分岐名の全部が分からないときは、古いとは言わない


def test_reads_where_the_github_copy_of_the_default_branch_points(lab):
    root, heads = lab
    for detail in (True, False):  # 配電盤（軽い読み取り）でも、回路図でも読む
        st = read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main", detail=detail)
        assert st.origin_main == heads["main"]
    st = read_local_state(root / "git", root / "run", "tool", default_branch="nope")
    assert st.origin_main == ""


def _shas(lab):
    root, heads = lab
    dev = root / "git" / "tool"
    lo = git(dev, "rev-list", "main..local-only").split()  # 新しい順。lo[-1] が古い方
    feat_new = git(dev, "rev-list", f"{heads['feat']}..feat").split()
    return root, heads, lo, feat_new


def test_commits_already_in_github_main_are_not_unpushed(lab):
    """作業台の origin/* が古くても、GitHub の幹線にすでにあるコミットは送電済み。"""
    root, heads, lo, feat_new = _shas(lab)
    read = lambda main: read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main", github_main=main)
    by = lambda st: {b.name: b for b in st.branches}

    st = read(None)  # 従来どおり
    assert by(st)["local-only"].unpushed == 2 and by(st)["feat"].unpushed == 1 and st.dev_unpushed == 1

    st = read(set(lo) | set(feat_new))  # 全部が GitHub の幹線にある
    assert by(st)["local-only"].unpushed == 0 and by(st)["local-only"].commits == []
    assert by(st)["feat"].unpushed == 0 and st.dev_unpushed == 0  # origin/feat との比較で数えた分も引く

    st = read({lo[-1]})  # 古い方の1つだけが幹線にある
    lo_b = by(st)["local-only"]
    assert lo_b.unpushed == 1 and [c.headline for c in lo_b.commits] == ["c.txt: 日本語の見出し"]

    st = read({"f" * 40})  # 無関係な sha は何も引かない
    assert by(st)["local-only"].unpushed == 2 and by(st)["feat"].unpushed == 1


def test_dashboard_total_also_subtracts_commits_in_github_main(lab, monkeypatch):
    import app.localgit as lg

    root, heads, lo, feat_new = _shas(lab)
    total = lambda main: read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main",
                                          detail=False, github_main=main).unpushed_total
    assert total(None) == 3
    assert total(set(lo)) == 1
    assert total(set(lo) | set(feat_new)) == 0
    monkeypatch.setattr(lg, "MAX_UNPUSHED_LIST", 1)  # 一覧が上限に達したら、引かずに従来の数え方（--count）に戻る
    assert total(set(lo)) == 3


def test_a_local_only_branch_whose_commits_are_in_github_main_leaves_the_pipeline(lab):
    """偽の「未送電・作業台のみ・GitHub へ送る」の行が、工程表から消える。"""
    from app.model import RepoView
    from app.pipeline import rows

    root, heads, lo, feat_new = _shas(lab)

    def names(main):
        st = read_local_state(root / "git", root / "run", "tool", remote_heads=heads, default_branch="main", github_main=main)
        return [r.get("name") for r in rows(RepoView("tool", "main", [], [], [], [], local=st))]

    assert "local-only" in names(None)
    assert "local-only" not in names(set(lo))


def test_displayed_github_target_is_read_even_outside_local_branch_limit(lab, monkeypatch):
    import app.localgit as lg

    root, heads, _, _ = _shas(lab)
    monkeypatch.setattr(lg, 'MAX_LOCAL_BRANCHES', 0)
    st = read_local_state(root / 'git', root / 'run', 'tool', remote_heads={**heads, 'local-only': heads['main']})
    assert next(b for b in st.branches if b.name == 'local-only').unpushed == 2


def test_targeted_read_detects_noncurrent_unpushed_work_and_dirty_current_branch(lab):
    from app.localgit import read_branch_work

    root, heads, _, _ = _shas(lab)
    dev = root / 'git' / 'tool'
    assert read_branch_work(dev, 'feat', heads['feat']) is True
    assert read_branch_work(dev, 'local-only', git(dev, 'rev-parse', 'HEAD')) is True
    assert read_branch_work(dev, 'missing', heads['main']) is False


@pytest.mark.parametrize('failed', ['status', 'for-each-ref', 'rev-list', 'worktree'])
def test_targeted_read_failures_are_unknown(lab, monkeypatch, failed):
    import app.localgit as lg

    root, heads, _, _ = _shas(lab)
    actual = lg._git
    monkeypatch.setattr(lg, '_git', lambda cwd, *args: None if failed in args else actual(cwd, *args))
    assert lg.read_branch_work(root / 'git' / 'tool', 'local-only', heads['main']) is None


def test_targeted_read_keeps_dirty_linked_worktree(lab):
    from app.localgit import read_branch_work

    root, heads, dev = _tidy_lab(lab)
    assert read_branch_work(dev, 'busy', heads['main']) is False
    (root / 'wt' / 'new.txt').write_text('work in progress', encoding='utf-8')
    assert read_branch_work(dev, 'busy', heads['main']) is True
