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
