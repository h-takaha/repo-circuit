import asyncio
import re

import httpx
import respx
import pytest

from fastapi.testclient import TestClient

from app.main import create_app

from app.github import GitHubError, GitHubSource

API = "https://api.github.com/repos/h-takaha/tool"


def _pr(n, head, sha, body=""):
    return {"number": n, "title": "t", "body": body, "head": {"ref": head, "sha": sha}, "base": {"ref": "main"},
            "state": "open", "merged_at": None, "draft": False, "mergeable_state": "clean", "html_url": ""}


def _mock_agent_ops(check_status=403, detail_status=200, router=respx, runs=None, workflows=1):
    """runs=None なら Actions API も 403（権限なし）。リストを渡すとその実行結果を返す。"""
    api = "https://api.github.com/repos/h-takaha/agent-ops"
    c = {"sha": "m1", "commit": {"message": "main", "committer": {"date": "2026-09-01T00:00:00Z"}}}
    pr = _pr(2, "fix/1-x", "b1", "Fixes #1")
    router.get("https://api.github.com/user/repos").respond(200, json=[
        {"name": "agent-ops", "owner": {"login": "h-takaha"}}])
    router.get(api).respond(200, json={"default_branch": "main"})
    router.get(f"{api}/commits").respond(200, json=[c])
    router.get(f"{api}/branches").respond(200, json=[{"name": "main"}, {"name": "fix/1-x"}])
    router.get(f"{api}/compare/main...fix/1-x").respond(200, json={
        "ahead_by": 1, "behind_by": 0, "merge_base_commit": c,
        "commits": [{**c, "sha": "b1"}]})
    router.get(f"{api}/pulls", params={"state": "open"}).respond(200, json=[pr])
    router.get(f"{api}/pulls", params={"state": "closed"}).respond(200, json=[])
    router.get(f"{api}/issues").respond(200, json=[
        {"number": 1, "title": "skill 配置", "state": "open", "labels": [{"name": "feedback"}],
         "created_at": "2026-09-01T00:00:00Z"}])
    router.get(f"{api}/pulls/2").respond(detail_status, json=pr if detail_status == 200 else {"message": "Forbidden"})
    checks = router.get(f"{api}/commits/b1/check-runs").respond(
        check_status, json={"check_runs": []} if check_status == 200 else {"message": "Forbidden"})
    if runs is None:
        router.get(f"{api}/actions/runs").respond(403, json={"message": "Forbidden"})
    else:
        router.get(f"{api}/actions/runs", params={"head_sha": "b1"}).respond(200, json={"workflow_runs": runs})
        router.get(f"{api}/actions/workflows").respond(200, json={"total_count": workflows})
    return api, checks


@respx.mock
def test_checks_403_preserves_repo_view():
    _mock_agent_ops()
    src = GitHubSource("t", "h-takaha")
    v = asyncio.run(src.repo_view("agent-ops"))
    assert v.head_sha == "m1"
    assert v.branches[0].name == "fix/1-x"
    assert v.issues[0].title == "skill 配置"
    assert v.pulls[0].ci == "unavailable"
    assert v.pulls[0].mergeable_state == "clean"


def test_checks_403_pages_render_and_merge_never_writes(settings):
    with respx.mock(assert_all_called=False) as router:
        _check_pages_and_merge(settings, router)


def _check_pages_and_merge(settings, router):
    api, checks = _mock_agent_ops(router=router)
    merge = router.put(f"{api}/pulls/2/merge").respond(200, json={"merged": True})
    src = GitHubSource("t", "h-takaha")
    auth = {"Authorization": "Basic YWRtaW46cHc="}
    with TestClient(create_app(settings, src), headers=auth) as client:
        panel = client.get("/")
        assert panel.status_code == 200
        assert 'href="/repo/agent-ops"' in panel.text and "取得不可" in panel.text
        circuit = client.get("/repo/agent-ops")
        assert circuit.status_code == 200
        assert 'aria-label="agent-ops の配線図"' in circuit.text
        assert ">取得不可</text>" in circuit.text
        assert "skill 配置" in circuit.text
        tray = client.get("/tray?repo=agent-ops")
        assert tray.status_code == 200 and "取得不可" in tray.text
        guide = client.get("/guide").text
        assert "検査ランプが取得不可のスイッチは閉じられません" in guide
        csrf = re.search(r'name="csrf-token" content="([^"]+)"', guide).group(1)
        body = {"action": "merge_pr", "params": {"repo": "agent-ops", "number": 2}, "ack": True}
        blocked = client.post("/actions/run", json=body, headers={"X-CSRF-Token": csrf}).json()
        assert blocked["tripped"] and not blocked["ok"]
        assert any(v["id"] == "merge_ci_unavailable" for v in blocked["verdicts"])
        assert not merge.called
        # 403 をキャッシュしないので、権限を直すと「取得不可」から復帰する。
        checks.respond(200, json={"check_runs": [{"status": "completed", "conclusion": "success"}]})
        recovered = client.get("/repo/agent-ops")
        assert recovered.status_code == 200 and ">合格</text>" in recovered.text
        assert ">取得不可</text>" not in recovered.text


@pytest.mark.parametrize("check_status, detail_status", [(401, 200), (404, 200), (500, 200), (403, 403)])
def test_other_github_errors_are_not_hidden(check_status, detail_status):
    with respx.mock(assert_all_called=False) as router:
        _mock_agent_ops(check_status, detail_status, router)
        src = GitHubSource("t", "h-takaha")
        with pytest.raises(GitHubError) as caught:
            asyncio.run(src.repo_view("agent-ops"))
    assert caught.value.status == (detail_status if detail_status != 200 else check_status)


def _run(n, status, conclusion, workflow=1, attempt=1):
    return {"workflow_id": workflow, "run_number": n, "run_attempt": attempt, "status": status, "conclusion": conclusion}


@pytest.mark.parametrize("runs, workflows, expected", [
    ([_run(1, "completed", "success")], 1, "success"),
    ([_run(1, "completed", "failure")], 1, "failure"),
    ([_run(1, "in_progress", None)], 1, "pending"),
    # やり直して通ったら、前の失敗は数えない
    ([_run(1, "completed", "failure"), _run(1, "completed", "success", attempt=2)], 1, "success"),
    ([_run(3, "completed", "success"), _run(2, "completed", "failure")], 1, "success"),
    # 別のワークフローが失敗していれば失敗
    ([_run(1, "completed", "success"), _run(1, "completed", "failure", workflow=2)], 2, "failure"),
    ([], 0, "none"),  # ワークフローが1つも無い repo は検査なし
    ([], 1, "unavailable"),  # ワークフローはあるのに実行が無い（まだ作られていないかも）→ 取得不可のまま
])
def test_checks_403_falls_back_to_actions_runs(runs, workflows, expected):
    with respx.mock(assert_all_called=False) as router:
        _mock_agent_ops(router=router, runs=runs, workflows=workflows)
        src = GitHubSource("t", "h-takaha")
        assert asyncio.run(src.repo_view("agent-ops")).pulls[0].ci == expected


@respx.mock
def test_empty_checks_are_none_not_unavailable():
    _mock_agent_ops(check_status=200)
    src = GitHubSource("t", "h-takaha")
    assert asyncio.run(src.repo_view("agent-ops")).pulls[0].ci == "none"


@respx.mock
def test_repo_view_maps_github_responses():
    c = {"sha": "m1", "commit": {"message": "first", "committer": {"date": "2026-09-01T00:00:00Z"}}}
    respx.get(API).mock(return_value=httpx.Response(200, json={"default_branch": "main", "html_url": "u"}))
    respx.get(f"{API}/commits").mock(return_value=httpx.Response(200, json=[c]))
    respx.get(f"{API}/branches").mock(return_value=httpx.Response(200, json=[{"name": "main"}, {"name": "fix/1-x"}]))
    respx.get(f"{API}/pulls", params={"state": "open"}).mock(return_value=httpx.Response(200, json=[_pr(2, "fix/1-x", "b1", "Fixes #1")]))
    respx.get(f"{API}/pulls", params={"state": "closed"}).mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/issues").mock(return_value=httpx.Response(200, json=[
        {"number": 1, "title": "i", "body": None, "state": "open", "labels": [{"name": "feedback"}, {"name": "bug"}], "created_at": "2026-09-01T00:00:00Z"},
        {"number": 2, "title": "pr", "state": "open", "labels": [], "pull_request": {}},
    ]))
    respx.get(f"{API}/compare/main...fix/1-x").mock(return_value=httpx.Response(200, json={
        "ahead_by": 1, "behind_by": 0, "merge_base_commit": c,
        "commits": [{"sha": "b1", "commit": {"message": "fix", "committer": {"date": "2026-09-02T00:00:00Z"}}}]}))
    respx.get(f"{API}/pulls/2").mock(return_value=httpx.Response(200, json=_pr(2, "fix/1-x", "b1", "Fixes #1")))
    respx.get(f"{API}/commits/b1/check-runs").mock(return_value=httpx.Response(200, json={"check_runs": [
        {"status": "completed", "conclusion": "failure"}]}))

    src = GitHubSource("t", "h-takaha")
    v = asyncio.run(src.repo_view("tool"))
    assert [c.sha for c in v.main_commits] == ["m1"]
    assert v.branches[0].ahead == 1 and v.branches[0].commits[0].sha == "b1"
    assert v.pulls[0].ci == "failure"
    assert [i.number for i in v.issues] == [1]  # PR は付箋に混ぜない
    assert v.issues[0].kind == "bug"
    assert v.github_names == ["main", "fix/1-x"]  # 古い origin/… の判定に使う。詳細を読む8本の上限とは別に、名前は全部持つ


@respx.mock
def test_github_names_are_unknown_when_the_branch_list_may_be_truncated():
    """/branches は100本まで。100本に達したら全部とは限らないので、None（分からない）にする。"""
    c = {"sha": "m1", "commit": {"message": "first", "committer": {"date": "2026-09-01T00:00:00Z"}}}
    respx.get(API).mock(return_value=httpx.Response(200, json={"default_branch": "main", "html_url": "u"}))
    respx.get(f"{API}/commits").mock(return_value=httpx.Response(200, json=[c]))
    respx.get(f"{API}/branches").mock(return_value=httpx.Response(200, json=[{"name": "main"}] + [{"name": f"b{i}"} for i in range(99)]))
    respx.get(f"{API}/pulls").mock(return_value=httpx.Response(200, json=[]))
    respx.get(f"{API}/issues").mock(return_value=httpx.Response(200, json=[]))
    respx.get(url__regex=rf"{API}/compare/.*").mock(return_value=httpx.Response(200, json={"ahead_by": 0, "behind_by": 0, "merge_base_commit": c, "commits": []}))
    v = asyncio.run(GitHubSource("t", "h-takaha").repo_view("tool"))
    assert v.github_names is None


@respx.mock
def test_create_issue_creates_missing_labels():
    respx.get(url__regex=rf"{API}/labels/.*").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    made = respx.post(f"{API}/labels").mock(return_value=httpx.Response(201, json={}))
    issue = respx.post(f"{API}/issues").mock(return_value=httpx.Response(201, json={"number": 7, "html_url": "x"}))
    src = GitHubSource("t", "h-takaha")
    res = asyncio.run(src.create_issue("tool", "t", "b", ["feedback", "bug", "p1"]))
    assert res["number"] == 7 and made.call_count == 3 and issue.called
