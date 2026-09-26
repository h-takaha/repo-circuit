"""作業台（C:\\lab\\git\\<repo>）と本番（C:\\lab\\run\\<repo>）の位置を読む。

読み取り専用。fetch / pull / checkout など状態を変える git コマンドは一切呼ばない。
稼働クローンにはこのアプリから書き込まない（agent-ops の「run\\ は開かない」方針）。
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .model import LocalState

_TIMEOUT = 3


def _git(cwd: Path, *args: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def read_local_state(lab_git: Path, lab_run: Path, repo: str, ignore_dirty: list[str] | None = None) -> LocalState:
    st = LocalState()
    dev = lab_git / repo
    if (dev / ".git").exists():
        st.dev_exists = True
        st.dev_head = _git(dev, "rev-parse", "HEAD") or ""
        count = _git(dev, "rev-list", "--count", "origin/HEAD..HEAD") or _git(dev, "rev-list", "--count", "origin/main..HEAD")
        st.dev_unpushed = int(count) if count and count.isdigit() else 0
    run = lab_run / repo
    if (run / ".git").exists():
        st.run_exists = True
        st.run_head = _git(run, "rev-parse", "HEAD") or ""
        porcelain = _git(run, "status", "--porcelain") or ""
        changed = [line[3:] for line in porcelain.splitlines() if len(line) > 3]
        st.run_dirty = any(not _ignored(p, ignore_dirty or []) for p in changed)
    return st


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
