"""作業台（C:\\lab\\git\\<repo>）と本番（C:\\lab\\run\\<repo>）の位置を読む。

読み取り専用。fetch / pull / checkout など状態を変える git コマンドは一切呼ばない。
稼働クローンにはこのアプリから書き込まない（agent-ops の「run\\ は開かない」方針）。
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .model import Commit, LocalBranch, LocalState, MergedBranch

_TIMEOUT = 3


def _git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            # git の出力は UTF-8。Windows の既定（cp932）で読むと日本語のコミット見出しで失敗する
            encoding="utf-8",
            errors="replace",
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


MAX_LOCAL_BRANCHES = 10
MAX_MERGED_READ = 60
MAX_UNPUSHED_DOTS = 5


def read_local_state(
    lab_git: Path,
    lab_run: Path,
    repo: str,
    ignore_dirty: list[str] | None = None,
    remote_heads: dict[str, str] | None = None,
    default_branch: str = "main",
    detail: bool = True,
    github_names: set[str] | None = None,
    github_main: set[str] | None = None,
) -> LocalState:
    """remote_heads は GitHub 上の分岐名→先端 sha。fetch しないローカルの origin/* より新しいので、送電済みの基準にする。
    渡されなかった分岐は手元の origin/* を使う。

    detail=False（配電盤）は分岐ごとに読まず、未送電の合計を git 1回で数える。repo が多いと分岐ごとの読み取りが遅いため。

    github_main は GitHub の幹線の直近のコミットの sha。作業台の origin/* は fetch していないと古いので、
    「どの origin/* にも無い」コミットでも、GitHub の幹線にすでにあれば送電済みとして未送電から引く。"""
    known = frozenset(github_main or ())
    dev = lab_git / repo
    st = LocalState(dev_dir=str(dev))
    if (dev / ".git").exists():
        st.dev_exists = True
        st.origin_main = _git(dev, "rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{default_branch}") or ""
        st.dev_head = _git(dev, "rev-parse", "HEAD") or ""
        st.dev_branch, st.dev_uncommitted = _worktree(dev)
        if not detail:
            st.unpushed_total = _unpushed_total(dev, known)
            return _read_run(st, lab_run / repo, ignore_dirty)
        # GitHub から受け取るのは表示する分岐（最大 8 本）だけなので、足りない分は手元の origin/* で補う
        origin = _origin_heads(dev)
        heads = {**origin, **(remote_heads or {})}
        for b in _local_branches(dev, heads, default_branch, st.dev_branch, known):
            if b.name == st.dev_branch:
                st.dev_unpushed = b.unpushed
            if b.name != default_branch:
                st.branches.append(b)
        st.merged = _merged_leftovers(dev, default_branch, st.dev_branch, heads)
        if github_names is not None:  # GitHub の分岐名の全部が分かるときだけ。分からないのに「古い」とは言わない
            st.stale_origin = sorted(n for n in origin if n not in github_names)
    return _read_run(st, lab_run / repo, ignore_dirty)


def _read_run(st: LocalState, run: Path, ignore_dirty: list[str] | None) -> LocalState:
    if (run / ".git").exists():
        st.run_exists = True
        st.run_head = _git(run, "rev-parse", "HEAD") or ""
        porcelain = _git(run, "--no-optional-locks", "status", "--porcelain") or ""
        changed = [line[3:] for line in porcelain.splitlines() if len(line) > 3]
        st.run_dirty = any(not _ignored(p, ignore_dirty or []) for p in changed)
    return st


def _worktree(dev: Path) -> tuple[str, int]:
    """今いる分岐と、まだコミットしていないファイルの数。--no-optional-locks で index を書き換えない。"""
    out = _git(dev, "--no-optional-locks", "status", "--porcelain=v1", "-b")
    if out is None:
        return "", 0
    lines = out.splitlines()
    branch = ""
    if lines and lines[0].startswith("## "):
        head = lines[0][3:]
        if head.startswith("No commits yet on "):
            branch = head.removeprefix("No commits yet on ").strip()
        elif not head.startswith("HEAD (no branch)"):
            branch = head.split("...")[0].split(" ")[0]
        lines = lines[1:]
    return branch, len([ln for ln in lines if ln.strip()])


def _origin_heads(dev: Path) -> dict[str, str]:
    out = _git(dev, "for-each-ref", "refs/remotes/origin", "--format=%(refname:lstrip=3)%00%(objectname)") or ""
    return dict(line.split("\0", 1) for line in out.splitlines() if "\0" in line and not line.startswith("HEAD\0"))


def _local_branches(dev: Path, heads: dict[str, str], default: str, current: str, known: frozenset[str] = frozenset()) -> list[LocalBranch]:
    fmt = "--format=%(refname:short)%00%(objectname)"
    base = f"origin/{default}"
    # 幹線に入りきった分岐は工程が終わっているので読まない（今いる分岐と既定分岐は必ず読む）
    out = _git(dev, "for-each-ref", "refs/heads", "--sort=-committerdate", f"--no-merged={base}", fmt)
    if out is None:
        out = _git(dev, "for-each-ref", "refs/heads", "--sort=-committerdate", fmt) or ""
    refs = [line.split("\0", 1) for line in out.splitlines() if "\0" in line]
    names = [n for n, _ in refs][:MAX_LOCAL_BRANCHES]
    all_refs = dict(refs)
    for must in (current, default):
        if must and must not in all_refs:
            sha = _git(dev, "rev-parse", "--verify", "--quiet", f"refs/heads/{must}")
            if sha:
                all_refs[must] = sha
        if must in all_refs and must not in names:
            names.append(must)
    return [_local_branch(dev, n, all_refs[n], heads.get(n), base, known) for n in names]


MAX_UNPUSHED_LIST = 2000


def _unpushed_total(dev: Path, known: frozenset[str]) -> int:
    """どの origin/* にも無いはんだ点の合計から、GitHub の幹線にすでにあるものを引く（配電盤用の軽い読み取り）。"""
    if known:
        shas = (_git(dev, "rev-list", "--branches", "--not", "--remotes", f"--max-count={MAX_UNPUSHED_LIST}") or "").split()
        if len(shas) < MAX_UNPUSHED_LIST:
            return len([s for s in shas if s not in known])
    count = _git(dev, "rev-list", "--count", "--branches", "--not", "--remotes")
    return int(count) if count and count.isdigit() else 0


def _busy_branches(dev: Path) -> set[str]:
    """どこかの作業場所（worktree）で開かれている分岐。git は切り替えも削除も断る。"""
    out = _git(dev, "worktree", "list", "--porcelain") or ""
    prefix = "branch refs/heads/"
    return {line[len(prefix):] for line in out.splitlines() if line.startswith(prefix)}


def _merged_leftovers(dev: Path, default: str, current: str, heads: dict[str, str]) -> list[MergedBranch]:
    """幹線に入りきった手元の分岐（片付け待ち）。回路図には描かず、「作業台の片付け」だけが使う。
    判定は fetch していない origin/<既定分岐> で行う。古くても「入りきっていない分岐を入りきっている」とは誤判定しない。"""
    base = f"origin/{default}"
    tip = _git(dev, "rev-parse", "--verify", "--quiet", base)
    if not tip:
        return []
    out = _git(dev, "for-each-ref", "refs/heads", f"--merged={base}", "--sort=-committerdate",
               "--format=%(refname:short)%00%(objectname)%00%(committerdate:iso-strict)") or ""
    skip = _busy_branches(dev) | {current, default}
    rows = [line.split("\0") for line in out.splitlines() if line.count("\0") == 2]
    return [MergedBranch(name=n, head=h, date=_date(d), on_github=n in heads, fresh=h == tip)
            for n, h, d in rows if n not in skip][:MAX_MERGED_READ]


def _local_branch(dev: Path, name: str, head: str, remote: str | None, base: str, known: frozenset[str] = frozenset()) -> LocalBranch:
    b = LocalBranch(name=name, head=head, on_github=remote is not None)
    if remote == head:
        return b
    if remote is None:
        b.base = _git(dev, "merge-base", head, base) or ""
    rng = [head, "--not", "--remotes"]  # どの origin/* にも無いはんだ点
    counted = False
    if remote:
        # 左=GitHub にだけある数、右=作業台にだけある数。GitHub の sha が手元に無ければ失敗する（fetch していない）
        lr = _git(dev, "rev-list", "--left-right", "--count", f"{remote}...{head}")
        if lr and len(lr.split()) == 2:
            behind, b.unpushed = (int(x) for x in lr.split())
            b.github_ahead, counted = behind > 0, True
            rng = [f"{remote}..{head}"]
            if not b.unpushed:
                return b
        else:
            b.github_ahead = True
    log = _git(dev, "log", "--format=%H%x00%s%x00%cI", *rng)
    if log is None:
        return b
    rows = [line.split("\0") for line in log.splitlines() if line.count("\0") == 2]
    if known:  # GitHub の幹線にすでにあるコミットは送電済み（origin/* の写しが古いだけ）
        rows = [r for r in rows if r[0] not in known]
    if known or not counted:  # counted の数は origin/* との比較なので、引いたら数え直す
        b.unpushed = len(rows)
    b.commits = [Commit(sha=h, message=m, date=_date(d)) for h, m, d in reversed(rows[:MAX_UNPUSHED_DOTS])]
    if b.commits:
        b.last_date = b.commits[-1].date
    return b


def _date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.now(timezone.utc)


def _ignored(path: str, patterns: list[str]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, p) for p in patterns)


def load_autodeploy(path: Path) -> dict[str, dict]:
    """agent-ops の autodeploy.json（自動デプロイ対象ツール）を読む。無ければ空。"""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in raw.items() if not k.startswith("_") and isinstance(v, dict)}
