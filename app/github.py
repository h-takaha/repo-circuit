"""GitHub REST API の読み書き。書き込みは actions.py からだけ呼ぶ。"""

from __future__ import annotations

import asyncio
import time
from urllib.parse import quote
from datetime import datetime
from typing import Any

import httpx

from .model import Branch, Commit, Issue, PullRequest, RepoView, now
from .issues import FEEDBACK_LABEL, LABEL_COLORS

API = "https://api.github.com"
MAX_BRANCHES = 8
MAIN_WINDOW = 8


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"GitHub {status}: {message}")
        self.status = status
        self.message = message


def _dt(value: str | None) -> datetime:
    if not value:
        return now()
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _commit(raw: dict) -> Commit:
    c = raw.get("commit", {})
    date = (c.get("committer") or c.get("author") or {}).get("date")
    return Commit(sha=raw["sha"], message=c.get("message", ""), date=_dt(date))


def _ci_state(check_runs: list[dict]) -> str:
    if not check_runs:
        return "none"
    if any(r.get("status") != "completed" for r in check_runs):
        return "pending"
    bad = {"failure", "timed_out", "cancelled", "action_required", "startup_failure"}
    if any(r.get("conclusion") in bad for r in check_runs):
        return "failure"
    return "success"


def _actions_state(runs: list[dict]) -> str:
    """ワークフローごとに最新の実行だけを見る（やり直し前の失敗を数えない）。"""
    latest: dict = {}
    for run in runs:
        key = run.get("workflow_id") or run.get("name")
        if key not in latest or run.get("run_number", 0) > latest[key].get("run_number", 0) or (
            run.get("run_number") == latest[key].get("run_number") and run.get("run_attempt", 1) > latest[key].get("run_attempt", 1)
        ):
            latest[key] = run
    return _ci_state(list(latest.values()))


class GitHubSource:
    def __init__(self, token: str, owner: str, ttl: float = 60, transport: httpx.AsyncBaseTransport | None = None):
        self.owner = owner
        self.ttl = ttl
        self._cache: dict[str, tuple[float, Any]] = {}
        self._client = httpx.AsyncClient(
            base_url=API,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=15,
            transport=transport,
        )

    # ---- 低レベル ----
    async def _req(self, method: str, path: str, **kw) -> Any:
        r = await self._client.request(method, path, **kw)
        if r.status_code >= 400:
            try:
                msg = r.json().get("message", r.text)
            except ValueError:
                msg = r.text
            raise GitHubError(r.status_code, msg)
        if r.status_code == 204 or not r.content:
            return None
        return r.json()

    async def _get(self, path: str, **params) -> Any:
        key = path + "?" + "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.ttl:
            return hit[1]
        data = await self._req("GET", path, params=params)
        self._cache[key] = (time.monotonic(), data)
        return data

    async def _read(self, path: str, *, fresh: bool = False, **params) -> Any:
        if fresh:
            return await self._req("GET", path, params=params)
        return await self._get(path, **params)

    def invalidate(self, repo: str | None = None) -> None:
        if repo is None:
            self._cache.clear()
            return
        marker = f"/repos/{self.owner}/{repo}"
        for k in [k for k in self._cache if k.startswith(marker) or k.startswith("/user/repos")]:
            del self._cache[k]

    def _r(self, repo: str) -> str:
        return f"/repos/{self.owner}/{repo}"

    # ---- 読み取り ----
    async def repo_names(self) -> list[str]:
        repos = await self._get("/user/repos", affiliation="owner", per_page=100, sort="pushed")
        return [r["name"] for r in repos if not r.get("archived") and r["owner"]["login"] == self.owner]

    async def repo_view(self, name: str) -> RepoView:
        r = self._r(name)
        meta = await self._get(r)
        default = meta["default_branch"]
        main_raw, branches_raw, pulls_raw, issues_raw, closed_raw = await asyncio.gather(
            self._get(f"{r}/commits", sha=default, per_page=MAIN_WINDOW),
            self._get(f"{r}/branches", per_page=100),
            self._get(f"{r}/pulls", state="open", per_page=50),
            self._get(f"{r}/issues", state="all", labels=FEEDBACK_LABEL, per_page=100),
            self._get(f"{r}/pulls", state="closed", per_page=30),
        )
        main_commits = [_commit(c) for c in reversed(main_raw)]
        others = [b for b in branches_raw if b["name"] != default][:MAX_BRANCHES]
        branches = await asyncio.gather(*(self._branch(r, default, b["name"]) for b in others))
        # compare のコミット一覧は behind 時に空になる。先端の正本は /branches。
        for branch, raw in zip(branches, others):
            branch.sha = raw.get("commit", {}).get("sha") or branch.sha
        pulls = await asyncio.gather(*(self._pull(r, p) for p in pulls_raw))
        closed = [self._pull_basic(p, "none", "unknown") for p in closed_raw]
        issues = [
            Issue(
                repo=name,
                number=i["number"],
                title=i["title"],
                body=i.get("body") or "",
                state=i["state"],
                labels=[lb["name"] for lb in i.get("labels", [])],
                created_at=_dt(i.get("created_at")),
                url=i.get("html_url", ""),
            )
            for i in issues_raw
            if "pull_request" not in i
        ]
        return RepoView(
            name=name,
            default_branch=default,
            main_commits=main_commits,
            branches=list(branches),
            pulls=list(pulls),
            issues=issues,
            closed_pulls=closed,
            github_names=[b["name"] for b in branches_raw] if len(branches_raw) < 100 else None,
            url=meta.get("html_url", ""),
        )

    async def worktree_evidence(self, name: str):
        """worktree画面に必要な証拠のみ。回路の分岐表示上限に依存しない。"""
        from .worktrees import RepoEvidence

        r = self._r(name)
        meta = await self._get(r)
        default = meta.get("default_branch")
        closed, branch = await asyncio.gather(
            self._get(f"{r}/pulls", state="closed", per_page=30),
            self._get(f"{r}/branches/{quote(default, safe='')}") if default else asyncio.sleep(0, result={}),
        )
        return RepoEvidence(default_branch=default,
            head_sha=branch.get("commit", {}).get("sha"),
            pulls=[self._pull_basic(p, "none", "unknown") for p in closed],
            source=f"GitHub（既存の最大{self.ttl:g}秒キャッシュを含む）", freshness="cached",
            repo_url=meta.get("html_url"))

    def cached_worktree_evidence(self, name: str):
        """障害時は既存cacheのみ。再送・fetch・disk保存はしない。"""
        from .worktrees import RepoEvidence

        r = self._r(name)
        meta = self._cache.get(r + "?", (0, {}))[1]
        default = meta.get("default_branch")
        closed = self._cache.get(f"{r}/pulls?per_page=30&state=closed", (0, []))[1]
        branch = self._cache.get(f"{r}/branches/{quote(default, safe='')}?", (0, {}))[1] if default else {}
        return RepoEvidence(default_branch=default, head_sha=branch.get("commit", {}).get("sha"),
            pulls=[self._pull_basic(p, "none", "unknown") for p in closed],
            source="GitHubの既存cache（障害時）", freshness="stale", repo_url=meta.get("html_url"))

    async def _branch(self, r: str, default: str, name: str) -> Branch:
        cmp = await self._get(f"{r}/compare/{default}...{name}")
        commits = [_commit(c) for c in cmp.get("commits", [])]
        head = cmp.get("commits", [{}])[-1].get("sha", "") if commits else cmp.get("merge_base_commit", {}).get("sha", "")
        last = commits[-1].date if commits else _dt(cmp.get("merge_base_commit", {}).get("commit", {}).get("committer", {}).get("date"))
        return Branch(
            name=name,
            sha=head,
            ahead=cmp.get("ahead_by", 0),
            behind=cmp.get("behind_by", 0),
            commits=commits,
            merge_base=cmp.get("merge_base_commit", {}).get("sha", ""),
            last_date=last,
        )

    async def pull_for_merge(self, repo: str, number: int) -> PullRequest | None:
        """合流の検証用。PR詳細と、その先端の検査結果をキャッシュせず読む。"""
        r = self._r(repo)
        try:
            detail = await self._read(f"{r}/pulls/{number}", fresh=True)
        except GitHubError as e:
            if e.status == 404:
                return None
            raise
        return await self._pull(r, {"number": number}, fresh=True, detail=detail)

    async def _pull(self, r: str, p: dict, *, fresh: bool = False, detail: dict | None = None) -> PullRequest:
        if detail is None:
            detail = await self._read(f"{r}/pulls/{p['number']}", fresh=fresh)
        if detail["state"] != "open" or detail.get("merged_at"):
            return self._pull_basic(detail, "none", detail.get("mergeable_state") or "unknown")
        try:
            checks = await self._read(f"{r}/commits/{detail['head']['sha']}/check-runs", fresh=fresh, per_page=100)
        except GitHubError as e:
            if e.status != 403:
                raise
            # Fine-grained PAT は Checks API を読めない。Actions の実行結果で代わりに判定する
            ci = await self._actions_ci(r, detail["head"]["sha"], fresh=fresh)
        else:
            ci = _ci_state(checks.get("check_runs", []))
        return self._pull_basic(detail, ci, detail.get("mergeable_state") or "unknown")

    async def _actions_ci(self, r: str, sha: str, *, fresh: bool = False) -> str:
        """Checks API の代わりに GitHub Actions の実行結果から検査ランプを決める。Actions 以外の外部 CI は見えない。

        - 実行がある → その結果（合格 / 失敗 / 検査中）
        - ワークフローが1つも無い → 検査なし
        - ワークフローはあるのに、この sha の実行が無い（まだ作られていないかもしれない）→ 取得不可のまま
        """
        try:
            runs = (await self._read(f"{r}/actions/runs", fresh=fresh, head_sha=sha, per_page=50)).get("workflow_runs", [])
            if runs:
                return _actions_state(runs)
            workflows = await self._read(f"{r}/actions/workflows", fresh=fresh, per_page=1)
        except GitHubError as e:
            if e.status in (403, 404):
                return "unavailable"
            raise
        return "none" if not workflows.get("total_count") else "unavailable"

    @staticmethod
    def _pull_basic(p: dict, ci: str, mergeable_state: str) -> PullRequest:
        return PullRequest(
            number=p["number"],
            title=p["title"],
            body=p.get("body") or "",
            head=p["head"]["ref"],
            base=p["base"]["ref"],
            state=p["state"],
            merged=bool(p.get("merged_at")),
            draft=bool(p.get("draft")),
            head_sha=p["head"]["sha"],
            ci=ci,
            mergeable_state=mergeable_state,
            url=p.get("html_url", ""),
            merge_commit_sha=(p.get("merge_commit_sha") or "") if p.get("merged_at") else "",
            base_sha=p.get("base", {}).get("sha") or "",
        )

    # ---- 書き込み（actions.py からだけ呼ぶ）----
    async def ensure_labels(self, repo: str, labels: list[str]) -> None:
        r = self._r(repo)
        for name in labels:
            try:
                await self._req("GET", f"{r}/labels/{name}")
            except GitHubError as e:
                if e.status != 404:
                    raise
                await self._req("POST", f"{r}/labels", json={"name": name, "color": LABEL_COLORS.get(name, "8c95a1")})

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict:
        await self.ensure_labels(repo, labels)
        return await self._req("POST", f"{self._r(repo)}/issues", json={"title": title, "body": body, "labels": labels})

    async def set_issue_state(self, repo: str, number: int, state: str) -> dict:
        payload: dict[str, str] = {"state": state}
        if state == "closed":
            payload["state_reason"] = "completed"
        return await self._req("PATCH", f"{self._r(repo)}/issues/{number}", json=payload)

    async def comment(self, repo: str, number: int, body: str) -> dict:
        return await self._req("POST", f"{self._r(repo)}/issues/{number}/comments", json={"body": body})

    async def create_branch(self, repo: str, name: str, sha: str) -> dict:
        return await self._req("POST", f"{self._r(repo)}/git/refs", json={"ref": f"refs/heads/{name}", "sha": sha})

    async def delete_branch(self, repo: str, name: str) -> None:
        await self._req("DELETE", f"{self._r(repo)}/git/refs/heads/{name}")

    async def create_pr(self, repo: str, head: str, base: str, title: str, body: str) -> dict:
        return await self._req("POST", f"{self._r(repo)}/pulls", json={"title": title, "head": head, "base": base, "body": body})

    async def merge_pr(self, repo: str, number: int, expected_head_sha: str) -> dict:
        return await self._req("PUT", f"{self._r(repo)}/pulls/{number}/merge",
                               json={"merge_method": "merge", "sha": expected_head_sha})

    async def close_pr(self, repo: str, number: int) -> dict:
        return await self._req("PATCH", f"{self._r(repo)}/pulls/{number}", json={"state": "closed"})

    async def rerun_failed(self, repo: str, head_sha: str) -> int:
        runs = await self._req("GET", f"{self._r(repo)}/actions/runs", params={"head_sha": head_sha, "per_page": 20})
        count = 0
        for run in runs.get("workflow_runs", []):
            if run.get("conclusion") in {"failure", "timed_out", "cancelled"}:
                await self._req("POST", f"{self._r(repo)}/actions/runs/{run['id']}/rerun-failed-jobs")
                count += 1
        return count

    async def aclose(self) -> None:
        await self._client.aclose()
