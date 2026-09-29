import asyncio
import re

from app.actions import Actions, ActionError
from app.commands import command, delete_local, push_local, q, safe_ref, switch_local
from app.demo import DemoSource
from app.explain import parts
from app.guards import ACTIONS
from app.model import LocalBranch, Commit, now
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
    assert set(labels) == {"GitHub に送る（push）", "作業台をこの分岐に切り替える", "作業台のこの分岐を消す（未合流なら git が断る）"}
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
