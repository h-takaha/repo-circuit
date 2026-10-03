"""repo-circuit — GitHub を配線図で見て・触って・覚える課題管理 Web アプリ。"""

from __future__ import annotations

import hashlib
import asyncio
import hmac
import os
import secrets
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from common.basic_auth import create_basic_auth_middleware

from .actions import ActionError, Actions, read_guard_log
from .circuit import layout
from .config import Settings, load_settings
from .explain import parts, status_text, summary
from .github import GitHubError, GitHubSource
from .guards import ACTIONS, RULES
from .issues import KINDS, PRIORITIES, board_state, labels_for, wire_for
from .model import Issue, now
from .pipeline import STAGES
from .pipeline import rows as pipeline_rows
from .service import Service
from .worktrees import repo_path, validate_repo_name

ROOT = Path(__file__).resolve().parent.parent
RULE_NAMES = {r.id: r.rule for r in RULES}


def create_app(settings: Settings | None = None, source=None) -> FastAPI:
    settings = settings or load_settings()
    if source is None:
        if settings.demo:
            from .demo import DemoSource

            source = DemoSource()
        else:
            source = GitHubSource(settings.github_token, settings.owner, settings.cache_ttl)
    service = Service(settings, source)
    log_path = settings.data_dir / "guard-log.jsonl"
    actions = Actions(service, log_path)
    # CSRF トークンは秘密値から導出する。起動ごとのランダムだと、再起動前に開いたページや
    # 別ワーカーが描いたページからの操作が「一致しません」になるため。
    secret = (os.getenv("CSRF_SECRET") or os.getenv("BASIC_AUTH_PASSWORD") or "").encode("utf-8")
    if not secret:
        secret = secrets.token_bytes(32)
    csrf = hmac.new(secret, b"repo-circuit-csrf", hashlib.sha256).hexdigest()
    used_ids_path = settings.data_dir / "used-submit-ids.txt"

    app = FastAPI(title="repo-circuit", docs_url=None, redoc_url=None, openapi_url=None)
    app.middleware("http")(create_basic_auth_middleware("repo-circuit"))
    app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=ROOT / "templates")
    # static の URL に内容ハッシュを付ける。更新後もブラウザが古い JS/CSS をキャッシュから使い続けないようにする。
    asset_versions = {
        f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:10] for f in (ROOT / "static").iterdir() if f.is_file()
    }

    def asset(name: str) -> str:
        return f"/static/{name}?v={asset_versions.get(name, '0')}"

    templates.env.globals.update(csrf=csrf, demo=settings.demo, owner=settings.owner, kinds=KINDS, priorities=PRIORITIES, asset=asset)

    def render(request: Request, name: str, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, ctx)

    def check_csrf(token: str | None) -> None:
        if not token or not hmac.compare_digest(token, csrf):
            raise HTTPException(403, "操作の確認用トークンが一致しません。アプリが再起動した可能性があります。ページを再読み込みしてから、もう一度操作してください。")

    @app.exception_handler(GitHubError)
    async def github_error(request: Request, exc: GitHubError):
        return JSONResponse({"ok": False, "message": f"GitHub がエラーを返しました（{exc.status}）: {exc.message}"}, status_code=502)

    @app.get("/healthz")
    async def healthz():
        return {"ok": True, "demo": settings.demo}

    @app.get("/", response_class=HTMLResponse)
    async def panel(request: Request):
        views = await service.views()
        rows = sorted((summary(v) for v in views), key=lambda r: (not r["alert"], r["led"] != "red", r["name"]))
        unwired = sum(1 for v in views for i in v.issues if i.state == "open" and board_state(v, i) == "unwired")
        ready = sum(1 for v in views for p in v.pulls if p.ci == "success" and p.mergeable_state != "dirty")
        since = datetime.now(timezone.utc) - timedelta(days=7)
        trips = Counter(
            rule["id"]
            for e in read_guard_log(log_path)
            if _parse(e.get("at")) >= since
            for rule in e.get("rules", [])
            if rule.get("level") == "trip"
        )
        top = trips.most_common(1)
        return render(request, "panel.html", rows=rows, alerts=sum(r["alert"] for r in rows), unwired=unwired, ready=ready,
                      top_trip=(RULE_NAMES.get(top[0][0], top[0][0]), top[0][1]) if top else None, active="panel")

    @app.get("/repo/{name}", response_class=HTMLResponse)
    async def circuit(request: Request, name: str):
        try:
            names = await source.repo_names()
            if name not in names:
                raise KeyError(name)
            view = await service.view(name)
        except KeyError:
            raise HTTPException(404, f"{name} という回路は見つかりません")
        chips = []
        for i in view.issues:
            state = board_state(view, i)
            chips.append({"number": i.number, "title": i.title, "state": state, "part": f"issue:{i.number}"})
        order = {"wired": 0, "unwired": 1, "done": 2}
        chips.sort(key=lambda c: (order[c["state"]], c["number"]))
        return render(request, "circuit.html", view=view, scene=layout(view), parts=parts(view), status=status_text(view),
                      chips=chips[:12], names=names, pipeline=pipeline_rows(view), stages=STAGES, active="circuit",
                      repo_name=name, repo_url=view.url or None, repo_tab="circuit")

    @app.get("/repo/{name}/worktrees", response_class=HTMLResponse)
    async def worktrees(request: Request, name: str):
        deadline = time.monotonic() + 15
        try:
            validate_repo_name(name)
            path = None if settings.demo else repo_path(settings.lab_git_dir, name)
        except (ValueError, OSError):
            raise HTTPException(404, "作業場所のrepoが見つかりません")
        names = []
        try:
            names = await asyncio.wait_for(source.repo_names(), timeout=3)
        except Exception:
            pass
        github_known = name in names
        # 登録されたGitHub repoか、設定済みroot直下の既存local repoだけ。
        if name not in names and (settings.demo or not (path / ".git").exists()):
            raise HTTPException(404, "作業場所のrepoが見つかりません")
        if name not in names:
            names = sorted(set(names + [name]))
        try:
            snapshot, url = await service.worktrees(name, deadline=deadline)
        except (KeyError, ValueError):
            raise HTTPException(404, "作業場所のrepoが見つかりません")
        if not github_known and not snapshot.rows:
            raise HTTPException(404, "有効なローカルrepoの登録を確認できませんでした")
        return render(request, "worktrees.html", snapshot=snapshot, repo_name=name,
                      repo_url=url, names=names, repo_tab="worktrees", active="circuit")

    @app.get("/repo", response_class=HTMLResponse)
    async def circuit_index():
        names = await source.repo_names()
        return RedirectResponse(f"/repo/{names[0]}" if names else "/", status_code=303)

    # 起票フォームの使い捨て送信トークン（「ランダムID.署名」）。署名はどのワーカーでも検証でき、
    # 使用済み ID はファイルで共有する。再読み込みによる再送信や二重クリックで Issue が2件できるのを防ぐ。
    def _sign(sid: str) -> str:
        return hmac.new(secret, f"submit:{sid}".encode(), hashlib.sha256).hexdigest()[:24]

    def new_submit_id() -> str:
        sid = secrets.token_urlsafe(12)
        return f"{sid}.{_sign(sid)}"

    def use_submit_id(token: str) -> bool:
        sid, _, sig = token.partition(".")
        if not sid or not hmac.compare_digest(sig, _sign(sid)):
            return False
        try:
            used = used_ids_path.read_text(encoding="utf-8").split() if used_ids_path.exists() else []
        except OSError:
            used = []
        if sid in used:
            return False
        try:
            used_ids_path.parent.mkdir(parents=True, exist_ok=True)
            used_ids_path.write_text("\n".join((used + [sid])[-1000:]) + "\n", encoding="utf-8")
        except OSError:
            pass
        return True

    async def tray_context(repo_filter: str = "", created: Issue | None = None) -> dict:
        views = await service.views()
        cols = {"unwired": [], "wired": [], "done": []}
        for v in views:
            if repo_filter and v.name != repo_filter:
                continue
            for i in v.issues:
                state = board_state(v, i)
                wire, pr = wire_for(v, i)
                note = ""
                if state == "wired":
                    note = f"分岐 {wire}" + (f" · スイッチ #{pr.number} " + {"success": "緑", "failure": "赤", "pending": "検査中", "unavailable": "取得不可"}.get(pr.ci, "") if pr else "")
                cols[state].append({"issue": i, "note": note})
        for c in cols.values():
            c.sort(key=lambda x: (x["issue"].priority, -x["issue"].created_at.timestamp()))
        cols["done"] = cols["done"][:20]
        # 起票直後は GitHub の一覧にまだ出ないことがあるので、作った付箋を先頭に足して見せる
        if created and not any(
            c["issue"].repo == created.repo and c["issue"].number == created.number for col in cols.values() for c in col
        ):
            cols["unwired"].insert(0, {"issue": created, "note": "たった今"})
        return {"cols": cols, "names": [v.name for v in views], "repo_filter": repo_filter, "submit_id": new_submit_id()}

    @app.get("/tray", response_class=HTMLResponse)
    async def tray(
        request: Request,
        repo: str = "",
        created_repo: str = "",
        created_no: int = 0,
        created_title: str = "",
        created_kind: str = "",
        created_priority: str = "",
        created_url: str = "",
    ):
        created, result = None, None
        if created_repo and created_no:
            created = Issue(repo=created_repo, number=created_no, title=created_title, body="", state="open",
                            labels=labels_for(created_kind, created_priority), created_at=now(), url=created_url)
            result = {
                "ok": True,
                "message": f"付箋 #{created_no} を貼りました",
                "url": created_url if created_url.startswith("https://github.com/") else "",
                "cmd": f'gh issue create -R {settings.owner}/{created_repo} -t "{created_title}" -l {",".join(created.labels)}',
            }
        return render(request, "tray.html", **await tray_context(repo, created), result=result, form={}, active="tray")

    @app.post("/tray", response_class=HTMLResponse)
    async def tray_post(
        request: Request,
        csrf_token: str = Form(""),
        repo: str = Form(""),
        title: str = Form(""),
        situation: str = Form(""),
        want: str = Form(""),
        kind: str = Form("improvement"),
        priority: str = Form("p2"),
        submit_id: str = Form(""),
    ):
        check_csrf(csrf_token)
        form = {"repo": repo, "title": title, "situation": situation, "want": want, "kind": kind, "priority": priority}
        if not use_submit_id(submit_id):
            result = {"ok": False, "message": "このフォームは送信済みです。同じ付箋が2枚できないよう、今回は貼りませんでした。部品トレイで貼られているか確認し、無ければもう一度入力してください。", "verdicts": []}
            return render(request, "tray.html", **await tray_context(), result=result, form={}, active="tray")
        try:
            result = await actions.run("create_issue", {**form, "source": "web"}, ack=True)
        except ActionError as e:
            result = {"ok": False, "message": str(e), "verdicts": []}
        if result.get("ok"):
            # Post/Redirect/Get: 結果ページを再読み込みしてもフォームが再送信されないよう、GET に移す
            q = urlencode({"created_repo": repo, "created_no": result.get("number") or 0, "created_title": title, "created_kind": kind,
                           "created_priority": priority, "created_url": result.get("url", "")})
            return RedirectResponse(f"/tray?{q}", status_code=303)
        return render(request, "tray.html", **await tray_context(), result=result, form=form, active="tray")

    @app.get("/guide", response_class=HTMLResponse)
    async def guide(request: Request):
        return render(request, "guide.html", rules=RULES, active="guide")

    # ---- 画面の JS から呼ぶ操作 ----
    async def _action(request: Request, run: bool):
        check_csrf(request.headers.get("X-CSRF-Token"))
        data = await request.json()
        action, params = data.get("action", ""), data.get("params") or {}
        try:
            if run:
                return await actions.run(action, params, ack=bool(data.get("ack")))
            return await actions.check(action, params)
        except ActionError as e:
            return JSONResponse({"ok": False, "message": str(e), "verdicts": []}, status_code=e.status)

    @app.post("/actions/check")
    async def action_check(request: Request):
        return await _action(request, run=False)

    @app.post("/actions/command")
    async def action_command(request: Request):
        check_csrf(request.headers.get("X-CSRF-Token"))
        data = await request.json()
        try:
            return {"cmd": await actions.command(data.get("action", ""), data.get("params") or {})}
        except ActionError as e:
            return JSONResponse({"cmd": "", "message": str(e)}, status_code=400)

    @app.post("/actions/run")
    async def action_run(request: Request):
        return await _action(request, run=True)

    # ---- AI（Claude / Codex）からの入力用 API ----
    @app.get("/api/repos")
    async def api_repos():
        return {"repos": await source.repo_names()}

    @app.get("/api/issues")
    async def api_issues(repo: str = "", state: str = "open"):
        views = [await service.view(repo)] if repo else await service.views()
        out = []
        for v in views:
            for i in v.issues:
                if state != "all" and i.state != state:
                    continue
                out.append({"repo": v.name, "number": i.number, "title": i.title, "state": i.state, "board": board_state(v, i),
                            "kind": i.kind, "priority": i.priority, "url": i.url})
        return {"issues": out}

    @app.post("/api/issues")
    async def api_create_issue(request: Request):
        # JSON 以外を拒否する: ブラウザの素のフォーム送信（CSRF）ではこの Content-Type を作れない
        if not request.headers.get("content-type", "").startswith("application/json"):
            raise HTTPException(415, "Content-Type: application/json で送ってください")
        data = await request.json()
        params = {k: data.get(k, "") for k in ("repo", "title", "situation", "want", "note", "kind", "priority")}
        params["situation"] = params["situation"] or data.get("body", "")
        params["source"] = data.get("source", "claude")
        try:
            result = await actions.run("create_issue", params, ack=True)
        except ActionError as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=400)
        return JSONResponse(result, status_code=201 if result.get("ok") else 422)

    return app


def _parse(value: str | None) -> datetime:
    try:
        return datetime.fromisoformat(value or "")
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


app = create_app()
