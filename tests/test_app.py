import re

from fastapi.testclient import TestClient

from app.main import create_app


def test_healthz_is_public_and_rest_requires_auth(settings, source):
    anon = TestClient(create_app(settings, source))
    assert anon.get("/healthz").status_code == 200
    assert anon.get("/").status_code == 401
    assert anon.get("/api/issues").status_code == 401


def test_fail_closed_without_password(settings, source, monkeypatch):
    monkeypatch.delenv("BASIC_AUTH_PASSWORD")
    anon = TestClient(create_app(settings, source), headers={"Authorization": "Basic YWRtaW46"})
    assert anon.get("/").status_code == 401


def test_pages_render(client):
    for path in ["/", "/repo/event-calendar", "/repo/job-watcher", "/tray", "/tray?repo=event-calendar", "/guide"]:
        r = client.get(path)
        assert r.status_code == 200, path
    assert client.get("/repo/nope").status_code == 404


def test_actions_require_csrf(client, csrf):
    body = {"action": "merge_pr", "params": {"repo": "event-calendar", "number": 6}}
    assert client.post("/actions/check", json=body).status_code == 403
    r = client.post("/actions/check", json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["tripped"] is True


def test_merge_flow_via_http(client, csrf):
    h = {"X-CSRF-Token": csrf}
    body = {"action": "merge_pr", "params": {"repo": "event-calendar", "number": 5}}
    assert client.post("/actions/run", json=body, headers=h).json()["needs_ack"] is True
    r = client.post("/actions/run", json={**body, "ack": True}, headers=h).json()
    assert r["ok"] is True
    issues = client.get("/api/issues?repo=event-calendar&state=all").json()["issues"]
    assert next(i for i in issues if i["number"] == 12)["state"] == "closed"


def test_tray_form_creates_issue_and_trips_on_secret(client, csrf):
    ok = client.post("/tray", data={"csrf_token": csrf, "repo": "job-watcher", "title": "通知が遅い", "situation": "朝", "kind": "bug", "priority": "p1"})
    assert "付箋 #1 を貼りました" in ok.text
    ng = client.post("/tray", data={"csrf_token": csrf, "repo": "job-watcher", "title": "x", "situation": "password=hunter22"})
    assert "ブレーカーが落ちました" in ng.text
    assert client.post("/tray", data={"repo": "job-watcher", "title": "x"}).status_code == 403


def test_api_issue_for_ai(client):
    r = client.post("/api/issues", json={"repo": "event-calendar", "title": "AI から", "body": "状況", "kind": "idea", "source": "claude"})
    assert r.status_code == 201 and r.json()["ok"]
    assert client.post("/api/issues", data={"repo": "event-calendar", "title": "x"}).status_code == 415
    bad = client.post("/api/issues", json={"repo": "event-calendar", "title": "x", "body": "token: ghp_abcdefghijklmnopqrstuvwxyz0123"})
    assert bad.status_code == 422 and bad.json()["tripped"]


def test_circuit_escapes_github_text(client, source):
    source.views["event-calendar"].issues[0].title = "<script>alert(1)</script>"
    html = client.get("/repo/event-calendar").text
    assert "<script>alert(1)</script>" not in html
