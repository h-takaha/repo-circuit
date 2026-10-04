"""操作ボタンに対応する本物のコマンド。

回路図のボタン横・確認画面・「ターミナルから実行」のコピーが、同じ文字列を見せる（画面の操作とコマンドの対応を覚えるため）。
ここは文字列を作るだけで、コマンドを実行しない。

コピーして貼り付けると実行されるので、GitHub 由来の文字列（タイトル・本文・分岐名）は PowerShell で解釈されない形にする。
"""

from __future__ import annotations

import re
import hashlib

from .issues import build_body, labels_for

_SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def q(text: object) -> str:
    """PowerShell の単一引用符の文字列。中の $ や " は解釈されない。' だけ '' に重ねる。"""
    return "'" + str(text).replace("'", "''") + "'"


def safe_ref(name: str) -> bool:
    """コマンドにそのまま埋め込んでよい分岐名か。"""
    return bool(_SAFE_REF.match(name)) and ".." not in name and "//" not in name and not name.endswith((".lock", "/", "."))


def default_workdir(repo: str) -> str:
    return rf"C:\lab\git\{repo}"


def _git(workdir: str, *args: str) -> str:
    return f'git -C "{workdir}" ' + " ".join(args)


# ---- 作業台の操作（アプリは実行しない。「ターミナルから実行」でコピーするだけ） ----

def switch_local(workdir: str, name: str) -> str:
    return _git(workdir, "switch", name) if safe_ref(name) else ""


def delete_local(workdir: str, name: str) -> str:
    return _git(workdir, "branch", "-d", name) if safe_ref(name) else ""


def fetch_origin(workdir: str) -> str:
    """作業台にある GitHub のコピー（origin/…）を最新にする。作業台のファイルや分岐は変わらない。"""
    return _git(workdir, "fetch", "origin")


def fast_forward_local(workdir: str, default_branch: str) -> str:
    """コピー後も幹線と未コミットを確認し、前へ進めるだけ。PowerShell用。"""
    if not safe_ref(default_branch):
        return ""
    git = f"git -C {q(workdir)}"
    return (
        f"$circuitBranch = {git} branch --show-current\n"
        f"if ($LASTEXITCODE -ne 0 -or $circuitBranch -ne {q(default_branch)}) {{\n"
        f"  Write-Error {q('先に幹線 ' + default_branch + ' に切り替えてください。')}\n"
        "} else {\n"
        f"  $circuitChanges = {git} --no-optional-locks status --porcelain\n"
        "  if ($LASTEXITCODE -ne 0 -or $circuitChanges) {\n"
        f"    Write-Error {q('未コミットの変更、または読み取り失敗があります。内容を確認してください。')}\n"
        "  } else {\n"
        f"    {git} merge --ff-only {q('refs/remotes/origin/' + default_branch)}\n"
        "  }\n"
        "}"
    )


def prune_origin(workdir: str) -> str:
    """GitHub にはもう無い分岐の、手元の古い記録（origin/…）だけを消す。fetch はしない。"""
    return _git(workdir, "remote", "prune", "origin")


def push_local(workdir: str, name: str, first_time: bool) -> str:
    if not safe_ref(name):
        return ""
    return _git(workdir, "push", "-u", "origin", name) if first_time else _git(workdir, "push", "origin", name)


def publish_local(workdir: str, branch: str, head: str, operation: str,
                  files: list[str] | None = None, message: str = "", index: str = "") -> str:
    """案内で選んだ対象だけをコピーする。コピー後の分岐・HEAD変更でも止める。"""
    if not safe_ref(branch) or not re.fullmatch(r"[0-9a-fA-F]{40}", head):
        return ""
    git = f"git --literal-pathspecs -C {q(workdir)}"
    if operation == "stage" and files:
        cmd = f"{git} add -- " + " ".join(q(path) for path in files)
    elif operation == "commit" and message.strip() and index:
        # 手動で段を進めるので、このcommitの自動push・即時deployを抑止する。
        message = message.strip() + " [no-push] [no-deploy]"
        cmd = f"{git} commit -m {q(message)}"
        # raw差分にはタブと改行がある。端末への貼付けで崩れない照合値だけを埋め込む。
        signature = hashlib.sha256(index.encode("utf-8")).hexdigest()
        cmd = (
            f'$circuitIndex = {git} -c core.quotepath=true diff --cached --raw --no-abbrev --no-ext-diff --no-renames\n'
            '  $circuitIndexExit = $LASTEXITCODE\n'
            '  $circuitHasher = [System.Security.Cryptography.SHA256]::Create()\n'
            '  try {\n'
            '    $circuitBytes = [System.Text.Encoding]::UTF8.GetBytes(($circuitIndex -join "`n"))\n'
            '    $circuitSignature = [System.BitConverter]::ToString($circuitHasher.ComputeHash($circuitBytes)).Replace("-", "").ToLowerInvariant()\n'
            '  } finally { $circuitHasher.Dispose() }\n'
            f'  if ($circuitIndexExit -ne 0 -or $circuitSignature -ne {q(signature)}) {{\n'
            f"    Write-Error {q('記録対象が変わりました。アプリで状態を読み直してください。')}\n"
            "  } else {\n"
            f"    {cmd}\n"
            "  }"
        )
    elif operation == "push":
        cmd = f"{git} push -u origin {q(branch)}"
    else:
        return ""
    return (
        f"$circuitBranch = {git} branch --show-current\n"
        f"$circuitHead = {git} rev-parse HEAD\n"
        f"if ($LASTEXITCODE -ne 0 -or $circuitBranch -ne {q(branch)} -or $circuitHead -ne {q(head)}) {{\n"
        f"  Write-Error {q('作業台の位置が変わりました。アプリで状態を読み直してください。')}\n"
        "} else {\n"
        f"  {cmd}\n"
        "}"
    )


# ---- GitHub への書き込み操作（ツールからも実行できる。同じ操作をターミナルで行うなら） ----

def command(action: str, p: dict, default_branch: str = "main", workdir: str = "") -> str:
    repo = p["repo"]
    r = f"h-takaha/{repo}"
    wd = workdir or default_workdir(repo)
    body = str(p.get("body", "") or "")
    if action == "create_issue":
        issue_body = build_body(p.get("situation", ""), p.get("want", ""), p.get("note", ""), p.get("source", "web"))
        labels = ",".join(labels_for(p.get("kind", ""), p.get("priority", "")))
        return f"gh issue create -R {r} -t {q(p.get('title', ''))} -b {q(issue_body)} -l {labels}"
    if action == "comment_issue":
        return f"gh issue comment {p['number']} -R {r}" + (f" -b {q(body)}" if body else "")
    if action == "close_issue":
        return f"gh issue close {p['number']} -R {r}"
    if action == "reopen_issue":
        return f"gh issue reopen {p['number']} -R {r}"
    if action == "create_branch":
        name = p["name"]
        if not safe_ref(name):
            return ""
        return (f"{_git(wd, 'fetch', 'origin')} && {_git(wd, 'switch', '-c', name, f'origin/{default_branch}')}"
                f" && {_git(wd, 'push', '-u', 'origin', name)}")
    if action == "delete_branch":
        return _git(wd, "push", "origin", "--delete", p["name"]) if safe_ref(p["name"]) else ""
    if action == "create_pr":
        if not safe_ref(p["head"]):
            return ""
        return (f"gh pr create -R {r} --base {default_branch} --head {p['head']} -t {q(p.get('title', ''))}"
                + (f" -b {q(body)}" if body else ""))
    if action == "merge_pr":
        sha = p.get("expected_head_sha", "")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            return ""
        return f"gh pr merge {p['number']} -R {r} --merge --match-head-commit {sha.lower()}"
    if action == "close_pr":
        return f"gh pr close {p['number']} -R {r}"
    if action == "rerun_ci":
        return f"gh run rerun --failed -R {r}  # スイッチ #{p['number']} の run"
    return ""
