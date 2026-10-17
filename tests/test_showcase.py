"""紹介用起動口で実トークン・実FSに触れず、説明した体験を再現できること。"""

import base64
import json
import re

import pytest
from fastapi.testclient import TestClient

from app.demo_server import create_demo_app

AUTH = {"Authorization": "Basic " + base64.b64encode(b"demo:demo").decode()}


@pytest.fixture
def demo(tmp_path, monkeypatch):
    for key in ("GITHUB_TOKEN", "BASIC_AUTH_USER", "BASIC_AUTH_PASSWORD", "CSRF_SECRET"):
        monkeypatch.setenv(key, "inherited-value-must-not-be-used")

    def forbidden(*args, **kwargs):
        raise AssertionError("紹介用デモが実データ源を使いました")

    import app.main
    import app.service
    monkeypatch.setattr(app.main, "GitHubSource", forbidden)
    monkeypatch.setattr(app.main, "read_publish_state", forbidden)
    for name in ("load_autodeploy", "read_local_state", "read_worktrees"):
        monkeypatch.setattr(app.service, name, forbidden)
    with TestClient(create_demo_app(tmp_path / "demo"), headers=AUTH) as client:
        yield client, tmp_path


def csrf(client):
    return re.search(r'name="csrf-token" content="([^"]+)"', client.get("/guide").text).group(1)


def merge_body(client, number):
    html = client.get("/repo/practice-board").text
    parts = json.loads(re.search(r'<script[^>]*id="parts-data"[^>]*>(.*?)</script>', html, re.S).group(1))
    action = parts[f"pr:{number}"]["actions"][0]
    return {"action": action["action"], "params": action["params"]}


def test_isolated_demo_renders_only_fictional_repo_and_no_github_links(demo):
    client, root = demo
    assert client.get("/healthz").json() == {"ok": True, "demo": True}
    assert client.get("/api/repos").json() == {"repos": ["practice-board", "practice-details"]}
    for path in ("/", "/repo/practice-board", "/repo/practice-board/worktrees", "/tray", "/guide",
                 "/repo/practice-board/publish-guide", "/repo/practice-details", "/repo/practice-details/worktrees"):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "event-calendar" not in response.text and "https://github.com/h-takaha" not in response.text
    assert client.get("/repo/event-calendar").status_code == 404
    assert not (root / "demo").exists()  # 表示だけではディスクへの保存も不要


def test_auth_and_csrf_are_still_required(demo):
    client, _ = demo
    assert client.get("/", headers={"Authorization": ""}).status_code == 401
    assert client.post("/actions/run", json=merge_body(client, 5)).status_code == 403


def test_failed_inspection_stops_merge_even_with_ack(demo):
    client, _ = demo
    headers = {"X-CSRF-Token": csrf(client)}
    body = merge_body(client, 6)
    response = client.post("/actions/run", json={**body, "ack": True}, headers=headers).json()
    assert response["tripped"] and not response["ok"]
    assert "merge_ci_failure" in [v["id"] for v in response["verdicts"]]
    issue = next(i for i in client.get("/api/issues?state=all").json()["issues"] if i["number"] == 14)
    assert issue["state"] == "open"


def test_successful_merge_closes_linked_issue_but_does_not_move_runtime(demo):
    client, root = demo
    headers = {"X-CSRF-Token": csrf(client)}
    body = merge_body(client, 5)
    before = client.get("/repo/practice-board?sel=pr:5").text
    checked = client.post("/actions/check", json=body, headers=headers).json()
    assert not checked["tripped"] and not checked["needs_ack"]
    assert any("本番反映（デプロイ）は別" in line for line in checked["preview"])
    merged = client.post("/actions/run", json={**body, "ack": True}, headers=headers).json()
    assert merged["ok"] and merged["merge_commit_sha"] and not merged["url"]
    issues = client.get("/api/issues?state=all").json()["issues"]
    assert next(i for i in issues if i["number"] == 12)["state"] == "closed"
    after = client.get("/repo/practice-board").text
    assert "Merge pull request #5" in after and 'id="lamp"' not in after
    assert before != after
    with TestClient(create_demo_app(root / "restart"), headers=AUTH) as restarted:
        assert next(i for i in restarted.get("/api/issues?state=all").json()["issues"] if i["number"] == 12)["state"] == "open"


def test_new_demo_issues_have_no_real_github_destination(demo):
    client, _ = demo
    result = client.post("/api/issues", json={"repo": "practice-board", "title": "練習用の付箋"})
    assert result.status_code == 201 and result.json()["ok"] and not result.json()["url"]
    assert "練習用の付箋" in client.get("/tray").text


def test_first_experience_defers_detailed_state_and_extra_actions(demo):
    client, _ = demo
    home = client.get("/").text
    assert "実際のテストを実行することはありません" in home
    assert 'class="stats"' not in home
    intro = client.get("/repo/practice-board").text
    for text in ('aria-label="工程表"', 'data-part="cleanup"', 'data-part="sync"', 'data-part="publish"',
                 'id="lamp"', 'aria-label="作業台マーカー"', 'data-part="branch:exp/old-layout"'):
        assert text not in intro
    assert 'href="/repo/practice-details"' in intro
    data = json.loads(re.search(r'<script[^>]*id="parts-data"[^>]*>(.*?)</script>', intro, re.S).group(1))
    assert [k for k in data if k.startswith("pr:")] == ["pr:5", "pr:6"]
    assert {a["action"] for part in data.values() for a in part.get("actions", [])} == {"merge_pr"}
    detail = client.get("/repo/practice-details").text
    assert 'aria-label="工程表"' in detail and 'id="lamp"' in detail
    assert "未送電" in detail and "GitHub のコピーが古い" in detail


def test_close_without_merge_is_distinct_and_deployment_stays_separate(demo):
    client, _ = demo
    html = client.get("/repo/practice-details").text
    parts = json.loads(re.search(r'<script[^>]*id="parts-data"[^>]*>(.*?)</script>', html, re.S).group(1))
    actions = parts["pr:5"]["actions"]
    assert actions[0]["label"] == "合流する（マージ）"
    assert actions[1]["label"] == "合流せずにPRを閉じる（Close pull request）"
    headers = {"X-CSRF-Token": csrf(client)}
    body = {"action": "close_pr", "params": {"repo": "practice-details", "number": 5}}
    result = client.post("/actions/run", json={**body, "ack": True}, headers=headers).json()
    assert result["ok"]
    issues = client.get("/api/issues?repo=practice-details&state=all").json()["issues"]
    assert next(i for i in issues if i["number"] == 12)["state"] == "open"
    after = client.get("/repo/practice-details").text
    assert "Merge pull request #5" not in after
