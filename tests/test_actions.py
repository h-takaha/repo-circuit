import asyncio
import copy
import json

import pytest

from app.actions import ActionError, Actions, normalize
from app.github import GitHubError
from app.config import Settings
from app.demo import DemoSource
from app.service import Service


class Spy(DemoSource):
    def __init__(self):
        super().__init__()
        self.writes = []

    async def merge_pr(self, repo, number, expected_head_sha):
        self.writes.append(("merge", repo, number, expected_head_sha))
        return await super().merge_pr(repo, number, expected_head_sha)

    async def delete_branch(self, repo, name):
        self.writes.append(("delete", repo, name))
        return await super().delete_branch(repo, name)


@pytest.fixture
def acts(settings):
    src = Spy()
    return Actions(Service(settings, src), settings.data_dir / "guard-log.jsonl"), src, settings


def run(coro):
    return asyncio.run(coro)


def merge_params(src, number=5):
    pr = next(p for p in src.views["event-calendar"].pulls if p.number == number)
    return {"repo": "event-calendar", "number": number,
            "expected_head_sha": pr.head_sha, "expected_base": pr.base}


def test_tripped_action_never_reaches_github(acts):
    a, src, _ = acts
    res = run(a.run("merge_pr", merge_params(src, 6), ack=True))
    assert res["tripped"] and not res["ok"]
    assert src.writes == []


def test_warning_requires_ack(acts):
    a, src, _ = acts
    res = run(a.run("merge_pr", merge_params(src)))
    assert not res["ok"] and res["needs_ack"]
    assert src.writes == []
    res = run(a.run("merge_pr", merge_params(src), ack=True))
    assert res["ok"]
    assert src.writes == [("merge", "event-calendar", 5, res["pr_head_sha"])]
    assert res["merge_commit_sha"] and res["merge_commit_sha"] != res["pr_head_sha"]


def test_merge_closes_linked_issue(acts):
    a, src, _ = acts
    run(a.run("merge_pr", merge_params(src), ack=True))
    v = run(src.repo_view("event-calendar"))
    assert v.issue(12).state == "closed"


def test_create_issue_builds_labels_and_body(acts):
    a, src, _ = acts
    res = run(a.run("create_issue", {"repo": "job-watcher", "title": "通知が遅い", "situation": "朝に届かない", "kind": "bug", "priority": "p1", "source": "claude"}))
    assert res["ok"]
    issue = run(src.repo_view("job-watcher")).issues[0]
    assert issue.labels == ["feedback", "bug", "p1"]
    assert "朝に届かない" in issue.body and "source=claude" in issue.body


def test_unknown_repo_and_action_rejected(acts):
    a, _, _ = acts
    with pytest.raises(ActionError):
        run(a.check("merge_pr", {"repo": "nope", "number": 1, "expected_head_sha": "a" * 40, "expected_base": "main"}))
    with pytest.raises(ActionError):
        run(a.check("force_push", {"repo": "event-calendar"}))


def test_guard_log_records_rules_but_not_text(acts):
    a, _, settings = acts
    run(a.check("create_issue", {"repo": "job-watcher", "title": "x", "situation": "password=supersecret"}))
    lines = (settings.data_dir / "guard-log.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["rules"] == [{"id": "secret", "level": "trip"}]
    assert "supersecret" not in lines[-1]

@pytest.mark.parametrize("value", ["", "short", "a" * 39, "g" * 40, 12, None, "a" * 40 + ";bad"])
def test_merge_requires_a_complete_confirmed_sha(value):
    with pytest.raises(ActionError):
        normalize("merge_pr", {"repo": "event-calendar", "number": 5,
                              "expected_head_sha": value, "expected_base": "main"})


def test_merge_does_not_adopt_head_changed_after_check_or_old_ack(acts):
    a, src, _ = acts
    params = merge_params(src)
    assert not run(a.check("merge_pr", params))["tripped"]
    src.views["event-calendar"].pulls[0].head_sha = "e" * 40
    blocked = run(a.run("merge_pr", params, ack=True))
    assert blocked["tripped"] and not blocked["ok"]
    assert any(v["id"] == "merge_head_changed" for v in blocked["verdicts"])
    assert src.writes == []


def test_merge_checks_fresh_head_even_when_display_view_is_cached(settings):
    class Cached(Spy):
        def __init__(self):
            super().__init__()
            self.cached = copy.deepcopy(self.views)

        async def repo_view(self, name):
            return copy.deepcopy(self.cached[name])

    src = Cached()
    params = merge_params(src)
    src.views["event-calendar"].pulls[0].head_sha = "e" * 40
    a = Actions(Service(settings, src), None)
    blocked = run(a.run("merge_pr", params, ack=True))
    assert any(v["id"] == "merge_head_changed" for v in blocked["verdicts"])
    assert src.writes == []


@pytest.mark.parametrize("change, rule", [
    ("base", "merge_base_changed"), ("closed", "merge_missing"), ("ci", "merge_ci_pending"),
])
def test_merge_rechecks_target_and_checks_before_using_ack(acts, change, rule):
    a, src, _ = acts
    params = merge_params(src)
    run(a.check("merge_pr", params))
    pr = src.views["event-calendar"].pulls[0]
    if change == "base":
        pr.base = "other"
    elif change == "closed":
        pr.state = "closed"
    else:
        pr.ci = "pending"
    blocked = run(a.run("merge_pr", params, ack=True))
    assert any(v["id"] == rule for v in blocked["verdicts"])
    assert src.writes == []


def test_head_changed_during_merge_is_conflict_without_retry(settings):
    class Race(Spy):
        async def merge_pr(self, repo, number, expected_head_sha):
            self.writes.append((repo, number, expected_head_sha))
            raise GitHubError(409, "Head SHA changed")

    src = Race()
    params = merge_params(src)
    with pytest.raises(ActionError) as caught:
        run(Actions(Service(settings, src), None).run("merge_pr", params, ack=True))
    assert caught.value.status == 409
    assert src.writes == [("event-calendar", 5, params["expected_head_sha"])]


def test_unsuccessful_merge_result_is_not_reported_as_success(settings):
    class Unmerged(Spy):
        async def merge_pr(self, repo, number, expected_head_sha):
            return {"merged": False}

    src = Unmerged()
    result = run(Actions(Service(settings, src), None).run("merge_pr", merge_params(src), ack=True))
    assert not result["ok"]
