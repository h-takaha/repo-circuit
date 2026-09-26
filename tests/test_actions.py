import asyncio
import json

import pytest

from app.actions import ActionError, Actions
from app.config import Settings
from app.demo import DemoSource
from app.service import Service


class Spy(DemoSource):
    def __init__(self):
        super().__init__()
        self.writes = []

    async def merge_pr(self, repo, number):
        self.writes.append(("merge", repo, number))
        return await super().merge_pr(repo, number)

    async def delete_branch(self, repo, name):
        self.writes.append(("delete", repo, name))
        return await super().delete_branch(repo, name)


@pytest.fixture
def acts(settings):
    src = Spy()
    return Actions(Service(settings, src), settings.data_dir / "guard-log.jsonl"), src, settings


def run(coro):
    return asyncio.run(coro)


def test_tripped_action_never_reaches_github(acts):
    a, src, _ = acts
    res = run(a.run("merge_pr", {"repo": "event-calendar", "number": 6}, ack=True))
    assert res["tripped"] and not res["ok"]
    assert src.writes == []


def test_warning_requires_ack(acts):
    a, src, _ = acts
    res = run(a.run("merge_pr", {"repo": "event-calendar", "number": 5}))
    assert not res["ok"] and res["needs_ack"]
    assert src.writes == []
    res = run(a.run("merge_pr", {"repo": "event-calendar", "number": 5}, ack=True))
    assert res["ok"]
    assert src.writes == [("merge", "event-calendar", 5)]


def test_merge_closes_linked_issue(acts):
    a, src, _ = acts
    run(a.run("merge_pr", {"repo": "event-calendar", "number": 5}, ack=True))
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
        run(a.check("merge_pr", {"repo": "nope", "number": 1}))
    with pytest.raises(ActionError):
        run(a.check("force_push", {"repo": "event-calendar"}))


def test_guard_log_records_rules_but_not_text(acts):
    a, _, settings = acts
    run(a.check("create_issue", {"repo": "job-watcher", "title": "x", "situation": "password=supersecret"}))
    lines = (settings.data_dir / "guard-log.jsonl").read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[-1])
    assert entry["rules"] == [{"id": "secret", "level": "trip"}]
    assert "supersecret" not in lines[-1]
