"""GitHub とローカル git から集めた状態。画面・ガード・配線図はすべてこれを読む。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Commit:
    sha: str
    message: str
    date: datetime

    @property
    def short(self) -> str:
        return self.sha[:7]

    @property
    def headline(self) -> str:
        return self.message.splitlines()[0] if self.message else ""


@dataclass
class Issue:
    repo: str
    number: int
    title: str
    body: str
    state: str  # open / closed
    labels: list[str]
    created_at: datetime
    url: str = ""

    @property
    def kind(self) -> str:
        for k in ("bug", "improvement", "idea"):
            if k in self.labels:
                return k
        return "improvement"

    @property
    def priority(self) -> str:
        for p in ("p1", "p2", "p3"):
            if p in self.labels:
                return p
        return "p2"


@dataclass
class PullRequest:
    number: int
    title: str
    body: str
    head: str
    base: str
    state: str  # open / closed
    merged: bool
    draft: bool
    head_sha: str
    ci: str  # success / failure / pending / none
    mergeable_state: str  # clean / dirty / blocked / unknown ...
    url: str = ""


@dataclass
class Branch:
    name: str
    sha: str
    ahead: int
    behind: int
    commits: list[Commit]  # 幹線に無いはんだ点（古い順）
    merge_base: str
    last_date: datetime

    @property
    def idle_days(self) -> int:
        return max(0, (now() - self.last_date).days)


@dataclass
class LocalState:
    """作業台（開発 repo）と本番（稼働クローン）の位置。読み取り専用で集める。"""

    dev_exists: bool = False
    dev_head: str = ""
    dev_unpushed: int = 0
    run_exists: bool = False
    run_head: str = ""
    run_dirty: bool = False


@dataclass
class RepoView:
    name: str
    default_branch: str
    main_commits: list[Commit]  # 古い順
    branches: list[Branch]
    pulls: list[PullRequest]
    issues: list[Issue]
    closed_pulls: list[PullRequest] = field(default_factory=list)
    local: LocalState = field(default_factory=LocalState)
    autodeploy: bool = False
    url: str = ""

    def pull_for(self, branch: str) -> PullRequest | None:
        for pr in self.pulls:
            if pr.head == branch and pr.state == "open":
                return pr
        return None

    def branch(self, name: str) -> Branch | None:
        for b in self.branches:
            if b.name == name:
                return b
        return None

    def issue(self, number: int) -> Issue | None:
        for i in self.issues:
            if i.number == number:
                return i
        return None

    @property
    def head_sha(self) -> str:
        return self.main_commits[-1].sha if self.main_commits else ""

    @property
    def run_behind(self) -> int | None:
        """本番マーカーが幹線の先端からいくつ遅れているか。窓の外なら None。"""
        if not self.local.run_head:
            return None
        shas = [c.sha for c in self.main_commits]
        for i, sha in enumerate(shas):
            if sha.startswith(self.local.run_head) or self.local.run_head.startswith(sha):
                return len(shas) - 1 - i
        return None
