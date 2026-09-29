"""書き込みの唯一の経路: 検証 → ブレーカー判定 → （注意なら確認）→ 実行。

画面・API・AI 入力のどれから来ても、GitHub への書き込みは必ず run() を通る。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .commands import command
from .guards import ACTIONS, TRIP, WARN, evaluate
from .issues import build_body, labels_for
from .model import RepoView
from .service import Service


class ActionError(Exception):
    pass


def _int(params: dict, key: str) -> int:
    try:
        return int(params.get(key) or 0)
    except (TypeError, ValueError):
        raise ActionError(f"{key} は数字で指定してください")


def normalize(action: str, params: dict) -> dict:
    if action not in ACTIONS:
        raise ActionError(f"未対応の操作です: {action}")
    p = {k: (v.strip() if isinstance(v, str) else v) for k, v in params.items()}
    if not p.get("repo"):
        raise ActionError("どのツール（repo）か指定してください")
    need = {
        "create_issue": ("title",),
        "comment_issue": ("number", "body"),
        "close_issue": ("number",),
        "reopen_issue": ("number",),
        "create_branch": ("name",),
        "delete_branch": ("name",),
        "create_pr": ("head", "title"),
        "merge_pr": ("number",),
        "close_pr": ("number",),
        "rerun_ci": ("number",),
    }[action]
    missing = [k for k in need if not p.get(k)]
    if missing:
        raise ActionError("入力が足りません: " + ", ".join(missing))
    if "number" in need:
        p["number"] = _int(p, "number")
    return p


def preview(action: str, p: dict, view: RepoView) -> list[str]:
    """この操作で起きること（平易な文）。同じ操作のコマンドは commands.command()。"""
    if action == "create_issue":
        return [f"{view.name} の repo に付箋（Issue）「{p['title']}」が作られます", "ラベル feedback が付き、部品トレイの「未接続」に並びます"]
    if action == "comment_issue":
        return [f"付箋 #{p['number']} にメモ（コメント）が追加されます"]
    if action == "close_issue":
        return [f"付箋 #{p['number']} が「完了」列に移ります", "GitHub では Closed になり、あとから読み返せます"]
    if action == "reopen_issue":
        return [f"付箋 #{p['number']} が未完了に戻ります"]
    if action == "create_branch":
        return [f"幹線の最新のはんだ点から分岐 {p['name']} が引き出されます", "分岐で何をしても、合流するまで幹線と本番には影響しません"]
    if action == "delete_branch":
        return [f"分岐 {p['name']} が GitHub から撤去されます"]
    if action == "create_pr":
        return [f"分岐 {p['head']} の戻り口にスイッチ（プルリクエスト）が付きます", "自動チェック（CI）が走り、検査ランプが点きます"]
    if action == "merge_pr":
        pr = next((x for x in view.pulls if x.number == p["number"]), None)
        lines = [f"スイッチ #{p['number']} が閉じ、分岐のはんだ点が幹線に入ります（マージ）"]
        if pr:
            from .issues import linked_issue

            n = linked_issue(pr)
            if n:
                lines.append(f"説明文の「Fixes #{n}」により、付箋 #{n} が自動で完了になります")
        if view.autodeploy:
            lines.append("自動デプロイで、数分以内に本番が新しい版に更新されます")
        lines.append("取り消したいときは、逆向きの変更（revert）で戻せます")
        return lines
    if action == "close_pr":
        return [f"スイッチ #{p['number']} を外します（合流せずに閉じる）", "分岐とはんだ点はそのまま残ります"]
    if action == "rerun_ci":
        return [f"スイッチ #{p['number']} の失敗した検査をやり直します"]
    return []


class Actions:
    def __init__(self, service: Service, log_path: Path | None) -> None:
        self.service = service
        self.log_path = log_path

    async def _view(self, repo: str) -> RepoView:
        names = await self.service.source.repo_names()
        if repo not in names:
            raise ActionError(f"{repo} という回路（repo）は見つかりません")
        return await self.service.view(repo)

    async def check(self, action: str, params: dict) -> dict:
        p = normalize(action, params)
        view = await self._view(p["repo"])
        verdicts = evaluate(action, p, view)
        lines = preview(action, p, view)
        self._log(action, p, verdicts)
        return {
            "action": action,
            "label": ACTIONS[action],
            "preview": lines,
            "cmd": command(action, p, view.default_branch),
            "verdicts": [v.to_dict() for v in verdicts],
            "tripped": any(v.level == TRIP for v in verdicts),
            "needs_ack": any(v.level == WARN for v in verdicts),
        }

    async def run(self, action: str, params: dict, ack: bool = False) -> dict:
        result = await self.check(action, params)
        if result["tripped"]:
            return {**result, "ok": False, "message": "ブレーカーが落ちたため実行しませんでした"}
        if result["needs_ack"] and not ack:
            return {**result, "ok": False, "message": "注意を読んで確認してください"}
        p = normalize(action, params)
        view = await self.service.view(p["repo"])
        src = self.service.source
        repo = p["repo"]
        url = ""
        number = None
        if action == "create_issue":
            body = build_body(p.get("situation", ""), p.get("want", ""), p.get("note", ""), p.get("source", "web"))
            res = await src.create_issue(repo, p["title"], body, labels_for(p.get("kind", ""), p.get("priority", "")))
            url, number = res.get("html_url", ""), res.get("number")
            message = f"付箋 #{number} を貼りました"
        elif action == "comment_issue":
            await src.comment(repo, p["number"], p["body"])
            message = f"付箋 #{p['number']} にメモを足しました"
        elif action == "close_issue":
            await src.set_issue_state(repo, p["number"], "closed")
            message = f"付箋 #{p['number']} を完了にしました"
        elif action == "reopen_issue":
            await src.set_issue_state(repo, p["number"], "open")
            message = f"付箋 #{p['number']} を未完了に戻しました"
        elif action == "create_branch":
            await src.create_branch(repo, p["name"], view.head_sha)
            message = f"分岐 {p['name']} を引きました"
        elif action == "delete_branch":
            await src.delete_branch(repo, p["name"])
            message = f"分岐 {p['name']} を撤去しました"
        elif action == "create_pr":
            res = await src.create_pr(repo, p["head"], view.default_branch, p["title"], p.get("body", ""))
            url, number = res.get("html_url", ""), res.get("number")
            message = f"スイッチ #{number} を付けました"
        elif action == "merge_pr":
            await src.merge_pr(repo, p["number"])
            message = f"スイッチ #{p['number']} を閉じました。通電しました"
        elif action == "close_pr":
            await src.close_pr(repo, p["number"])
            message = f"スイッチ #{p['number']} を外しました"
        elif action == "rerun_ci":
            pr = next((x for x in view.pulls if x.number == p["number"]), None)
            if not pr:
                raise ActionError(f"スイッチ #{p['number']} が見つかりません")
            count = await src.rerun_failed(repo, pr.head_sha)
            message = f"検査のやり直しを {count} 件依頼しました" if count else "やり直す失敗検査はありませんでした"
        else:  # normalize() で弾いているので到達しない
            raise ActionError(action)
        src.invalidate(repo)
        return {**result, "ok": True, "message": message, "url": url, "number": number}

    def _log(self, action: str, p: dict, verdicts) -> None:
        if not self.log_path or not verdicts:
            return
        # 本文（秘密を含むかもしれない）は記録しない。どの操作でどのブレーカーが反応したかだけ残す。
        entry = {
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "repo": p.get("repo"),
            "rules": [{"id": v.id, "level": v.level} for v in verdicts],
        }
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass


def read_guard_log(path: Path | None, limit: int = 500) -> list[dict]:
    if not path or not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-limit:]
    except OSError:
        return []
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out
