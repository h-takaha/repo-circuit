import asyncio

from app.actions import Actions, ActionError
from app.commands import command
from app.demo import DemoSource
from app.explain import parts
from app.guards import ACTIONS
from app.service import Service

SAMPLE = {"repo": "event-calendar", "title": "T", "number": 7, "name": "fix/1-x", "head": "fix/1-x"}


def test_every_action_has_a_command():
    for action in ACTIONS:
        assert command(action, SAMPLE), action


def test_create_branch_uses_the_repo_default_branch():
    assert "origin/trunk" in command("create_branch", SAMPLE, "trunk")


def test_every_panel_button_shows_the_command_the_confirmation_will_show(settings):
    """回路図のボタン横の「コマンドで言うと」は、確認画面（Actions.check）と同じ文字列になる。"""
    view = asyncio.run(DemoSource().repo_view("event-calendar"))
    acts = Actions(Service(settings, DemoSource()), None)
    seen = 0
    for part_id, part in parts(view).items():
        for a in part["actions"]:
            assert a["cmd"], (part_id, a["action"])
            seen += 1
            params = {**a["params"], **{f["name"]: f["value"] for f in a["fields"]}}
            try:
                checked = asyncio.run(acts.check(a["action"], params))
            except ActionError:  # 入力が空のままでは確認に進めない操作（メモなど）
                continue
            assert a["cmd"] == checked["cmd"], (part_id, a["action"])
    assert seen


def test_tray_shows_the_issue_command_before_submitting(client):
    html = client.get("/tray").text
    assert 'id="issue-cmd"' in html
    assert "/static/tray.js" in html
    assert client.get("/static/tray.js").status_code == 200
