"""状態を平易な日本語にする。部品ごとの解説と、その部品でできる操作もここで組み立てる。"""

from __future__ import annotations

from .circuit import RUST_DAYS
from .commands import command
from .issues import KINDS, PRIORITIES, board_state, branch_linked_issue, branch_name_for, linked_issue, wire_for
from .model import RepoView
from .pipeline import local_totals

CI_WORD = {"success": "合格（緑）", "failure": "失敗（赤）", "pending": "検査中（黄）", "none": "検査なし", "unavailable": "取得不可"}


def summary(view: RepoView) -> dict:
    """配電盤の1行ぶん。"""
    open_issues = [i for i in view.issues if i.state == "open"]
    rust = [b for b in view.branches if not view.pull_for(b.name) and b.idle_days >= RUST_DAYS]
    fail = [p for p in view.pulls if p.ci == "failure" or p.mergeable_state == "dirty"]
    ready = [p for p in view.pulls if p.ci == "success" and p.mergeable_state != "dirty" and not p.draft]
    pending = [p for p in view.pulls if p.ci == "pending"]
    unavailable = [p for p in view.pulls if p.ci == "unavailable"]
    lag = view.run_behind
    lagging = view.local.run_exists and (lag is None or lag >= 3)
    uncommitted, unpushed = local_totals(view)

    parts: list[str] = []
    nxt = "やることはありません"
    if fail:
        parts.append(f"自動チェック失敗かショート中のスイッチが{len(fail)}つ。")
        nxt = f"スイッチ #{fail[0].number} の赤ランプ（失敗ログ）を確認"
    if lagging:
        parts.append("本番が幹線に追いついていません。")
        if not fail:
            nxt = "自動デプロイのログを確認"
    if uncommitted or unpushed:
        where = f"作業台（{view.local.dev_branch}）" if view.local.dev_branch else "作業台"
        bits = ([f"未コミットの変更が{uncommitted}つ"] if uncommitted else []) + ([f"GitHub へ送っていないはんだ点が{unpushed}つ"] if unpushed else [])
        parts.append(f"{where}に" + "、".join(bits) + "。")
        if nxt == "やることはありません":
            nxt = "作業台でコミットする" if uncommitted else "作業台から GitHub へ送る（push）"
    if rust:
        parts.append(f"{rust[0].idle_days}日以上はんだ付けされていない分岐が{len(rust)}本。")
        if nxt == "やることはありません":
            nxt = f"分岐 {rust[0].name} が要るか見直す"
    if ready:
        parts.append(f"合流の準備ができたスイッチが{len(ready)}つ（検査ランプ緑）。")
        if nxt == "やることはありません":
            nxt = f"スイッチ #{ready[0].number} を閉じる"
    if pending:
        parts.append(f"検査中のスイッチが{len(pending)}つ。")
        if nxt == "やることはありません":
            nxt = "検査が終わるのを待つ"
    if unavailable:
        parts.append(f"検査ランプが取得不可のスイッチが{len(unavailable)}つ。アプリからは合流できません。")
        if nxt == "やることはありません":
            nxt = "検査結果の読み取り権限を確認"
    unwired = [i for i in open_issues if board_state(view, i) == "unwired"]
    if unwired and nxt == "やることはありません":
        nxt = f"付箋 #{unwired[0].number} 用に分岐を引く"
    if not parts:
        parts.append(f"未接続の付箋が{len(unwired)}枚。" if unwired else "すべて幹線に合流済み。")

    led = "red" if fail else ("yellow" if pending else ("green" if ready else "off"))
    return {
        "name": view.name,
        "alert": bool(fail or rust or lagging or unavailable),
        "led": led,
        "sentence": "".join(parts),
        "issues": len(open_issues),
        "branches": len(view.branches),
        "prs": len(view.pulls),
        "deploy": "自動" if view.autodeploy else "手動",
        "uncommitted": uncommitted,
        "unpushed": unpushed,
        "dev_exists": view.local.dev_exists,
        "next": nxt,
        "viz": [
            {
                "kind": ("fail" if (p and (p.ci == "failure" or p.mergeable_state == "dirty")) else
                         "ok" if (p and p.ci == "success") else
                         "run" if (p and p.ci == "pending") else
                         "rust" if b.idle_days >= RUST_DAYS else "work"),
            }
            for b in view.branches[:5]
            for p in [view.pull_for(b.name)]
        ],
    }


def status_text(view: RepoView) -> str:
    n = len(view.branches)
    ready = sum(1 for p in view.pulls if p.ci == "success" and p.mergeable_state != "dirty")
    fail = sum(1 for p in view.pulls if p.ci == "failure")
    unavailable = sum(1 for p in view.pulls if p.ci == "unavailable")
    rust = sum(1 for b in view.branches if not view.pull_for(b.name) and b.idle_days >= RUST_DAYS)
    out = [f"{view.name} には、幹線に合流していない分岐が {n} 本あります。" if n else f"{view.name} の分岐はすべて幹線に合流済みです。"]
    if ready:
        out.append(f"合流の準備ができたスイッチが {ready} つ（検査ランプ緑）。")
    if fail:
        out.append(f"自動チェックが失敗中のスイッチが {fail} つ。")
    if unavailable:
        out.append(f"検査ランプが取得不可のスイッチが {unavailable} つ。取得不可の間はアプリから合流できません。")
    if rust:
        out.append(f"{RUST_DAYS} 日以上放置された分岐が {rust} 本。")
    loc = view.local
    uncommitted, unpushed = local_totals(view)
    if uncommitted:
        out.append(f"作業台の {loc.dev_branch or '（分岐なし）'} には、まだコミットしていない変更が {uncommitted} つ。")
    if unpushed:
        out.append(f"作業台には GitHub に送っていないはんだ点が {unpushed} つ。")
    lag = view.run_behind
    if loc.run_exists and lag:
        out.append(f"GitHub には本番に届いていない変更が {lag} つあります。")
    elif loc.run_exists and lag is None:
        out.append("本番は表示範囲より古い版で動いています（自動デプロイを確認）。")
    return "".join(out)


def _act(action: str, label: str, repo: str, danger: bool = False, fields: list[dict] | None = None, **params) -> dict:
    return {"action": action, "label": label, "danger": danger, "params": {"repo": repo, **params}, "fields": fields or []}


def parts(view: RepoView) -> dict[str, dict]:
    r = view.name
    out: dict[str, dict] = {}
    out["bus"] = {
        "title": f"幹線 {view.default_branch}",
        "term": f"{view.default_branch}（本線・デフォルトブランチ）",
        "body": "全員の土台になる配線。右端の本番ランプにつながっていて、ここに入った変更が本番に届きます。"
                "幹線に直接はんだ付けはしません。必ず分岐で作業し、スイッチ経由で合流します。",
        "cmd": f"git log origin/{view.default_branch}",
        "actions": [],
    }
    out["lamp"] = {
        "title": "本番ランプ",
        "term": f"稼働クローン C:\\lab\\run\\{r}",
        "body": ("幹線に届いた電気で光る、いま動いている本番のツール。"
                 + ("このツールは自動デプロイ対象なので、幹線に合流した変更は数分以内に本番へ反映されます。"
                    if view.autodeploy else "自動デプロイ対象ではないので、本番への反映は手作業です。")
                 + "このアプリは本番フォルダには書き込みません。"),
        "cmd": "（GitHub の外。agent-ops の DeploySweep / auto-deploy が git pull して再起動）",
        "actions": [],
    }
    lag = view.run_behind
    loc = view.local
    out["run"] = {
        "title": "本番マーカー「本番はここまで」",
        "term": f"稼働クローン C:\\lab\\run\\{r} の位置",
        "body": (("本番の稼働クローンがこの PC に見つかりません。" if not loc.run_exists else
                  "本番は表示範囲より古い版で動いています。自動デプロイが止まっていないか確認しましょう。" if lag is None else
                  "本番は幹線の先端と同じ版で動いています。" if lag == 0 else
                  f"本番で動いているのは、このはんだ点までの版です。その先の {lag} つは GitHub には入っていますが、まだ本番に届いていません（自動デプロイ待ち）。")
                 + ("本番フォルダに手で変えたファイルがあります（想定外の変更）。" if loc.run_dirty else "")),
        "cmd": f"git -C C:\\lab\\run\\{r} log -1",
        "actions": [],
    }
    out["dev"] = {
        "title": "作業台マーカー",
        "term": f"開発リポジトリ C:\\lab\\git\\{r}（いまの分岐: {loc.dev_branch or 'なし'}）",
        "body": ((f"あなたの PC の作業フォルダで、幹線 {view.default_branch} に直接はんだ付けしています。"
                  f"点線のはんだ点 {loc.dev_unpushed} つは、まだ GitHub に送っていない（push していない）変更です。"
                  "幹線には直接送らず、分岐を作ってスイッチ経由で合流させましょう。") if loc.dev_unpushed else
                 "あなたの PC の作業フォルダ。幹線の変更はすべて GitHub に送ってあります。")
                + (f"まだコミットしていない変更が {loc.dev_uncommitted} つあります。" if loc.dev_uncommitted else ""),
        "cmd": f"git -C C:\\lab\\git\\{r} status",
        "actions": [],
    }
    for lb in loc.branches:
        for c in lb.commits:
            out[f"commit:{c.sha}"] = {
                "title": f"送っていないはんだ点「{c.headline}」",
                "term": f"コミット {c.short}（作業台だけにある・未 push）",
                "body": f"{c.date:%Y-%m-%d %H:%M} に作業台で記録した変更。まだ GitHub に送っていないので、GitHub にも本番にもありません。"
                        f"分岐 {lb.name} を push すると GitHub に届きます。",
                "cmd": f"git -C C:\\lab\\git\\{r} push" + ("" if lb.on_github else f" -u origin {lb.name}"),
                "actions": [],
            }
        if lb.on_github or view.branch(lb.name):
            continue
        here = lb.name == loc.dev_branch
        out[f"local:{lb.name}"] = {
            "title": f"作業台だけの分岐 {lb.name}",
            "term": "ローカルブランチ（未 push）",
            "body": (f"あなたの PC の作業フォルダにだけある分岐。はんだ点 {lb.unpushed} つを、まだ一度も GitHub に送っていません。"
                     "送るまでは GitHub にも本番にも存在せず、スイッチ（プルリクエスト）も付けられません。"
                     + ("いま作業台で開いている分岐です。" if here else "")
                     + (f"まだコミットしていない変更も {loc.dev_uncommitted} つあります。" if here and loc.dev_uncommitted else "")
                     + "このアプリは作業台に書き込まないので、送るのはあなたのターミナルから行います。"),
            "cmd": f"git -C C:\\lab\\git\\{r} push -u origin {lb.name}",
            "actions": [],
        }
    commits = {c.sha: c for c in view.main_commits}
    for b in view.branches:
        for c in b.commits:
            commits.setdefault(c.sha, c)
    for sha, c in commits.items():
        out[f"commit:{sha}"] = {
            "title": f"はんだ点「{c.headline}」",
            "term": f"コミット {c.short}（セーブポイント）",
            "body": f"{c.date:%Y-%m-%d %H:%M} に記録された変更。はんだ点は消えないので、あとからいつでもこの時点の状態を確認できます。",
            "cmd": f"git show {c.short}",
            "actions": [],
        }
    for b in view.branches:
        pr = view.pull_for(b.name)
        n = branch_linked_issue(view, b.name)
        issue = view.issue(n) if n else None
        lb = view.local_branch(b.name)
        acts = []
        if not pr:
            default_title = issue.title if issue else b.name
            default_body = f"Fixes #{issue.number}" if issue else ""
            acts.append(_act("create_pr", "スイッチを付ける（プルリクエスト作成）", r, head=b.name, fields=[
                {"name": "title", "label": "スイッチの名前（PR タイトル）", "value": default_title},
                {"name": "body", "label": "説明（Fixes #番号 で付箋とつながる）", "value": default_body, "multiline": True},
            ]))
        acts.append(_act("delete_branch", "この分岐を撤去する", r, danger=True, name=b.name))
        rusty = not pr and b.idle_days >= RUST_DAYS
        out[f"branch:{b.name}"] = {
            "title": ("錆びた分岐 " if rusty else "分岐 ") + b.name,
            "term": "ブランチ" + ("（放置）" if rusty else ""),
            "body": (f"幹線から引き出した作業用の配線。ここで何度はんだ付け（コミット）しても、幹線と本番には影響しません。"
                     f"幹線より {b.ahead} 進み、{b.behind} 遅れています。"
                     + (f"{b.idle_days} 日間はんだ付けされていません。" if rusty else "")
                     + (f"付箋 #{issue.number} が差し込まれています。" if issue else "")
                     + (f"作業台には、まだ GitHub に送っていないはんだ点が {lb.unpushed} つあります（点線）。" if lb and lb.unpushed else "")
                     + ("GitHub 側に作業台に無いはんだ点があります（作業台に取り込むには git pull）。" if lb and lb.github_ahead else "")),
            "cmd": f"git switch {b.name}",
            "actions": acts,
        }
    for pr in view.pulls:
        n = linked_issue(pr)
        acts = [_act("merge_pr", "スイッチを閉じる（マージ）", r, number=pr.number),
                _act("close_pr", "スイッチを外す（合流せずに閉じる）", r, danger=True, number=pr.number)]
        out[f"pr:{pr.number}"] = {
            "title": f"スイッチ #{pr.number}「{pr.title}」",
            "term": "プルリクエスト",
            "body": (f"分岐 {pr.head} の変更を幹線に入れてよいか確認するスイッチ。検査ランプは {CI_WORD.get(pr.ci, pr.ci)}。"
                     + ("ショート（コンフリクト）しています。" if pr.mergeable_state == "dirty" else "")
                     + (f"閉じると付箋 #{n} が自動で完了になります。" if n else "説明文に Fixes #番号 が無いので、付箋とはつながっていません。")),
            "cmd": f"gh pr view {pr.number}",
            "url": pr.url,
            "actions": acts,
        }
        led_acts = [_act("rerun_ci", "検査をやり直す（失敗した分だけ）", r, number=pr.number)] if pr.ci == "failure" else []
        out[f"led:{pr.number}"] = {
            "title": f"検査ランプ（{CI_WORD.get(pr.ci, pr.ci)}）",
            "term": "CI（自動チェック）",
            "body": {"success": "自動テストが全部通りました。閉じても安全というサインです。",
                     "failure": "自動テストが失敗しています。GitHub で失敗ログを読み、分岐に修正をはんだ付けすると、ランプは自動で再検査されます。",
                     "pending": "自動テストの実行中です。終わるまでスイッチは閉じられません。",
                     "none": "この回路には自動テストが設定されていません。",
                     "unavailable": "GitHub の検査結果を取得できません（Checks API が 403 で、Actions の実行結果からも判定できない）。検査なしとは異なります。取得不可の間はブレーカーがアプリからの合流を止めます。"}.get(pr.ci, ""),
            "cmd": f"gh pr checks {pr.number}",
            "url": pr.url + "/checks" if pr.url else "",
            "actions": led_acts,
        }
    for issue in view.issues:
        out[f"issue:{issue.number}"] = issue_part(view, issue)
    for part in out.values():
        _attach_commands(part, view)
    return out


def _attach_commands(part: dict, view: RepoView) -> None:
    """各操作ボタンに「コマンドで言うと」の文字列を付ける。入力欄は既定値で埋めた形（実際の値は確認画面で見える）。"""
    for a in part.get("actions", []):
        defaults = {f["name"]: f["value"] for f in a["fields"]}
        a["cmd"] = command(a["action"], {**a["params"], **defaults}, view.default_branch)


def issue_part(view: RepoView, issue) -> dict:
    r = view.name
    state = board_state(view, issue)
    wire, pr = wire_for(view, issue)
    acts = []
    if state == "unwired":
        acts.append(_act("create_branch", "この付箋用に分岐を引く", r, fields=[
            {"name": "name", "label": "分岐の名前", "value": branch_name_for(issue)}]))
    if issue.state == "open":
        acts.append(_act("comment_issue", "メモを足す（コメント）", r, number=issue.number, fields=[
            {"name": "body", "label": "メモ", "value": "", "multiline": True}]))
        acts.append(_act("close_issue", "完了にする", r, danger=True, number=issue.number))
    else:
        acts.append(_act("reopen_issue", "未完了に戻す", r, number=issue.number))
    where = {"unwired": "まだどの分岐にも差し込まれていない（未着手）",
             "wired": f"分岐 {wire} に差し込まれている（対応中）" + (f"。スイッチ #{pr.number} が閉じると自動で完了" if pr else ""),
             "done": "完了（クローズ済み）"}[state]
    return {
        "title": f"付箋 #{issue.number}「{issue.title}」",
        "term": f"Issue · {KINDS.get(issue.kind, issue.kind)} · {PRIORITIES.get(issue.priority, issue.priority)}",
        "body": f"{where}。",
        "cmd": f"gh issue view {issue.number} -R h-takaha/{r}",
        "url": issue.url,
        "actions": acts,
    }
