"""付箋（Issue）のラベル・本文テンプレ・状態判定。"""

from __future__ import annotations

import re

from .model import Issue, PullRequest, RepoView

FEEDBACK_LABEL = "feedback"
KINDS = {"bug": "不具合", "improvement": "改善", "idea": "アイデア"}
PRIORITIES = {"p1": "急ぎ", "p2": "ふつう", "p3": "いつか"}
LABEL_COLORS = {
    FEEDBACK_LABEL: "a4561a",
    "bug": "c23a2b",
    "improvement": "2f6db5",
    "idea": "c79100",
    "p1": "9e2a1d",
    "p2": "8c95a1",
    "p3": "c9c1b2",
}
SOURCES = ("web", "claude", "codex", "github")

# GitHub が自動クローズに使うキーワード（大文字小文字を問わない）
_FIXES = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)", re.IGNORECASE)
_BRANCH_ISSUE = re.compile(r"^(?:fix|feat|issue)/(\d+)(?:-|$)")


def labels_for(kind: str, priority: str) -> list[str]:
    kind = kind if kind in KINDS else "improvement"
    priority = priority if priority in PRIORITIES else "p2"
    return [FEEDBACK_LABEL, kind, priority]


def build_body(situation: str, want: str = "", note: str = "", source: str = "web") -> str:
    source = source if source in SOURCES else "web"
    parts = ["## 気づいた状況", situation.strip() or "（未記入）"]
    if want.strip():
        parts += ["", "## どうなってほしいか", want.strip()]
    if note.strip():
        parts += ["", "## 補足", note.strip()]
    parts += ["", f"<!-- repo-circuit source={source} -->", f"_起票元: {source}_"]
    return "\n".join(parts)


def fixes_numbers(text: str) -> set[int]:
    return {int(n) for n in _FIXES.findall(text or "")}


def branch_issue_number(branch: str) -> int | None:
    m = _BRANCH_ISSUE.match(branch)
    return int(m.group(1)) if m else None


def linked_issue(pr: PullRequest) -> int | None:
    nums = sorted(fixes_numbers(pr.body))
    if nums:
        return nums[0]
    return branch_issue_number(pr.head)


def branch_linked_issue(view: RepoView, branch: str) -> int | None:
    pr = view.pull_for(branch)
    if pr:
        n = linked_issue(pr)
        if n:
            return n
    return branch_issue_number(branch)


def slugify(title: str, limit: int = 24) -> str:
    ascii_part = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return (ascii_part[:limit].strip("-")) or "fix"


def branch_name_for(issue: Issue) -> str:
    return f"fix/{issue.number}-{slugify(issue.title)}"


def board_state(view: RepoView, issue: Issue) -> str:
    """unwired（未接続）/ wired（配線中）/ done（完了）"""
    if issue.state == "closed":
        return "done"
    for pr in view.pulls:
        if pr.state == "open" and linked_issue(pr) == issue.number:
            return "wired"
    for b in view.branches:
        if branch_issue_number(b.name) == issue.number:
            return "wired"
    return "unwired"


def wire_for(view: RepoView, issue: Issue) -> tuple[str, PullRequest | None]:
    """付箋がどの分岐・スイッチにつながっているか。"""
    for pr in view.pulls:
        if pr.state == "open" and linked_issue(pr) == issue.number:
            return pr.head, pr
    for b in view.branches:
        if branch_issue_number(b.name) == issue.number:
            return b.name, None
    return "", None


def merged_fix_for(view: RepoView, number: int) -> PullRequest | None:
    for pr in view.closed_pulls:
        if pr.merged and linked_issue(pr) == number:
            return pr
    return None
