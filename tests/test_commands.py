import asyncio
import re

from app.actions import Actions, ActionError
from app.commands import command, delete_local, push_local, q, safe_ref, switch_local
from app.demo import DemoSource
from app.explain import parts
from app.guards import ACTIONS
from app.model import Commit, LocalBranch, MergedBranch, now
from app.service import Service

SAMPLE = {"repo": "event-calendar", "title": "T", "number": 7, "name": "fix/1-x", "head": "fix/1-x",
          "expected_head_sha": "a" * 40, "expected_base": "main"}
WD = r"C:\lab\git\event-calendar"


def test_every_action_has_a_command():
    for action in ACTIONS:
        assert command(action, SAMPLE), action


def test_create_branch_fetches_first_and_uses_the_repo_default_branch():
    cmd = command("create_branch", SAMPLE, "trunk")
    assert cmd.startswith(f'git -C "{WD}" fetch origin && ')
    assert "switch -c fix/1-x origin/trunk" in cmd


def test_workbench_commands_pin_the_directory_with_git_dash_c():
    assert switch_local(WD, "feat/a") == f'git -C "{WD}" switch feat/a'
    assert delete_local(WD, "feat/a") == f'git -C "{WD}" branch -d feat/a'
    assert push_local(WD, "feat/a", True) == f'git -C "{WD}" push -u origin feat/a'
    assert push_local(WD, "feat/a", False) == f'git -C "{WD}" push origin feat/a'
    assert command("delete_branch", SAMPLE).startswith("git -C ")


def test_text_is_single_quoted_so_powershell_does_not_expand_it():
    """コピーして貼ると実行される。$(...) や " を含むタイトルが、貼った瞬間に実行されてはいけない。"""
    nasty = 'a $(calc) "b" `c` it\'s'
    cmd = command("create_pr", {**SAMPLE, "title": nasty, "body": "Fixes #1\n$(calc)"})
    assert q(nasty) in cmd and "-b 'Fixes #1\n$(calc)'" in cmd
    assert "it''s" in cmd
    outside = re.sub(r"'(?:[^']|'')*'", "''", cmd)  # 単一引用符の中身を除いた残り
    assert not any(ch in outside for ch in '"$`;&|'), outside
    issue = command("create_issue", {"repo": "r", "title": "$(calc)", "situation": "`x`", "kind": "bug", "priority": "p1"})
    assert "-t '$(calc)'" in issue and "-b '## 気づいた状況" in issue and "-l feedback,bug,p1" in issue


def test_unsafe_branch_names_never_reach_a_command():
    for bad in ("a;calc", "a b", "$(x)", "-rf", "a..b", "x.lock", "a&b", ""):
        assert not safe_ref(bad), bad
        assert switch_local(WD, bad) == delete_local(WD, bad) == push_local(WD, bad, True) == ""
    assert command("delete_branch", {**SAMPLE, "name": "a;calc"}) == ""
    assert command("create_pr", {**SAMPLE, "head": "a;calc"}) == ""
    assert command("create_branch", {**SAMPLE, "name": "a;calc"}) == ""


def _github_buttons(part):
    return [a for a in part["actions"] if not a.get("terminal")]


def test_every_panel_button_shows_the_command_the_confirmation_will_show(settings):
    """回路図のボタン横の「コマンドで言うと」は、確認画面（Actions.check）と同じ文字列になる。"""
    view = asyncio.run(DemoSource().repo_view("event-calendar"))
    acts = Actions(Service(settings, DemoSource()), None)
    seen = 0
    for part_id, part in parts(view).items():
        for a in _github_buttons(part):
            assert a["cmd"], (part_id, a["action"])
            seen += 1
            params = {**a["params"], **{f["name"]: f["value"] for f in a["fields"]}}
            try:
                checked = asyncio.run(acts.check(a["action"], params))
            except ActionError:  # 入力が空のままでは確認に進めない操作（メモなど）
                continue
            assert a["cmd"] == checked["cmd"], (part_id, a["action"])
    assert seen


def test_terminal_only_buttons_for_the_workbench():
    """作業台の操作は、アプリが実行せず、コマンドをコピーさせるだけ。"""
    v = asyncio.run(DemoSource().repo_view("event-calendar"))
    b = v.branches[0]
    v.local.dev_dir = WD
    v.local.dev_branch = "main"
    v.local.branches = [LocalBranch(b.name, "abc", unpushed=2, on_github=True)]
    labels = {a["label"]: a for a in parts(v)[f"branch:{b.name}"]["actions"] if a.get("terminal")}
    assert set(labels) == {"GitHub に送る（push）", "作業台をこの分岐に切り替える"}  # 手元の分岐の削除は「作業台の片付け」だけ
    assert labels["作業台をこの分岐に切り替える"]["cmd"] == f'git -C "{WD}" switch {b.name}'
    assert all("action" not in a and "params" not in a for a in labels.values())  # /actions/* には送られない

    v.local.dev_branch = b.name  # いま立っている分岐は、切り替えも削除も出さない
    names = [a["label"] for a in parts(v)[f"branch:{b.name}"]["actions"] if a.get("terminal")]
    assert names == ["GitHub に送る（push）"]


def test_workbench_only_branch_offers_push_and_switch():
    v = asyncio.run(DemoSource().repo_view("event-calendar"))
    v.local.dev_dir, v.local.dev_branch = WD, "main"
    c = Commit("d" * 40, "wip", now())
    v.local.branches = [LocalBranch("feat/local-only", "d" * 40, unpushed=1, on_github=False, commits=[c])]
    p = parts(v)
    cmds = {a["label"]: a["cmd"] for a in p["local:feat/local-only"]["actions"]}
    assert cmds["GitHub に送る（push）"] == f'git -C "{WD}" push -u origin feat/local-only'
    assert cmds["作業台をこの分岐に切り替える"] == f'git -C "{WD}" switch feat/local-only'
    assert p[f"commit:{c.sha}"]["actions"][0]["cmd"] == f'git -C "{WD}" push -u origin feat/local-only'


def test_command_endpoint_needs_csrf_and_returns_the_current_input(client, csrf):
    body = {"action": "create_issue", "params": {"repo": "event-calendar", "title": "保存 $(x)", "kind": "bug", "priority": "p1"}}
    assert client.post("/actions/command", json=body).status_code == 403
    r = client.post("/actions/command", json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200
    assert "-t '保存 $(x)'" in r.json()["cmd"]
    bad = client.post("/actions/command", json={"action": "nope", "params": {}}, headers={"X-CSRF-Token": csrf})
    assert bad.status_code == 400


def test_tray_shows_the_issue_command_before_submitting(client):
    html = client.get("/tray").text
    assert 'id="issue-cmd"' in html
    assert 'id="issue-copy"' in html
    assert "/static/tray.js" in html
    assert client.get("/static/tray.js").status_code == 200


def test_cleanup_part_lists_merged_leftovers_with_terminal_delete_buttons():
    v = asyncio.run(DemoSource().repo_view("event-calendar"))
    v.local.dev_dir = WD
    v.local.origin_stale = False
    v.local.stale_origin = ["fix/2-old-label"]
    p = parts(v)["cleanup"]
    assert p["title"] == "作業台の片付け" and "2 本" in p["body"]
    labels = [a["label"] for a in p["actions"]]
    assert all(a.get("terminal") and "action" not in a for a in p["actions"])
    assert [a["cmd"] for a in p["actions"]] == [
        f'git -C "{WD}" branch -d fix/9-old-icon', f'git -C "{WD}" branch -d fix/11-typo', f'git -C "{WD}" remote prune origin']  # 最後は古い記録の掃除
    assert "GitHub にも同名の分岐が残っています" in labels[1] and "GitHub にも" not in labels[0]


def test_cleanup_part_caps_the_list_and_handles_empty_and_no_workbench():
    v = asyncio.run(DemoSource().repo_view("event-calendar"))
    base = v.local.merged[0]
    v.local.origin_stale = False
    v.local.merged = [MergedBranch(f"old/{i}", base.head, now(), fresh=(i == 0)) for i in range(12)]
    v.local.stale_origin = []
    p = parts(v)["cleanup"]
    assert len(p["actions"]) == 10 and "12 本" in p["body"] and "10 本を出しています" in p["body"]
    assert "作ったばかり" in p["actions"][0]["label"] and "作ったばかり" not in p["actions"][1]["label"]
    v.local.merged = []
    p = parts(v)["cleanup"]
    assert p["actions"] == [] and "ありません" in p["body"] and "古い記録" not in p["body"]
    v.local.dev_exists = False
    assert "cleanup" not in parts(v)


def test_circuit_page_has_the_cleanup_chip(client):
    html = client.get("/repo/event-calendar").text
    assert 'data-part="cleanup"' in html and "作業台の片付け · 2本" in html


def test_cleanup_part_offers_one_prune_button_for_stale_origin_refs():
    v = asyncio.run(DemoSource().repo_view("event-calendar"))
    v.local.dev_dir = WD
    v.local.origin_stale = False
    v.local.merged = []
    v.local.stale_origin = ["a", "b"]
    p = parts(v)["cleanup"]
    assert "2 個" in p["body"] and "a、b" in p["body"]
    assert [(a["label"], a["cmd"]) for a in p["actions"]] == [("古い GitHub のコピー（origin/…）をまとめて掃除する", f'git -C "{WD}" remote prune origin')]
    v.local.stale_origin = []
    assert parts(v)["cleanup"]["actions"] == []


def test_circuit_chip_counts_stale_refs(client):
    html = client.get("/repo/event-calendar").text
    assert "作業台の片付け · 2本 · 古い記録 1個" in html


def test_stale_origin_notice_shows_on_dashboard_status_dev_flag_and_cleanup():
    from app.explain import status_text, summary

    v = asyncio.run(DemoSource().repo_view("event-calendar"))
    v.local.dev_dir = WD
    v.pulls, v.branches = [], []  # 赤ランプ・放置分岐が「次にやること」を先取りしないように
    assert v.local.origin_stale and v.local.origin_behind == 2
    sm = summary(v)
    assert sm["origin_stale"] and "実際より多いかもしれません" in sm["sentence"]
    assert sm["next"] == "作業台でコミットする"  # 未コミットのほうを先に
    v.local.dev_uncommitted = 0
    assert summary(v)["next"] == "作業台の GitHub のコピーを最新にする（fetch）"  # 偽の警告に「push しろ」とは言わない
    assert "2 コミット古い" in status_text(v) and "実際より多いかもしれません" in status_text(v)
    p = parts(v)
    fetch = f'git -C "{WD}" fetch origin'
    assert [a["cmd"] for a in p["dev"]["actions"]] == [fetch] and p["dev"]["actions"][0]["terminal"]
    assert "2 コミット古い" in p["dev"]["body"] and fetch in [a["cmd"] for a in p["cleanup"]["actions"]]

    v.local.origin_stale, v.local.origin_behind = False, 0  # 新しい写しなら、何も言わない・ボタンも出さない
    assert summary(v)["next"] == "作業台から GitHub へ送る（push）"
    assert "古い状態" not in status_text(v) and p["dev"]["actions"] and not parts(v)["dev"]["actions"]
    assert fetch not in [a["cmd"] for a in parts(v)["cleanup"]["actions"]]


def test_dashboard_marks_unpushed_as_reference_when_the_copy_is_stale(client):
    html = client.get("/").text
    assert "未送電※" in html and "GitHub のコピーが古いため" in html


def test_mark_origin_lag_counts_commits_or_says_unknown():
    from app.model import Commit, LocalState, RepoView
    from app.service import mark_origin_lag

    def view(origin_main, n=8):
        cs = [Commit(f"{i:040x}", f"c{i}", now()) for i in range(n)]  # 古い順
        return RepoView("r", "main", cs, [], [], [], local=LocalState(dev_exists=True, origin_main=origin_main)), cs

    v, cs = view(f"{7:040x}")  # 先端と同じ
    mark_origin_lag(v)
    assert not v.local.origin_stale
    v, cs = view(f"{4:040x}")  # 3コミット古い
    mark_origin_lag(v)
    assert v.local.origin_stale and v.local.origin_behind == 3
    v, cs = view("f" * 40)  # 表示範囲（直近8コミット）の外 = かなり古い
    mark_origin_lag(v)
    assert v.local.origin_stale and v.local.origin_behind is None
    v, cs = view("")  # 作業台に origin/main が無いときは判定しない
    mark_origin_lag(v)
    assert not v.local.origin_stale
    v, cs = view(f"{4:040x}")
    v.local.dev_exists = False
    mark_origin_lag(v)
    assert not v.local.origin_stale

def test_merge_command_pins_the_confirmed_head_and_never_fills_it_from_latest():
    assert command("merge_pr", SAMPLE) == (
        "gh pr merge 7 -R h-takaha/event-calendar --merge --match-head-commit " + "a" * 40)
    for bad in ("", "short", "g" * 40, "a" * 40 + ";bad"):
        assert command("merge_pr", {**SAMPLE, "expected_head_sha": bad}) == ""
    assert command("merge_pr", {"repo": "event-calendar", "number": 7}) == ""
