"""環境変数からの設定。GITHUB_TOKEN が無ければデモモードで動く。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    github_token: str
    owner: str
    lab_git_dir: Path
    lab_run_dir: Path
    agent_ops_dir: Path
    data_dir: Path
    cache_ttl: float

    @property
    def demo(self) -> bool:
        return not self.github_token

    @property
    def autodeploy_path(self) -> Path:
        return self.agent_ops_dir / "automation" / "config" / "autodeploy.json"


def load_settings() -> Settings:
    return Settings(
        github_token=os.getenv("GITHUB_TOKEN", "").strip(),
        owner=os.getenv("GITHUB_OWNER", "h-takaha"),
        lab_git_dir=Path(os.getenv("LAB_GIT_DIR", "C:/lab/git")),
        lab_run_dir=Path(os.getenv("LAB_RUN_DIR", "C:/lab/run")),
        agent_ops_dir=Path(os.getenv("AGENT_OPS_DIR", "C:/lab/git/agent-ops")),
        data_dir=Path(os.getenv("REPO_CIRCUIT_DATA_DIR", "C:/lab/data/repo-circuit")),
        cache_ttl=float(os.getenv("CACHE_TTL_SECONDS", "60")),
    )
