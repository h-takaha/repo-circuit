import asyncio

import httpx
import respx

from app.github import GitHubSource

API = "https://api.github.com/repos/h-takaha/tool"


def _pr(n, head, sha, body=""):
    return {"number": n, "title": "t", "body": body, "head": {"ref": head, "sha": sha}, "base": {"ref": "main"},
            "state": "open", "merged_at": None, "draft": False, "mergeable_state": "clean", "html_url": ""}


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


@respx.mock
def test_create_issue_creates_missing_labels():
    respx.get(url__regex=rf"{API}/labels/.*").mock(return_value=httpx.Response(404, json={"message": "Not Found"}))
    made = respx.post(f"{API}/labels").mock(return_value=httpx.Response(201, json={}))
    issue = respx.post(f"{API}/issues").mock(return_value=httpx.Response(201, json={"number": 7, "html_url": "x"}))
    src = GitHubSource("t", "h-takaha")
    res = asyncio.run(src.create_issue("tool", "t", "b", ["feedback", "bug", "p1"]))
    assert res["number"] == 7 and made.call_count == 3 and issue.called
