"""書き込みの唯一の経路: 検証 → ブレーカー判定 → （注意なら確認）→ 実行。

画面・API・AI 入力のどれから来ても、GitHub への書き込みは必ず run() を通る。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .commands import command
from .guards import ACTIONS, TRIP, WARN, evaluate
from .github import GitHubError
from .issues import build_body, labels_for
from .model import RepoView
from .service import Service


class ActionError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


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
        "merge_pr": ("number", "expected_head_sha", "expected_base"),
        "close_pr": ("number",),
        "rerun_ci": ("number",),
    }[action]
    missing = [k for k in need if not p.get(k)]
    if missing:
        raise ActionError("入力が足りません: " + ", ".join(missing))
    if "number" in need:
        p["number"] = _int(p, "number")
    if action == "merge_pr":
        sha = p["expected_head_sha"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise ActionError("確認した分岐の先端（head SHA）は40桁の16進数で指定してください")
        if not isinstance(p["expected_base"], str):
            raise ActionError("確認した合流先（base）は分岐名で指定してください")
        p["expected_head_sha"] = sha.lower()
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
        return [f"分岐 {p['head']} の戻り口にスイッチ（プルリクエスト）が付きます", "自動チェック（CI）は設定された条件で実行されます。検査ランプで結果を確認してください"]
    if action == "merge_pr":
        pr = next((x for x in view.pulls if x.number == p["number"]), None)
        lines = [f"スイッチ #{p['number']} の変更を幹線に取り込みます（マージ）",
                 f"確認した先端のはんだ点（head SHA）: {p['expected_head_sha']} ／ 合流先: {p['expected_base']}"]
        if pr:
            from .issues import linked_issue

            n = linked_issue(pr)
            if n:
                lines.append(f"説明文の「Fixes #{n}」により、付箋 #{n} が自動で完了になります")
        if view.autodeploy:
            lines.append("合流後、別の自動デプロイ処理が本番への反映を試みます。到達と実際の画面を確認してください")
        lines.append("幹線への合流と、本番反映（デプロイ）は別の段階です")
        lines.append("取り消したいときは、逆向きの変更（revert）で戻せます")
        return lines
    if action == "close_pr":
        return [f"スイッチ #{p['number']} の提案を合流せずに閉じます（Close pull request）", "分岐とはんだ点はそのまま残ります"]
    if action == "rerun_ci":
        return [f"スイッチ #{p['number']} の失敗した検査をやり直します"]
    return []


class Actions:
    def __init__(self, service: Service, log_path: Path | None) -> None:
        self.service = service
        self.log_path = log_path

    async def _view(self, repo: str, *, delete_branch: str | None = None) -> RepoView:
        names = await self.service.source.repo_names()
        if repo not in names:
            raise ActionError(f"{repo} という回路（repo）は見つかりません")
        return await self.service.view(repo, delete_branch=delete_branch)

    async def command(self, action: str, params: dict) -> str:
        """入力の途中でも、いま入力されている内容で走るコマンドを返す（判定も記録もしない。表示専用）。"""
        if action not in ACTIONS:
            raise ActionError(f"未対応の操作です: {action}")
        p = {k: (v.strip() if isinstance(v, str) else v) for k, v in params.items()}
        view = await self._view(str(p.get("repo") or ""))
        try:
            return command(action, p, view.default_branch, view.local.dev_dir)
        except KeyError:
            return ""

    async def check(self, action: str, params: dict) -> dict:
        result, _ = await self._check(action, params)
        return result

    async def _check(self, action: str, params: dict) -> tuple[dict, RepoView]:
        p = normalize(action, params)
        view = await self._view(p["repo"], delete_branch=p["name"] if action == "delete_branch" else None)
        if action == "merge_pr":
            # 表示用の60秒キャッシュでは合流を判断しない。入力のSHAは取り直したSHAで置き換えない。
            pr = await self.service.source.pull_for_merge(p["repo"], p["number"])
            view.pulls = [x for x in view.pulls if x.number != p["number"]]
            if pr is not None and pr.state == "open" and not pr.merged:
                view.pulls.append(pr)
        verdicts = evaluate(action, p, view)
        if any(v.id in {"merge_missing", "merge_head_changed", "merge_base_changed"} for v in verdicts):
            self.service.source.invalidate(p["repo"])
        lines = preview(action, p, view)
        self._log(action, p, verdicts)
        return {
            "action": action,
            "label": ACTIONS[action],
            "preview": lines,
            "cmd": command(action, p, view.default_branch, view.local.dev_dir),
            "verdicts": [v.to_dict() for v in verdicts],
            "tripped": any(v.level == TRIP for v in verdicts),
            "needs_ack": any(v.level == WARN for v in verdicts),
        }, view

    async def run(self, action: str, params: dict, ack: bool = False) -> dict:
        result, view = await self._check(action, params)
        if result["tripped"]:
            return {**result, "ok": False, "message": "ブレーカーが落ちたため実行しませんでした"}
        if result["needs_ack"] and not ack:
            return {**result, "ok": False, "message": "注意を読んで確認してください"}
        p = normalize(action, params)
        src = self.service.source
        repo = p["repo"]
        url = ""
        number = None
        merge_commit_sha = ""
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
            try:
                merged = await src.merge_pr(repo, p["number"], p["expected_head_sha"])
            except GitHubError as e:
                if e.status != 409:
                    raise
                src.invalidate(repo)
                raise ActionError("確認後に分岐の先端が変わったため合流しませんでした。回路図を読み直し、検査と確認をやり直してください", status=409) from e
            if not merged.get("merged"):
                src.invalidate(repo)
                return {**result, "ok": False, "message": "GitHub で合流が完了していません。状態を確認してください"}
            merge_commit_sha = merged.get("sha") or ""
            message = f"スイッチ #{p['number']} の変更を幹線に合流しました。本番反映は別途確認してください"
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
        response = {**result, "ok": True, "message": message, "url": url, "number": number}
        if action == "merge_pr":
            response.update(pr_head_sha=p["expected_head_sha"], merge_commit_sha=merge_commit_sha)
        return response

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
