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

    async def view(self, name: str) -> RepoView:
        v = await self.source.repo_view(name)
        if self.settings.demo:
            return v
        cfg = self.autodeploy.get(name)
        v.autodeploy = cfg is not None
        ignore = list(cfg.get("noRestartPaths", [])) if cfg and cfg.get("pullStrategy") == "preserve-local" else []
        v.local = await asyncio.to_thread(
            read_local_state, self.settings.lab_git_dir, self.settings.lab_run_dir, name, ignore
        )
        return v

    async def views(self) -> list[RepoView]:
        names = await self.source.repo_names()
        results = await asyncio.gather(*(self.view(n) for n in names), return_exceptions=True)
        return [r for r in results if isinstance(r, RepoView)]
