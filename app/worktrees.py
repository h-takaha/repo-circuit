"""登録された作業場所を読み取る。Git とファイルシステムへの書込みは行わない。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import copy
import os
from pathlib import Path
import re
import subprocess
import time
from threading import Lock
from concurrent.futures import ThreadPoolExecutor, wait

from .model import PullRequest, now


@dataclass
class FileSummary:
    # 要約パス件数。directory は1件。未取得・失敗は None、完全な空結果だけ0。
    tracked: int | None = None
    untracked: int | None = None
    ignored: int | None = None
    tracked_samples: list[str] = field(default_factory=list)
    untracked_samples: list[str] = field(default_factory=list)
    ignored_samples: list[str] = field(default_factory=list)
    state: str = "unknown"  # complete / partial / unknown
    notes: list[str] = field(default_factory=list)


@dataclass
class Integration:
    state: str = "unknown"  # integrated / receipt_only / unconfirmed / unknown
    head_sha: str | None = None
    base_ref: str | None = None
    base_sha: str | None = None
    source: str = "未取得"
    freshness: str = "unknown"  # current / cached / stale / unknown / demo
    pull: PullRequest | None = None  # head_sha / base / merge_commit_sha / url を含む
    notes: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return {"integrated": "確認済み", "receipt_only": "合流記録あり・到達未確認",
                "unconfirmed": "取り込み未確認", "unknown": "判定不明"}[self.state]


@dataclass
class WorktreeRow:
    path: str
    is_primary: bool = False
    branch: str | None = None
    head_sha: str | None = None  # 登録情報でなく、検証した実HEAD
    detached: bool = False
    availability: str = "unreadable"  # available / missing / unreadable / replaced
    locked: bool = False
    locked_reason: str | None = None
    prunable: bool = False
    prunable_reason: str | None = None
    files: FileSummary = field(default_factory=FileSummary)
    integration: Integration = field(default_factory=Integration)
    guidance: list[str] = field(default_factory=list)
    manager_candidate: str | None = None
    manager_reason: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class WorktreeSnapshot:
    observed_at: datetime = field(default_factory=now)
    source_path: str = ""
    state: str = "unknown"  # complete / partial / unknown
    rows: list[WorktreeRow] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class RepoEvidence:
    default_branch: str | None = None
    head_sha: str | None = None
    pulls: list[PullRequest] = field(default_factory=list)
    source: str = "GitHub 未取得"
    freshness: str = "unknown"
    repo_url: str | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class GitResult:
    args: tuple[str, ...]
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    timeout: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timeout


class Reader:
    """各呼出し3秒・全体15秒。optional index更新も禁止する。"""
    def __init__(self, deadline: float | None = None):
        self.deadline = deadline if deadline is not None else time.monotonic() + 15
        self.results: list[GitResult] = []
        self._progress: dict[str, WorktreeRow] = {}
        self._progress_lock = Lock()

    def publish(self, row: WorktreeRow) -> None:
        with self._progress_lock:
            self._progress[row.path] = copy.deepcopy(row)

    def latest(self, row: WorktreeRow) -> WorktreeRow:
        with self._progress_lock:
            return copy.deepcopy(self._progress.get(row.path, row))

    def git(self, path: Path, *args: str) -> GitResult:
        result = GitResult(tuple(args))
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            result.timeout = True
            result.stderr = "一覧取得の総期限に達しました"
        else:
            env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
            env["GIT_OPTIONAL_LOCKS"] = "0"
            env["GIT_TERMINAL_PROMPT"] = "0"
            try:
                p = subprocess.run(["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(path), *args],
                                   capture_output=True, timeout=min(3, remaining), env=env)
                result.returncode = p.returncode
                result.stdout = p.stdout.decode("utf-8", errors="replace")
                result.stderr = p.stderr.decode("utf-8", errors="replace").strip()
            except subprocess.TimeoutExpired as exc:
                result.timeout = True
                result.stdout = (exc.stdout or b"").decode("utf-8", errors="replace")
                result.stderr = (exc.stderr or b"").decode("utf-8", errors="replace").strip()
                result.stderr = (result.stderr + " / " if result.stderr else "") + "Git読取りが期限切れです"
            except OSError as exc:
                result.stderr = str(exc)
        self.results.append(result)
        return result


def validate_repo_name(name: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", name) or name in {".", ".."}:
        raise ValueError("無効なrepo名です")


def repo_path(root: Path, name: str) -> Path:
    """ブラウザの入力はslugのみ。設定済みroot外への解決も許さない。"""
    validate_repo_name(name)
    root = root.resolve()
    path = root / name
    if path.resolve().parent != root:
        raise ValueError("設定された作業台の外を指しています")
    return path


def parse_worktrees(raw: str) -> list[WorktreeRow]:
    rows: list[WorktreeRow] = []
    current: WorktreeRow | None = None
    # 不完全な末尾はpath/HEADを捏造しない。完了した登録レコードだけ残す。
    boundary = raw.rfind("\0\0")
    raw = raw[:boundary + 2] if boundary >= 0 else ""
    for token in raw.split("\0"):
        key, _, value = token.partition(" ")
        if key == "worktree":
            current = WorktreeRow(path=value, is_primary=not rows)
            rows.append(current)
        elif current:
            if key == "branch":
                current.branch = value.removeprefix("refs/heads/")
            elif key == "detached":
                current.detached = True
            elif key == "locked":
                current.locked, current.locked_reason = True, value or None
            elif key == "prunable":
                current.prunable, current.prunable_reason = True, value or None
    return rows[:1] + sorted(rows[1:], key=lambda r: r.path.casefold())


def parse_status(result: GitResult) -> FileSummary:
    summary = FileSummary()
    if not result.ok:
        summary.notes.append(result.stderr or "ファイル一覧を取得できませんでした")
        return summary
    categories = {"tracked": [], "untracked": [], "ignored": []}
    tokens = result.stdout.split("\0")
    pos, malformed = 0, False
    while pos < len(tokens):
        token = tokens[pos]
        pos += 1
        if not token:
            continue
        if len(token) < 4 or token[2] != " ":
            malformed = True
            continue
        code, path = token[:2], token[3:]
        category = "untracked" if code == "??" else "ignored" if code == "!!" else "tracked"
        categories[category].append(path)
        if "R" in code or "C" in code:
            # -z のrenameは「現在のpath NUL 元のpath NUL」。元pathを別変更と数えない。
            if pos >= len(tokens) or not tokens[pos]:
                malformed = True
            pos += 1
    summary.state = "partial" if result.stderr or malformed else "complete"
    if result.stderr:
        summary.notes.append(result.stderr)
    if malformed:
        summary.notes.append("ファイル一覧の一部を解釈できませんでした")
    for category, paths in categories.items():
        # 警告時の部分値を完全な0と誤解させない。取得済み例は保持する。
        setattr(summary, category, len(paths) if summary.state == "complete" else None)
        setattr(summary, category + "_samples", paths[:20])
    return summary


def _identity(reader: Reader, path: Path) -> tuple[str | None, GitResult]:
    result = reader.git(path, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir")
    lines = result.stdout.splitlines()
    if not result.ok or len(lines) != 2:
        return None, result
    try:
        if Path(lines[0]).resolve() != path.resolve():
            return None, result
        return str(Path(lines[1]).resolve()), result
    except OSError:
        return None, result


def integration(reader: Reader, path: Path, head: str, branch: str | None,
                base_ref: str | None, base_sha: str | None, evidence: RepoEvidence) -> Integration:
    out = Integration(head_sha=head, base_ref=base_ref, base_sha=base_sha,
                      source="ローカルの既定ref / " + evidence.source, freshness=evidence.freshness)
    out.notes.append("fetchしていない手元のrefを確認しています。確認済みでも使用終了・削除可能を意味しません。")
    receipt = next((p for p in evidence.pulls if p.merged and p.state == "closed"
                    and p.base == evidence.default_branch and p.head_sha == head
                    and (branch is None or p.head == branch)), None)
    out.pull = receipt
    if base_sha:
        ancestor = reader.git(path, "merge-base", "--is-ancestor", head, base_sha)
        if ancestor.ok and not ancestor.stderr:
            out.state = "integrated"
            return out
        if ancestor.returncode == 1 and not ancestor.stderr:
            out.state = "unconfirmed"
        else:
            out.notes.append(ancestor.stderr or "祖先関係を取得できませんでした")
    if receipt:
        out.state = "receipt_only"
        if base_sha and receipt.merge_commit_sha:
            reached = reader.git(path, "merge-base", "--is-ancestor", receipt.merge_commit_sha, base_sha)
            if reached.ok and not reached.stderr:
                out.state = "integrated"
            else:
                out.notes.append(reached.stderr or "PRの合流SHAが既定refに届いたことを確認できませんでした")
    return out


def _guidance(row: WorktreeRow) -> None:
    lower = row.path.replace("\\", "/").lower()
    for marker, candidate in (("/.mulmoterminal/", "MulmoTerminal"), ("/.codex/", "Codex"), ("/.claude/", "Claude Code")):
        if marker in lower:
            row.manager_candidate = candidate
            row.manager_reason = f"pathに {marker} があるための推定です。使用状態やsession履歴は確認していません。"
            break
    row.guidance = ["管理元でこの作業場所の使用終了を確認してください。checkoutの存在だけでは使用状態は分かりません。",
                    "必要ファイルを退避し、復元できることを確認してください。ignoredにも設定・環境・生成物が残る場合があります。",
                    "管理元のworktree整理を使い、必要なら残った分岐を別途整理してください。登録pruneとフォルダ削除は別操作です。"]
    if row.is_primary:
        row.guidance.insert(0, "主作業場所です。既定分岐とは別の区分で、通常の整理対象とは判断しません。")
    if row.availability != "available":
        row.guidance.insert(0, "登録先の所在・読取りを確認できません。登録だけでファイルの不要を判断しないでください。")
    if row.locked:
        row.guidance.insert(0, "ロックされています。管理元でロックの理由を確認してください。")


def _scan_row(reader: Reader, row: WorktreeRow, common: str, base_ref: str | None,
              base_sha: str | None, evidence: RepoEvidence) -> WorktreeRow:
    path = Path(row.path)
    try:
        path.stat()
    except FileNotFoundError:
        row.availability = "missing"
    except OSError as exc:
        row.notes.append(str(exc))
    else:
        actual_common, check = _identity(reader, path)
        if not actual_common:
            row.availability = "replaced" if check.ok else "unreadable"
            row.notes.append(check.stderr or "登録先がそのpathをtoplevelとするrepoであることを確認できません")
        elif actual_common != common:
            row.availability = "replaced"
            row.notes.append("登録元と異なるgit-common-dirです")
        elif check.stderr:
            row.notes.append(check.stderr)
        else:
            row.availability = "available"
            reader.publish(row)
            head = reader.git(path, "rev-parse", "--verify", "HEAD")
            if head.ok and not head.stderr:
                row.head_sha = head.stdout.strip()
            else:
                row.notes.append(head.stderr or "実HEADを取得できませんでした")
            reader.publish(row)
            ref = reader.git(path, "symbolic-ref", "--quiet", "HEAD")
            if ref.ok and not ref.stderr:
                row.branch = ref.stdout.strip().removeprefix("refs/heads/")
                row.detached = False
            elif ref.returncode == 1 and not ref.stderr:
                row.branch, row.detached = None, True
            else:
                row.notes.append(ref.stderr or "分岐を取得できませんでした")
            reader.publish(row)
            row.files = parse_status(reader.git(path, "status", "--porcelain=v1", "-z", "--untracked-files=normal", "--ignored=matching"))
            reader.publish(row)
            if row.head_sha:
                row.integration = Integration(head_sha=row.head_sha, base_ref=base_ref, base_sha=base_sha,
                    source="ローカルの既定ref / " + evidence.source, freshness=evidence.freshness)
                reader.publish(row)
                row.integration = integration(reader, path, row.head_sha, row.branch, base_ref, base_sha, evidence)
    if row.availability != "available":
        row.files.notes.append("登録先を検証できないため、ファイル件数は未取得です")
    _guidance(row)
    reader.publish(row)
    return row


def read_worktrees(root: Path, name: str, evidence: RepoEvidence | None = None,
                   deadline: float | None = None) -> WorktreeSnapshot:
    source = repo_path(root, name)
    snapshot = WorktreeSnapshot(source_path=str(source))
    reader, evidence = Reader(deadline), evidence or RepoEvidence()
    snapshot.notes.extend(evidence.notes)
    common, check = _identity(reader, source)
    if not common:
        snapshot.notes.append(check.stderr or "設定済み作業台にrepoを確認できませんでした")
        return snapshot
    listed = reader.git(source, "worktree", "list", "--porcelain", "-z")
    snapshot.rows = parse_worktrees(listed.stdout)
    if listed.stdout and not listed.stdout.endswith("\0\0"):
        snapshot.notes.append("登録一覧の末尾が不完全です。取得済みの登録だけを表示します。")
    if not listed.ok or listed.stderr or check.stderr:
        snapshot.notes.append(listed.stderr or check.stderr or "登録一覧を取得できませんでした")
    if not snapshot.rows:
        return snapshot
    default = evidence.default_branch
    if not default:
        origin = reader.git(source, "symbolic-ref", "--quiet", "refs/remotes/origin/HEAD")
        if origin.ok and not origin.stderr and origin.stdout.strip().startswith("refs/remotes/origin/"):
            default = origin.stdout.strip().removeprefix("refs/remotes/origin/")
            evidence.default_branch = default
            evidence.source += " / ローカルorigin/HEAD"
            evidence.freshness = "cached"
        else:
            snapshot.notes.append("既定分岐を確認できません。mainとは仮定しません。")
    base_ref = f"refs/remotes/origin/{default}" if default else None
    base = reader.git(source, "rev-parse", "--verify", base_ref) if base_ref else None
    base_sha = base.stdout.strip() if base and base.ok and not base.stderr else None
    if base_ref and not base_sha:
        snapshot.notes.append("手元の既定refを取得できませんでした")
    if base_sha and evidence.head_sha and base_sha != evidence.head_sha:
        evidence.freshness = "stale"
        snapshot.notes.append("手元の既定refとGitHubの先端が異なります。fetchはしていません。")
    elif evidence.freshness == "unknown" and base_sha:
        evidence.freshness = "cached"
    pool = ThreadPoolExecutor(max_workers=4)
    futures = {pool.submit(_scan_row, reader, copy.deepcopy(row), common, base_ref, base_sha, evidence): row
               for row in snapshot.rows}
    done, pending = wait(futures, timeout=max(0, reader.deadline - time.monotonic()))
    for future in done:
        try:
            row = futures[future]
            snapshot.rows[snapshot.rows.index(row)] = future.result()
        except Exception as exc:
            original = futures[future]
            row = reader.latest(original)
            row.notes.append(f"読取り失敗: {exc}")
            _guidance(row)
            snapshot.rows[snapshot.rows.index(original)] = row
    for future in pending:
        future.cancel()
        original = futures[future]
        row = reader.latest(original)
        row.notes.append("一覧取得の総期限に達したため、一部は未取得です")
        if row.files.state == "unknown":
            row.files.notes.append("総期限に達したため、ファイル件数は未取得です")
        _guidance(row)
        snapshot.rows[snapshot.rows.index(original)] = row
    pool.shutdown(wait=False, cancel_futures=True)
    snapshot.state = "partial" if (snapshot.notes or pending or not listed.ok or
        any(r.availability != "available" or r.notes or r.files.state != "complete" for r in snapshot.rows)) else "complete"
    return snapshot
