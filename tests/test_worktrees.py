"""worktreeの実HEAD・所在・残存パス・読取り専用境界を検証する。"""

import asyncio
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import time
from threading import Lock

import pytest
from fastapi.testclient import TestClient

from app.demo import DemoSource
from app.github import GitHubSource
from app.main import create_app
from app.model import PullRequest
from app.service import Service
from app.worktrees import (FileSummary, GitResult, Reader, RepoEvidence, WorktreeRow,
                           _scan_row, integration, parse_status, parse_worktrees,
                           read_worktrees, repo_path)
from conftest import AUTH


def git(path, *args):
    env = os.environ.copy()
    for who in ("AUTHOR", "COMMITTER"):
        env[f"GIT_{who}_NAME"] = "test"
        env[f"GIT_{who}_EMAIL"] = "test@example.com"
    return subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True,
                          env=env, text=True, encoding="utf-8").stdout.strip()


def commit(path, filename="tracked.txt", value="first", *, message=None):
    (path / filename).write_text(value, encoding="utf-8")
    git(path, "add", filename)
    git(path, "commit", "-qm", message or value)
    return git(path, "rev-parse", "HEAD")


@pytest.fixture
def lab(tmp_path):
    root = tmp_path / "git"
    dev = root / "tool"
    dev.mkdir(parents=True)
    git(dev, "init", "-q", "-b", "trunk")
    head = commit(dev)
    (dev / ".gitignore").write_text(".venv/\n.env\n", encoding="utf-8")
    commit(dev, ".gitignore", ".venv/\n.env\n")
    head = git(dev, "rev-parse", "HEAD")
    git(dev, "update-ref", "refs/remotes/origin/trunk", head)
    git(dev, "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/trunk")
    return root, dev, head


def pr(head, base="trunk", merge=""):
    return PullRequest(1, "finished", "", "feat", base, "closed", True, False,
                       head, "none", "unknown", merge_commit_sha=merge)


def test_all_registration_rows_files_and_readonly(lab, tmp_path):
    root, dev, head = lab
    extra = tmp_path / "additional"
    detached = tmp_path / "detached"
    missing = tmp_path / "missing"
    git(dev, "worktree", "add", "-qb", "feat", str(extra))
    git(dev, "worktree", "add", "-q", "--detach", str(detached))
    git(dev, "worktree", "add", "-qb", "gone", str(missing))
    git(dev, "worktree", "lock", "--reason", "keep for review", str(extra))
    shutil.rmtree(missing)  # テストfixtureだけ。reader自体は削除しない。
    (extra / "tracked.txt").write_text("dirty", encoding="utf-8")
    (extra / "notes").mkdir()
    (extra / "notes" / "a").write_text("a")
    (extra / "notes" / "b").write_text("b")
    (extra / ".venv").mkdir()
    (extra / ".venv" / "many-files").write_text("x")
    (extra / ".env").write_text("fixture")
    before = (git(dev, "rev-parse", "HEAD"), git(dev, "show-ref"),
              git(dev, "worktree", "list", "--porcelain"), (dev / ".git" / "index").read_bytes(),
              (Path(git(extra, "rev-parse", "--absolute-git-dir")) / "index").read_bytes())
    snapshot = read_worktrees(root, "tool", RepoEvidence("trunk", head))
    assert len(snapshot.rows) == 4
    assert snapshot.rows[0].is_primary and snapshot.rows[0].path == dev.as_posix()
    assert [r.path for r in snapshot.rows[1:]] == sorted(r.path for r in snapshot.rows[1:])
    rows = {r.path: r for r in snapshot.rows}
    row = rows[extra.as_posix()]
    assert row.head_sha == head and row.locked and row.locked_reason == "keep for review"
    assert row.files.tracked == 1 and row.files.untracked == 1 and row.files.ignored == 2
    assert row.files.untracked_samples == ["notes/"]
    assert set(row.files.ignored_samples) == {".env", ".venv/"}
    assert row.integration.state == "integrated"
    assert rows[detached.as_posix()].detached and rows[detached.as_posix()].branch is None
    assert rows[missing.as_posix()].availability == "missing"
    assert rows[missing.as_posix()].prunable and rows[missing.as_posix()].files.tracked is None
    after = (git(dev, "rev-parse", "HEAD"), git(dev, "show-ref"),
             git(dev, "worktree", "list", "--porcelain"), (dev / ".git" / "index").read_bytes(),
             (Path(git(extra, "rev-parse", "--absolute-git-dir")) / "index").read_bytes())
    assert before == after


def test_replaced_registration_cannot_use_foreign_or_parent_repo(lab, tmp_path):
    root, dev, head = lab
    extra = dev / "inside"
    git(dev, "worktree", "add", "-qb", "feat", str(extra))
    shutil.rmtree(extra)
    extra.mkdir()
    snap = read_worktrees(root, "tool", RepoEvidence("trunk", head))
    row = next(r for r in snap.rows if not r.is_primary)
    assert row.availability == "replaced" and row.head_sha is None
    git(extra, "init", "-q", "-b", "foreign")
    commit(extra)
    row = next(r for r in read_worktrees(root, "tool", RepoEvidence("trunk", head)).rows if not r.is_primary)
    assert row.availability == "replaced" and row.files.tracked is None and row.head_sha is None


def test_squash_receipt_requires_exact_actual_head_base_and_merge_reachability(lab, tmp_path):
    root, dev, base = lab
    extra = tmp_path / "feat"
    git(dev, "worktree", "add", "-qb", "feat", str(extra))
    feature = commit(extra, "new.txt", "feature")
    # 同じ秒・親・内容・メッセージだと同じSHAになる。時刻に頼らず別履歴を作る。
    merged = commit(dev, "new.txt", "feature", message="squashed feature")
    assert feature != merged
    git(dev, "update-ref", "refs/remotes/origin/trunk", merged)
    evidence = RepoEvidence("trunk", merged, [pr(feature, merge=merged)])
    row = next(r for r in read_worktrees(root, "tool", evidence).rows if not r.is_primary)
    assert row.integration.state == "integrated" and row.integration.pull.head_sha == feature
    evidence.pulls = [pr(feature, base="other", merge=merged)]
    row = next(r for r in read_worktrees(root, "tool", evidence).rows if not r.is_primary)
    assert row.integration.state == "unconfirmed" and row.integration.pull is None
    evidence.pulls = [pr(feature, merge=merged)]
    newer = commit(extra, "later.txt", "later")
    row = next(r for r in read_worktrees(root, "tool", evidence).rows if not r.is_primary)
    assert row.head_sha == newer and row.integration.state == "unconfirmed" and row.integration.pull is None
    git(extra, "reset", "--hard", feature)  # テストfixtureだけ。readerにreset経路はない。
    git(dev, "update-ref", "refs/remotes/origin/trunk", base)
    row = next(r for r in read_worktrees(root, "tool", evidence).rows if not r.is_primary)
    assert row.integration.state == "receipt_only" and row.integration.freshness == "stale"


def test_non_main_default_from_origin_head_and_unknown_without_it(lab):
    root, dev, head = lab
    row = read_worktrees(root, "tool").rows[0]
    assert row.integration.base_ref == "refs/remotes/origin/trunk"
    assert row.integration.state == "integrated" and row.integration.freshness == "cached"
    git(dev, "symbolic-ref", "--delete", "refs/remotes/origin/HEAD")
    row = read_worktrees(root, "tool").rows[0]
    assert row.integration.base_ref is None and row.integration.state == "unknown"


def test_nul_parser_preserves_newlines_rename_directory_summary_and_sample_limit():
    rows = parse_worktrees("worktree /root\0HEAD dead\0branch refs/heads/trunk\0\0worktree /extra\nline\0detached\0locked reason\nline\0prunable stale\0\0")
    assert rows[1].path == "/extra\nline" and rows[1].locked_reason == "reason\nline"
    raw = "R  renamed\0original\0 M odd\nname\0?? folder/\0!! .venv/\0"
    summary = parse_status(GitResult(("status",), 0, raw))
    assert (summary.tracked, summary.untracked, summary.ignored) == (2, 1, 1)
    assert summary.tracked_samples == ["renamed", "odd\nname"]
    summary = parse_status(GitResult(("status",), 0, "".join(f"?? p{i}\0" for i in range(30))))
    assert summary.untracked == 30 and len(summary.untracked_samples) == 20


@pytest.mark.parametrize("result,state", [
    (GitResult(("status",), 0, ""), "complete"),
    (GitResult(("status",), 0, "?? partial\0", "warning: unreadable folder"), "partial"),
    (GitResult(("status",), 128, "", "permission denied"), "unknown"),
    (GitResult(("status",), None, timeout=True), "unknown"),
    (GitResult(("status",), 0, "R  incomplete\0"), "partial"),
])
def test_zero_null_warning_and_failures(result, state):
    summary = parse_status(result)
    assert summary.state == state
    assert summary.tracked == (0 if state == "complete" else None)
    if result.stderr.startswith("warning"):
        assert summary.untracked_samples == ["partial"] and summary.untracked is None


def test_runner_limits_retains_result_and_disables_locks(monkeypatch, tmp_path):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, b"output", b"warning")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setenv("GIT_DIR", "/injected")
    reader = Reader()
    result = reader.git(tmp_path, "status", "-z")
    assert result.stdout == "output" and result.stderr == "warning" and result.returncode == 0
    args, kwargs = calls[0]
    assert "--no-optional-locks" in args and kwargs["timeout"] <= 3
    assert kwargs["env"]["GIT_OPTIONAL_LOCKS"] == "0" and "GIT_DIR" not in kwargs["env"]
    assert reader.results == [result]
    assert Reader(time.monotonic()-1).git(tmp_path, "status").timeout
    assert len(calls) == 1


def test_unreadable_row_is_retained_and_ancestor_errors_do_not_confirm(monkeypatch, tmp_path):
    reader = Reader()
    monkeypatch.setattr(reader, "git", lambda path, *args: GitResult(args, 128, stderr="permission denied"))
    row = _scan_row(reader, WorktreeRow(str(tmp_path)), "/common", "ref", "base", RepoEvidence())
    assert row.availability == "unreadable" and row.files.tracked is None and row.head_sha is None
    result = integration(reader, tmp_path, "head", "feat", "ref", "base", RepoEvidence("trunk"))
    assert result.state == "unknown"


def test_total_deadline_keeps_registration_rows_and_null_metrics(lab, monkeypatch):
    root, dev, head = lab
    def slow(self, path, *args):
        if args[0] == "status":
            time.sleep(.12)
            return GitResult(args, timeout=True)
        if args[0] == "worktree":
            return GitResult(args, 0, f"worktree {dev}\0branch refs/heads/trunk\0\0")
        if "--show-toplevel" in args:
            return GitResult(args, 0, f"{dev}\n{dev / '.git'}\n")
        if args[0] == "symbolic-ref":
            return GitResult(args, 0, "refs/heads/trunk")
        return GitResult(args, 0, head)
    monkeypatch.setattr(Reader, "git", slow)
    start = time.monotonic()
    snapshot = read_worktrees(root, "tool", RepoEvidence("trunk", head), deadline=start+.04)
    assert len(snapshot.rows) == 1 and snapshot.state == "partial"
    assert snapshot.rows[0].files.tracked is None
    assert time.monotonic()-start < .5


@pytest.mark.parametrize("name", [".", "..", "../tool", "a/b", "a\\b", "C:tool", "//server/share", "\\\\server\\share"])
def test_slug_path_injection_rejected(tmp_path, name):
    with pytest.raises(ValueError):
        repo_path(tmp_path, name)


def test_demo_never_reads_real_fs_or_git(settings, source, monkeypatch):
    monkeypatch.setattr(Reader, "git", lambda *a: pytest.fail("demo Git access"))
    monkeypatch.setattr("app.service.read_worktrees", lambda *a: pytest.fail("demo FS scan"))
    snapshot, url = asyncio.run(Service(settings, source).worktrees("event-calendar"))
    assert len(snapshot.rows) == 6 and url
    assert {r.integration.state for r in snapshot.rows} == {"integrated", "receipt_only", "unconfirmed", "unknown"}


def test_github_outage_keeps_local_rows(settings, lab):
    root, dev, head = lab
    class Outage:
        async def repo_view(self, name):
            raise RuntimeError("offline")
    snapshot, url = asyncio.run(Service(replace(settings, github_token="token", lab_git_dir=root), Outage()).worktrees("tool"))
    assert snapshot.rows[0].head_sha == head and url is None
    assert snapshot.rows[0].integration.base_ref.endswith("/trunk")
    assert "GitHub" in snapshot.notes[0]


def test_reader_only_invokes_readonly_git(lab, monkeypatch):
    root, _, _ = lab
    original = Reader.git
    commands = []
    def record(self, path, *args):
        commands.append(args)
        return original(self, path, *args)
    monkeypatch.setattr(Reader, "git", record)
    read_worktrees(root, "tool")
    assert commands and {a[0] for a in commands} <= {"rev-parse", "worktree", "symbolic-ref", "status", "merge-base"}
    assert all(a[1] == "list" for a in commands if a[0] == "worktree")
    assert all("--delete" not in a for a in commands)


def test_worktree_get_auth_404_demo_html_and_no_public_json(client, settings, source):
    anon = TestClient(create_app(settings, source))
    assert anon.get("/repo/event-calendar/worktrees").status_code == 401
    assert client.get("/repo/nope/worktrees").status_code == 404
    response = client.get("/repo/event-calendar/worktrees")
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    assert "主作業場所" in response.text and "判定不明" in response.text
    assert client.get("/api/worktrees").status_code == 404


def test_route_github_outage_valid_local_and_invalid_slug(settings, lab):
    root, dev, head = lab
    class Outage:
        async def repo_names(self):
            raise RuntimeError("offline")
        async def repo_view(self, name):
            raise RuntimeError("offline")
    app = create_app(replace(settings, github_token="token", lab_git_dir=root), Outage())
    client = TestClient(app, headers=AUTH)
    response = client.get("/repo/tool/worktrees")
    assert response.status_code == 200 and head[:7] in response.text
    assert client.get("/repo/nope/worktrees").status_code == 404
    assert client.get("/repo/C:tool/worktrees").status_code == 404


def test_circuit_and_dashboard_never_scan_worktree_files(client, monkeypatch):
    monkeypatch.setattr("app.service.read_worktrees", lambda *a: pytest.fail("unexpected scan"))
    assert client.get("/").status_code == 200
    assert client.get("/repo/event-calendar").status_code == 200


def test_worker_concurrency_is_at_most_four_and_all_rows_retained(tmp_path, monkeypatch):
    source = tmp_path / "tool"
    paths = [source] + [tmp_path / f"extra{i}" for i in range(8)]
    def fake_git(self, path, *args):
        if args[0] == "worktree":
            return GitResult(args, 0, "".join(f"worktree {p}\0branch refs/heads/trunk\0\0" for p in paths))
        if "--show-toplevel" in args:
            return GitResult(args, 0, f"{source}\n{tmp_path / 'common'}\n")
        return GitResult(args, 0, "f"*40)
    active, peak = 0, 0
    lock = Lock()
    def scan(reader, row, *args):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(.03)
        row.availability = "available"
        row.files = FileSummary(0, 0, 0, state="complete")
        with lock:
            active -= 1
        return row
    monkeypatch.setattr(Reader, "git", fake_git)
    monkeypatch.setattr("app.worktrees._scan_row", scan)
    snapshot = read_worktrees(tmp_path, "tool", RepoEvidence("trunk"))
    assert len(snapshot.rows) == 9 and 1 < peak <= 4


def test_timeout_captures_partial_stdout_and_stderr(monkeypatch, tmp_path):
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("git", 3, output=b"partial", stderr=b"read warning")
    monkeypatch.setattr(subprocess, "run", timeout)
    result = Reader().git(tmp_path, "status", "-z")
    assert result.timeout and result.returncode is None and result.stdout == "partial"
    assert "read warning" in result.stderr


def test_github_existing_cache_preserves_default_and_receipt_without_network():
    source = GitHubSource("token", "owner", ttl=5)
    root = "/repos/owner/tool"
    source._cache[root+"?"] = (0, {"default_branch": "release", "html_url": "https://github.com/owner/tool"})
    source._cache[root+"/branches/release?"] = (0, {"commit": {"sha": "f"*40}})
    source._cache[root+"/pulls?per_page=30&state=closed"] = (0, [{
        "number": 1, "title": "merged", "state": "closed", "merged_at": "date", "draft": False,
        "head": {"ref": "feat", "sha": "a"*40}, "base": {"ref": "release", "sha": "c"*40},
        "merge_commit_sha": "b"*40,
    }])
    evidence = source.cached_worktree_evidence("tool")
    assert evidence.default_branch == "release" and evidence.head_sha == "f"*40
    assert evidence.freshness == "stale" and evidence.pulls[0].head_sha == "a"*40
    assert evidence.pulls[0].merge_commit_sha == "b"*40
    assert evidence.pulls[0].base_sha == "c"*40
    asyncio.run(source._client.aclose())


def test_worktree_html_escapes_paths_and_demo_route_never_resolves_local_root(settings, source, monkeypatch):
    original = source.worktree_snapshot
    def unsafe(name):
        snapshot = original(name)
        snapshot.rows[1].path = '<script>alert("path")</script>'
        snapshot.rows[1].files.untracked_samples = ['<script>alert("file")</script>']
        return snapshot
    monkeypatch.setattr(source, "worktree_snapshot", unsafe)
    monkeypatch.setattr("app.main.repo_path", lambda *a: pytest.fail("demo local root resolution"))
    client = TestClient(create_app(settings, source), headers=AUTH)
    html = client.get("/repo/event-calendar/worktrees").text
    assert '<script>alert("path")</script>' not in html and '&lt;script&gt;' in html
    assert '<script>alert("file")</script>' not in html


def test_timeout_keeps_obtained_head_and_files_without_late_mutation(tmp_path, monkeypatch):
    source = tmp_path / "tool"
    def fake_git(self, path, *args):
        if args[0] == "worktree":
            return GitResult(args, 0, f"worktree {source}\0branch refs/heads/trunk\0\0")
        if "--show-toplevel" in args:
            return GitResult(args, 0, f"{source}\n{tmp_path / 'common'}\n")
        return GitResult(args, 0, "f"*40)
    def scan(reader, row, *args):
        row.availability = "available"
        row.head_sha = "a"*40
        row.files = FileSummary(2, 3, 4, state="complete")
        reader.publish(row)
        time.sleep(.12)
        row.files.tracked = 999
        return row
    monkeypatch.setattr(Reader, "git", fake_git)
    monkeypatch.setattr("app.worktrees._scan_row", scan)
    snapshot = read_worktrees(tmp_path, "tool", RepoEvidence("trunk"), deadline=time.monotonic()+.04)
    assert snapshot.rows[0].head_sha == "a"*40 and snapshot.rows[0].files.tracked == 2
    assert snapshot.rows[0].integration.state == "unknown" and snapshot.state == "partial"
    time.sleep(.13)
    assert snapshot.rows[0].files.tracked == 2


def test_incomplete_worktree_record_never_becomes_complete_row():
    raw = "worktree /valid\0branch refs/heads/trunk\0\0worktree /truncated"
    assert [row.path for row in parse_worktrees(raw)] == ["/valid"]


def test_invalid_local_git_marker_does_not_bypass_route_validation(settings, tmp_path):
    root = tmp_path / "git"
    dev = root / "bogus"
    dev.mkdir(parents=True)
    (dev / ".git").write_text("not a git repo")
    class Outage:
        async def repo_names(self):
            raise RuntimeError("offline")
        async def repo_view(self, name):
            raise RuntimeError("offline")
    client = TestClient(create_app(replace(settings, github_token="token", lab_git_dir=root), Outage()), headers=AUTH)
    assert client.get("/repo/bogus/worktrees").status_code == 404


def test_worker_failure_keeps_acquired_values(lab, monkeypatch):
    root, _, head = lab
    def failed_scan(reader, row, *args):
        row.availability = "available"
        row.head_sha = head
        row.files = FileSummary(0, 1, 2, state="complete")
        reader.publish(row)
        raise OSError("fixture read failed")
    monkeypatch.setattr("app.worktrees._scan_row", failed_scan)
    snapshot = read_worktrees(root, "tool", RepoEvidence("trunk"))
    assert snapshot.state == "partial" and snapshot.rows[0].head_sha == head
    assert snapshot.rows[0].files.untracked == 1 and snapshot.rows[0].files.ignored == 2
    assert "fixture read failed" in snapshot.rows[0].notes[0]
