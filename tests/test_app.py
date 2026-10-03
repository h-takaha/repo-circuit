import re

from fastapi.testclient import TestClient

from conftest import AUTH

from app.main import create_app
from app.demo import DemoSource
from app.github import GitHubError


def _merge_params(number=5):
    pr = next(p for p in DemoSource().views["event-calendar"].pulls if p.number == number)
    return {"repo": "event-calendar", "number": number,
            "expected_head_sha": pr.head_sha, "expected_base": pr.base}


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
    body = {"action": "merge_pr", "params": _merge_params(6)}
    assert client.post("/actions/check", json=body).status_code == 403
    r = client.post("/actions/check", json=body, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200 and r.json()["tripped"] is True


def test_merge_flow_via_http(client, csrf):
    h = {"X-CSRF-Token": csrf}
    body = {"action": "merge_pr", "params": _merge_params()}
    assert client.post("/actions/run", json=body, headers=h).json()["needs_ack"] is True
    r = client.post("/actions/run", json={**body, "ack": True}, headers=h).json()
    assert r["ok"] is True
    issues = client.get("/api/issues?repo=event-calendar&state=all").json()["issues"]
    assert next(i for i in issues if i["number"] == 12)["state"] == "closed"


def test_tray_form_creates_issue_and_trips_on_secret(client, csrf):
    ok = client.post("/tray", data={"csrf_token": csrf, "submit_id": _submit_id(client), "repo": "job-watcher", "title": "通知が遅い", "situation": "朝", "kind": "bug", "priority": "p1"})
    assert "付箋 #1 を貼りました" in ok.text
    ng = client.post("/tray", data={"csrf_token": csrf, "submit_id": _submit_id(client), "repo": "job-watcher", "title": "x", "situation": "password=hunter22"})
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


def _submit_id(client):
    return re.search(r'name="submit_id" value="([^"]+)"', client.get("/tray").text).group(1)


def test_tray_post_redirects_and_resubmit_does_not_duplicate(client, csrf, source):
    sid = _submit_id(client)
    data = {"csrf_token": csrf, "submit_id": sid, "repo": "job-watcher", "title": "二重起票テスト", "situation": "x"}
    first = client.post("/tray", data=data, follow_redirects=False)
    assert first.status_code == 303 and "created_no=1" in first.headers["location"]
    page = client.get(first.headers["location"])
    assert "付箋 #1 を貼りました" in page.text and "二重起票テスト" in page.text
    again = client.post("/tray", data=data, follow_redirects=False)  # 再読み込み・二重クリック
    assert again.status_code == 200 and "送信済み" in again.text
    assert [i.title for i in source.views["job-watcher"].issues] == ["二重起票テスト"]


def test_created_issue_is_shown_even_if_github_list_lags(client):
    page = client.get("/tray?created_repo=job-watcher&created_no=9&created_title=まだ一覧に無い&created_kind=idea&created_priority=p3")
    assert "まだ一覧に無い" in page.text and "たった今" in page.text


def test_static_urls_carry_content_version(client):
    html = client.get("/repo/event-calendar").text
    assert re.search(r'/static/style\.css\?v=[0-9a-f]{10}', html)
    assert re.search(r'/static/circuit\.js\?v=[0-9a-f]{10}', html)
    assert client.get(re.search(r'(/static/circuit\.js\?v=[0-9a-f]+)', html).group(1)).status_code == 200


def test_csrf_and_submit_tokens_survive_restart_and_other_workers(settings, source, csrf):
    # 別プロセス（再起動後・別ワーカー）相当の app でも、同じページのトークンが通る
    other = TestClient(create_app(settings, source), headers=AUTH)
    body = {"action": "merge_pr", "params": _merge_params(6)}
    assert other.post("/actions/check", json=body, headers={"X-CSRF-Token": csrf}).status_code == 200

    first = TestClient(create_app(settings, source), headers=AUTH)
    sid = _submit_id(first)
    data = {"csrf_token": csrf, "submit_id": sid, "repo": "job-watcher", "title": "別ワーカー"}
    assert other.post("/tray", data=data, follow_redirects=False).status_code == 303
    again = first.post("/tray", data=data, follow_redirects=False)  # 使用済みはどのワーカーでも拒否
    assert again.status_code == 200 and "送信済み" in again.text
    forged = {**data, "submit_id": "abc.0000"}
    assert "送信済み" in other.post("/tray", data=forged, follow_redirects=False).text

def test_missing_confirmed_sha_fails_closed(client, csrf):
    response = client.post("/actions/run", headers={"X-CSRF-Token": csrf},
                           json={"action": "merge_pr", "params": {"repo": "event-calendar", "number": 5}, "ack": True})
    assert response.status_code == 400 and not response.json()["ok"]


def test_http_old_ack_cannot_merge_a_new_head(client, csrf, source):
    body = {"action": "merge_pr", "params": _merge_params()}
    header = {"X-CSRF-Token": csrf}
    assert not client.post("/actions/check", json=body, headers=header).json()["tripped"]
    source.views["event-calendar"].pulls[0].head_sha = "e" * 40
    response = client.post("/actions/run", json={**body, "ack": True}, headers=header)
    assert response.json()["tripped"] and not response.json()["ok"]
    assert source.views["event-calendar"].pulls[0].state == "open"


def test_merge_api_head_mismatch_returns_http_409(client, csrf, source, monkeypatch):
    calls = []

    async def raced_merge(repo, number, expected_head_sha):
        calls.append(expected_head_sha)
        raise GitHubError(409, "Head SHA changed")

    monkeypatch.setattr(source, "merge_pr", raced_merge)
    params = _merge_params()
    response = client.post("/actions/run", headers={"X-CSRF-Token": csrf},
                           json={"action": "merge_pr", "params": params, "ack": True})
    assert response.status_code == 409 and not response.json()["ok"]
    assert calls == [params["expected_head_sha"]]
