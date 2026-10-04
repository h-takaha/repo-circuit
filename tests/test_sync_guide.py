import asyncio
import shutil
import subprocess

import pytest

from app.commands import fast_forward_local
from app.demo import DemoSource
from app.explain import parts


def test_guide_is_terminal_only_and_requires_existing_clean_workbench(client):
    view = asyncio.run(DemoSource().repo_view("event-calendar"))
    view.local.dev_uncommitted = 0
    guide = parts(view)["sync"]
    assert len(guide["steps"]) == 3
    assert all(action["terminal"] and "action" not in action
               for step in guide["steps"] for action in step["actions"])
    view.local.dev_uncommitted = 1
    guide = parts(view)["sync"]
    assert "未コミット" in guide["body"]
    assert all(not step["actions"] for step in guide["steps"])
    view.local.dev_exists = False
    assert "sync" not in parts(view)
    assert 'data-part="sync"' in client.get("/repo/event-calendar").text


def test_invalid_default_branch_never_generates_update_command():
    assert fast_forward_local("C:/repo", "main;calc") == ""


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / "作業台 with spaces"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-b", "trunk")
    git("config", "core.hooksPath", str(tmp_path / "no-hooks"))
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (repo / "file.txt").write_text("base\n")
    git("add", "file.txt")
    git("commit", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("switch", "-c", "future")
    (repo / "file.txt").write_text("merged fix\n")
    git("add", "file.txt")
    git("commit", "-m", "fix")
    target = git("rev-parse", "HEAD")
    git("switch", "trunk")
    git("update-ref", "refs/remotes/origin/trunk", target)
    return repo, git, base, target


def run_copy(repo):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell is required to verify copied command")
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-Command",
                           fast_forward_local(str(repo), "trunk")],
                          capture_output=True, text=True, encoding="utf-8", errors="replace")


def test_copy_updates_only_clean_default_branch_without_new_commit(history):
    repo, git, _, target = history
    result = run_copy(repo)
    assert result.returncode == 0, result.stderr
    assert git("rev-parse", "HEAD") == target
    assert git("rev-list", "--count", "HEAD") == "2"
    assert (repo / "file.txt").read_text() == "merged fix\n"


@pytest.mark.parametrize("changed_after_copy", ["branch", "dirty", "diverged", "ahead", "missing_origin"])
def test_copy_keeps_work_when_context_changed(history, changed_after_copy):
    repo, git, base, target = history
    if changed_after_copy == "branch":
        git("switch", "-c", "still-working")
    elif changed_after_copy == "dirty":
        (repo / "file.txt").write_text("unfinished work\n")
    elif changed_after_copy == "diverged":
        (repo / "local.txt").write_text("local work\n")
        git("add", "local.txt")
        git("commit", "-m", "local work")
    elif changed_after_copy == "ahead":
        git("merge", "--ff-only", "future")
        git("update-ref", "refs/remotes/origin/trunk", base)
    else:
        git("update-ref", "-d", "refs/remotes/origin/trunk")
    before = git("rev-parse", "HEAD")
    file_before = (repo / "file.txt").read_text()
    run_copy(repo)
    assert git("rev-parse", "HEAD") == before
    assert (repo / "file.txt").read_text() == file_before
    if changed_after_copy == "branch":
        assert git("branch", "--show-current") == "still-working"
    elif changed_after_copy == "diverged":
        assert (repo / "local.txt").read_text() == "local work\n"
