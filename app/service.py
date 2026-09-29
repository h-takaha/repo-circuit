"""データ源（GitHub かデモ）に、作業台・本番の位置と自動デプロイ情報を足して RepoView を返す。"""

from __future__ import annotations

import asyncio

from .config import Settings
from .localgit import load_autodeploy, read_local_state
from .model import RepoView


class Service:
    def __init__(self, settings: Settings, source) -> None:
        self.settings = settings
        self.source = source
        self.autodeploy = {} if settings.demo else load_autodeploy(settings.autodeploy_path)

    async def view(self, name: str, detail: bool = True) -> RepoView:
        """detail=False は配電盤・部品トレイ用。作業台を分岐ごとには読まない（repo が多いと遅いため）。"""
        v = await self.source.repo_view(name)
        if self.settings.demo:
            return v
        cfg = self.autodeploy.get(name)
        v.autodeploy = cfg is not None
        ignore = list(cfg.get("noRestartPaths", [])) if cfg and cfg.get("pullStrategy") == "preserve-local" else []
        # 送電済みかどうかは GitHub 上の分岐の位置で判断する（作業台の origin/* は fetch しないので古いことがある）
        heads = {b.name: b.sha for b in v.branches if b.sha}
        if v.head_sha:
            heads[v.default_branch] = v.head_sha
        v.local = await asyncio.to_thread(
            read_local_state, self.settings.lab_git_dir, self.settings.lab_run_dir, name, ignore, heads, v.default_branch, detail,
            set(v.github_names) if v.github_names is not None else None,
        )
        return v

    async def views(self) -> list[RepoView]:
        names = await self.source.repo_names()
        results = await asyncio.gather(*(self.view(n, detail=False) for n in names), return_exceptions=True)
        return [r for r in results if isinstance(r, RepoView)]
