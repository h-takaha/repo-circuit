"""データ源（GitHub かデモ）に、作業台・本番の位置と自動デプロイ情報を足して RepoView を返す。"""

from __future__ import annotations

import asyncio

from .config import Settings
from .localgit import load_autodeploy, read_local_state
from .model import RepoView


def mark_origin_lag(v: RepoView) -> None:
    """作業台の origin/<既定分岐>（GitHub のコピー）が GitHub の幹線より古いかを印す。
    このアプリは fetch しないので、古いままだと「未送電」が実際より多く出る。"""
    loc = v.local
    if not (loc.dev_exists and loc.origin_main and v.head_sha) or loc.origin_main == v.head_sha:
        return
    loc.origin_stale = True
    shas = [c.sha for c in v.main_commits]  # 古い順
    loc.origin_behind = len(shas) - 1 - shas.index(loc.origin_main) if loc.origin_main in shas else None


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
            {c.sha for c in v.main_commits},
        )
        mark_origin_lag(v)
        return v

    async def views(self) -> list[RepoView]:
        names = await self.source.repo_names()
        results = await asyncio.gather(*(self.view(n, detail=False) for n in names), return_exceptions=True)
        return [r for r in results if isinstance(r, RepoView)]
