import asyncio

import pytest

from app.demo import DemoSource
from app.guards import RULES, TRIP, WARN, evaluate


def view(name="event-calendar"):
    return asyncio.run(DemoSource().repo_view(name))


def ids(action, params, v):
    if action == "merge_pr":
        pr = next((p for p in v.pulls if p.number == params.get("number")), None)
        if pr:
            params = {"expected_head_sha": pr.head_sha, "expected_base": pr.base, **params}
    return {x.id: x.level for x in evaluate(action, {"repo": v.name, **params}, v)}


def test_every_rule_has_teaching_text():
    for r in RULES:
        assert r.level in (TRIP, WARN)
        assert r.rule and r.why, r.id


def test_merge_red_led_trips():
    assert ids("merge_pr", {"number": 6}, view())["merge_ci_failure"] == TRIP


def test_merge_green_led_only_warns_about_autodeploy_and_run_lag():
    got = ids("merge_pr", {"number": 5}, view())
    assert TRIP not in got.values()
    assert got["merge_autodeploy"] == WARN


def test_merge_pending_trips():
    assert ids("merge_pr", {"number": 9}, view("deck-studio"))["merge_ci_pending"] == TRIP


def test_merge_unavailable_trips():
    v = view()
    v.pulls[0].ci = "unavailable"
    got = ids("merge_pr", {"number": v.pulls[0].number}, v)
    assert got["merge_ci_unavailable"] == TRIP
    assert "merge_no_ci" not in got
    assert "merge_ci_unavailable" not in ids("close_pr", {"number": v.pulls[0].number}, v)


@pytest.mark.parametrize("ci", ["success", "failure", "pending", "none"])
def test_available_checks_do_not_trip_unavailable_rule(ci):
    v = view()
    v.pulls[0].ci = ci
    got = ids("merge_pr", {"number": v.pulls[0].number}, v)
    assert "merge_ci_unavailable" not in got
    if ci == "none":
        assert got["merge_no_ci"] == WARN


def test_merge_conflict_trips():
    v = view()
    v.pulls[0].mergeable_state = "dirty"
    assert ids("merge_pr", {"number": v.pulls[0].number}, v)["merge_conflict"] == TRIP


def test_run_lag_warns_when_run_clone_is_far_behind():
    v = view()
    v.local.run_head = "0" * 40  # 表示範囲に無い＝大きく遅れている
    assert ids("merge_pr", {"number": 5}, v)["merge_run_lagging"] == WARN


def test_delete_rules():
    v = view()
    assert ids("delete_branch", {"name": "main"}, v)["delete_default"] == TRIP
    assert ids("delete_branch", {"name": "exp/old-layout"}, v)["delete_unmerged"] == TRIP
    assert ids("delete_branch", {"name": "fix/12-button-size"}, v)["delete_open_pr"] == TRIP


def test_create_pr_rules():
    v = view()
    v.branches[0].ahead = 0
    got = ids("create_pr", {"head": v.branches[0].name, "title": "t", "body": ""}, v)
    assert got["pr_no_diff"] == TRIP and got["pr_no_fixes"] == WARN
    assert ids("create_pr", {"head": "fix/12-button-size", "title": "t", "body": "Fixes #12"}, v)["pr_exists"] == TRIP


@pytest.mark.parametrize('unpushed, dirty, trips', [(0, 0, False), (1, 0, True), (0, 1, True)])
def test_delete_local_work_breaker(unpushed, dirty, trips):
    v = view()
    name = v.local.dev_branch
    v.local_branch(name).unpushed = unpushed
    v.local.dev_unpushed = unpushed
    v.local.dev_uncommitted = dirty
    assert ('delete_local_work' in ids('delete_branch', {'name': name}, v)) == trips


@pytest.mark.parametrize("text", [
    "token: ghp_abcdefghijklmnopqrstuvwxyz0123",
    "password=hunter22",
    "github_pat_11ABCDEFG0123456789_abcdefghij",
    "パスワード：abcd1234",
    "-----BEGIN RSA PRIVATE KEY-----",
])
def test_secret_trips(text):
    assert ids("create_issue", {"title": "x", "situation": text}, view())["secret"] == TRIP


@pytest.mark.parametrize("text", ["パスワードが効かない", ".env の BASIC_AUTH_PASSWORD が効いていない", "token の期限が切れた"])
def test_secret_names_without_values_pass(text):
    assert "secret" not in ids("create_issue", {"title": "x", "situation": text}, view())


@pytest.mark.parametrize("name", ["fix 12", "fix/..x", "-bad", "fix/", "修正"])
def test_bad_branch_names(name):
    assert ids("create_branch", {"name": name}, view())["branch_bad_name"] == TRIP


def test_branch_exists():
    assert ids("create_branch", {"name": "fix/12-button-size"}, view())["branch_exists"] == TRIP


def test_close_issue_without_fix_warns_but_with_fix_passes():
    v = view()
    assert ids("close_issue", {"number": 15}, v)["close_without_fix"] == WARN
    assert "close_without_fix" not in ids("close_issue", {"number": 10}, v)

def test_merge_head_guard_trips_and_same_head_does_not_trip():
    v = view()
    pr = v.pulls[0]
    assert ids("merge_pr", {"number": pr.number, "expected_head_sha": "e" * 40}, v)["merge_head_changed"] == TRIP
    assert "merge_head_changed" not in ids("merge_pr", {"number": pr.number, "expected_head_sha": pr.head_sha}, v)


def test_merge_base_guard_trips_and_same_base_does_not_trip():
    v = view()
    pr = v.pulls[0]
    assert ids("merge_pr", {"number": pr.number, "expected_base": "other"}, v)["merge_base_changed"] == TRIP
    assert "merge_base_changed" not in ids("merge_pr", {"number": pr.number, "expected_base": pr.base}, v)


def test_merge_missing_guard_trips_and_open_pr_does_not_trip():
    v = view()
    assert ids("merge_pr", {"number": 999}, v)["merge_missing"] == TRIP
    assert "merge_missing" not in ids("merge_pr", {"number": v.pulls[0].number}, v)
