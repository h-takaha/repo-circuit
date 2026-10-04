import asyncio
import base64
import shutil
import subprocess
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from conftest import AUTH
from test_sync_guide import history
from app.commands import publish_local
from app.demo import DemoSource
from app.localgit import read_publish_state
from app.main import create_app
from app.publish import guide, local_command


def snapshot(view, **values):
    return {"branch": view.local.dev_branch, "head": view.local.dev_head, "staged": [],
            "unstaged": [], "index": "", "conflicted": False, "error": "", **values}


def current_view():
    view = asyncio.run(DemoSource().repo_view("event-calendar"))
    view.local.dev_uncommitted = 0
    return view


def test_delivery_phases_follow_actual_state_and_existing_actions():
    view = current_view()
    state = snapshot(view)
    assert guide(view, {**state, "unstaged": ["app/a.py"]})["phase"] == "stage"
    assert guide(view, {**state, "staged": ["app/a.py"], "index": "index"})["phase"] == "commit"
    view.local.dev_unpushed = 1
    assert guide(view, state)["phase"] == "push"
    view.local.dev_unpushed = 0
    assert guide(view, state)["phase"] == "check"  # failing CI
    pr = view.pull_for(state["branch"])
    pr.ci = "success"
    result = guide(view, state)
    assert result["phase"] == "merge"
    assert result["actions"][0]["action"] == "merge_pr"
    assert result["actions"][0]["params"]["expected_head_sha"] == state["head"]
    view.pulls = []
    result = guide(view, state)
    assert result["phase"] == "pr"
    assert result["actions"][0]["action"] == "create_pr"
    pr.state, pr.merged, pr.merge_commit_sha = "closed", True, view.main_commits[-1].sha
    view.closed_pulls.append(pr)
    assert guide(view, state)["phase"] == "deploy"
    view.local.run_head = view.head_sha
    assert guide(view, state)["phase"] == "done"
    view.branch(state["branch"]).sha = "b" * 40
    assert guide(view, state)["phase"] == "unknown"


@pytest.mark.parametrize("problem", ["main", "detached", "conflict", "failed", "changed"])
def test_unsafe_or_unknown_state_never_offers_an_operation(problem):
    view = current_view()
    state = snapshot(view, unstaged=["app/a.py"])
    if problem == "main":
        state["branch"] = view.local.dev_branch = "main"
    elif problem == "detached":
        state["branch"] = view.local.dev_branch = ""
    elif problem == "conflict":
        state["conflicted"] = True
    elif problem == "failed":
        state["error"] = "read failed"
    else:
        state["head"] = "a" * 40
    assert not guide(view, state)["actions"]


def test_read_publish_state_is_readonly_and_tracks_partial_staging_and_rename(history):
    repo, git, _, _ = history
    git("switch", "-c", "fix/guide")
    (repo / "file.txt").write_text("staged\n")
    git("add", "file.txt")
    (repo / "file.txt").write_text("later edit\n")
    (repo / "日本語 with space.txt").write_text("new\n", encoding="utf-8")
    before = (repo / ".git/index").read_bytes()
    head = git("rev-parse", "HEAD")
    state = read_publish_state(repo)
    assert not state["error"] and not state["conflicted"]
    assert state["staged"] == ["file.txt"]
    assert set(state["unstaged"]) == {"file.txt", "日本語 with space.txt"}
    assert state["index"]
    assert (repo / ".git/index").read_bytes() == before
    assert git("rev-parse", "HEAD") == head
    git("restore", "file.txt")
    git("mv", "file.txt", "renamed.txt")
    state = read_publish_state(repo)
    assert "file.txt" in state["staged"] and "renamed.txt" in state["staged"]


def run_script(script, shell_name=None):
    shell = shutil.which(shell_name) if shell_name else shutil.which("pwsh") or shutil.which("powershell")
    if not shell:
        pytest.skip("PowerShell required for copied command verification")
    encoded = base64.b64encode(script.encode("utf-16le")).decode("ascii")
    return subprocess.run([shell, "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                          capture_output=True, encoding="utf-8", errors="replace")


def test_copied_stage_and_commit_record_only_selected_file(history):
    repo, git, _, _ = history
    git("switch", "-c", "fix/guide")
    # Quotes, dollar signs and brackets remain literal path characters in PowerShell and Git.
    chosen = "日本語 $name [one]'s.txt"
    (repo / chosen).write_text("selected\n", encoding="utf-8")
    (repo / "unrelated.txt").write_text("keep\n")
    head = git("rev-parse", "HEAD")
    assert run_script(publish_local(str(repo), "fix/guide", head, "stage", [chosen])).returncode == 0
    state = read_publish_state(repo)
    assert state["staged"] == [chosen] and state["unstaged"] == ["unrelated.txt"]
    cmd = publish_local(str(repo), "fix/guide", head, "commit", message="fix: $name 'quoted'", index=state["index"])
    result = run_script(cmd)
    assert result.returncode == 0, result.stderr
    assert git("log", "-1", "--format=%s") == "fix: $name 'quoted' [no-push] [no-deploy]"
    assert read_publish_state(repo)["unstaged"] == ["unrelated.txt"]
    assert (repo / "unrelated.txt").read_text() == "keep\n"


@pytest.mark.parametrize("shell_name", ["powershell", "pwsh"])
def test_copied_commit_survives_paste_normalization_with_multiple_files(history, shell_name):
    repo, git, _, _ = history
    git("switch", "-c", "fix/guide")
    git("config", "core.quotepath", "false")
    (repo / "日本語.txt").write_text("selected\n", encoding="utf-8")
    (repo / "file.txt").write_text("also selected\n")
    git("add", "--", "日本語.txt", "file.txt")
    state = read_publish_state(repo)
    assert "\t" in state["index"] and "\n" in state["index"]
    cmd = publish_local(str(repo), "fix/guide", state["head"], "commit", message="fix pasted command", index=state["index"])
    assert "\t" not in cmd
    pasted = cmd.replace("\t", "").replace("\n", "\r\n")
    result = run_script(pasted, shell_name)
    assert result.returncode == 0, result.stderr
    assert git("rev-list", "--count", "HEAD") == "2"
    assert not read_publish_state(repo)["staged"]


def test_partial_staging_allows_explicit_additional_selection_without_forcing_it():
    view = current_view()
    state = snapshot(view, staged=["file.txt"], unstaged=["file.txt", "unrelated.txt"], index="index")
    result = guide(view, state)
    assert result["phase"] == "commit"
    assert result["actions"][0]["workbench"] == "commit"
    assert result["actions"][1]["workbench"] == "stage"
    assert result["actions"][1]["files"] == state["unstaged"]


@pytest.mark.parametrize("changed", ["branch", "head", "index"])
def test_copied_commit_stops_when_context_changes(history, changed):
    repo, git, _, _ = history
    git("switch", "-c", "fix/guide")
    (repo / "file.txt").write_text("selected\n")
    git("add", "file.txt")
    state = read_publish_state(repo)
    cmd = publish_local(str(repo), "fix/guide", state["head"], "commit", message="fix", index=state["index"])
    if changed == "branch":
        git("switch", "-c", "fix/other")
    elif changed == "head":
        git("commit", "-m", "other work")
    else:
        (repo / "file.txt").write_text("different staged content\n")
        git("add", "file.txt")
    before = git("rev-parse", "HEAD")
    index = (repo / ".git/index").read_bytes()
    run_script(cmd)
    assert git("rev-parse", "HEAD") == before
    assert (repo / ".git/index").read_bytes() == index


def test_local_command_revalidates_files_index_and_position():
    view = current_view()
    state = snapshot(view, unstaged=["app/a.py"], staged=["tests/a.py"], index="raw signature")
    params = {"expected_branch": state["branch"], "expected_head": state["head"]}
    with pytest.raises(ValueError):
        local_command(view, state, {**params, "operation": "stage", "files": ["../secret"]})
    for changes in [{"expected_head": "b" * 40}, {"expected_index": "changed"}, {"expected_files": []}]:
        with pytest.raises(ValueError):
            local_command(view, state, {**params, "operation": "commit", "message": "fix",
                                       "expected_files": state["staged"], "expected_index": state["index"], **changes})


def test_guide_http_is_authenticated_csrf_protected_and_copy_does_not_advance(client, csrf, settings, source):
    assert TestClient(create_app(settings, source)).get("/repo/event-calendar/publish-guide").status_code == 401
    result = client.get("/repo/event-calendar/publish-guide").json()
    assert result["phase"] == "stage"
    params = result["actions"][0]["params"]
    body = {**params, "operation": "stage", "files": ["app/example.py"]}
    assert client.post("/workbench/command", json=body).status_code == 403
    response = client.post("/workbench/command", json=body, headers={"X-CSRF-Token": csrf})
    assert response.status_code == 200 and "add -- 'app/example.py'" in response.json()["cmd"]
    assert client.get("/repo/event-calendar/publish-guide").json()["phase"] == "stage"
    assert client.get("/repo/nope/publish-guide").status_code == 404
    assert client.post("/workbench/command", json={**body, "repo": "../secret"}, headers={"X-CSRF-Token": csrf}).status_code == 404
    html = client.get("/repo/event-calendar").text
    assert 'data-part="publish"' in html
    assert html.index('class="board"') < html.index('aria-label="工程表"')


def test_guide_http_reads_fresh_actual_index_without_mutating_it(settings, source, history, monkeypatch):
    repo, git, _, _ = history
    view = source.views["event-calendar"]
    git("switch", "-c", view.local.dev_branch)
    monkeypatch.setattr("app.main.repo_path", lambda root, name: repo)
    async def local_view(self, name, **kwargs):
        value = await source.repo_view(name)
        state = read_publish_state(repo)
        value.local.dev_head, value.local.dev_branch, value.local.dev_dir = state["head"], state["branch"], str(repo)
        return value
    monkeypatch.setattr("app.main.Service.view", local_view)
    client = TestClient(create_app(replace(settings, github_token="test-only"), source), headers=AUTH)
    import re
    csrf = re.search(r'name="csrf-token" content="([^"]+)"', client.get("/guide").text).group(1)
    (repo / "file.txt").write_text("selected\n")
    git("add", "file.txt")
    result = client.get("/repo/event-calendar/publish-guide").json()
    assert result["phase"] == "commit"
    params = result["actions"][0]["params"]
    index_before = (repo / ".git/index").read_bytes()
    body = {**params, "operation": "commit", "message": "fix", "expected_files": result["files"], "expected_index": result["index"]}
    assert client.post("/workbench/command", json=body, headers={"X-CSRF-Token": csrf}).status_code == 200
    assert (repo / ".git/index").read_bytes() == index_before
    (repo / "file.txt").write_text("changed selection\n")
    git("add", "file.txt")
    assert client.post("/workbench/command", json=body, headers={"X-CSRF-Token": csrf}).status_code == 400
