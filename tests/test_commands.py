import asyncio
import re

from app.actions import Actions, ActionError
from app.commands import command, delete_local, push_local, q, safe_ref, switch_local
from app.demo import DemoSource
from app.explain import parts
from app.guards import ACTIONS
from app.model import Commit, LocalBranch, MergedBranch, now
from app.service import Service

SAMPLE = {"repo": "event-calendar", "title": "T", "number": 7, "name": "fix/1-x", "head": "fix/1-x"}
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
    p = parts(v)["cleanup"]
    assert p["title"] == "作業台の片付け" and "2 本" in p["body"]
    labels = [a["label"] for a in p["actions"]]
    assert all(a.get("terminal") and "action" not in a for a in p["actions"])
    assert [a["cmd"] for a in p["actions"]] == [f'git -C "{WD}" branch -d fix/9-old-icon', f'git -C "{WD}" branch -d fix/11-typo']
    assert "GitHub にも同名の分岐が残っています" in labels[1] and "GitHub にも" not in labels[0]


def test_cleanup_part_caps_the_list_and_handles_empty_and_no_workbench():
    v = asyncio.run(DemoSource().repo_view("event-calendar"))
    base = v.local.merged[0]
    v.local.merged = [MergedBranch(f"old/{i}", base.head, now(), fresh=(i == 0)) for i in range(12)]
    p = parts(v)["cleanup"]
    assert len(p["actions"]) == 10 and "12 本" in p["body"] and "10 本を出しています" in p["body"]
    assert "作ったばかり" in p["actions"][0]["label"] and "作ったばかり" not in p["actions"][1]["label"]
    v.local.merged = []
    p = parts(v)["cleanup"]
    assert p["actions"] == [] and "ありません" in p["body"]
    v.local.dev_exists = False
    assert "cleanup" not in parts(v)


def test_circuit_page_has_the_cleanup_chip(client):
    html = client.get("/repo/event-calendar").text
    assert 'data-part="cleanup"' in html and "作業台の片付け · 2本" in html
