"""操作ボタンに対応する本物のコマンド。

回路図のボタン横・確認画面・ブレーカーの表示が、同じ文字列を見せる（画面の操作とコマンドの対応を覚えるため）。
ここは文字列を作るだけで、コマンドを実行しない。
"""

from __future__ import annotations

from .issues import labels_for


def command(action: str, p: dict, default_branch: str = "main") -> str:
    r = f"h-takaha/{p['repo']}"
    if action == "create_issue":
        return f'gh issue create -R {r} -t "{p.get("title", "")}" -l {",".join(labels_for(p.get("kind", ""), p.get("priority", "")))}'
    if action == "comment_issue":
        return f"gh issue comment {p['number']} -R {r}"
    if action == "close_issue":
        return f"gh issue close {p['number']} -R {r}"
    if action == "reopen_issue":
        return f"gh issue reopen {p['number']} -R {r}"
    if action == "create_branch":
        return f"git switch -c {p['name']} origin/{default_branch} && git push -u origin {p['name']}"
    if action == "delete_branch":
        return f"git push origin --delete {p['name']}"
    if action == "create_pr":
        return f'gh pr create -R {r} --head {p["head"]} -t "{p.get("title", "")}"'
    if action == "merge_pr":
        return f"gh pr merge {p['number']} -R {r} --merge"
    if action == "close_pr":
        return f"gh pr close {p['number']} -R {r}"
    if action == "rerun_ci":
        return f"gh run rerun --failed  # スイッチ #{p['number']} の run"
    return ""
