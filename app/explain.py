"""状態を平易な日本語にする。部品ごとの解説と、その部品でできる操作もここで組み立てる。"""

from __future__ import annotations

from .circuit import RUST_DAYS
from .commands import command, default_workdir, delete_local, fetch_origin, prune_origin, push_local, switch_local
from .issues import KINDS, PRIORITIES, board_state, branch_linked_issue, branch_name_for, linked_issue, wire_for
from .model import RepoView
from .pipeline import local_totals

CI_WORD = {"success": "合格（緑）", "failure": "失敗（赤）", "pending": "検査中（黄）", "none": "検査なし", "unavailable": "取得不可"}


def summary(view: RepoView) -> dict:
    """配電盤の1行ぶん。"""
    open_issues = [i for i in view.issues if i.state == "open"]
    active = [b for b in view.branches if not view.completed_branch(b.name)]
    rust = [b for b in active if not view.pull_for(b.name) and b.idle_days >= RUST_DAYS]
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
        ref = "（作業台の GitHub のコピーが古いため、実際より多いかもしれません）" if view.local.origin_stale else ""
        bits = ([f"未コミットの変更が{uncommitted}つ"] if uncommitted else []) + ([f"GitHub へ送っていないはんだ点が{unpushed}つ{ref}"] if unpushed else [])
        parts.append(f"{where}に" + "、".join(bits) + "。")
        if nxt == "やることはありません":
            nxt = ("作業台でコミットする" if uncommitted else
                   "作業台の GitHub のコピーを最新にする（fetch）" if view.local.origin_stale else "作業台から GitHub へ送る（push）")
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
        "branches": len(active),
        "prs": len(view.pulls),
        "deploy": "自動" if view.autodeploy else "手動",
        "uncommitted": uncommitted,
        "unpushed": unpushed,
        "origin_stale": view.local.origin_stale,
        "dev_exists": view.local.dev_exists,
        "next": nxt,
        "viz": [
            {
                "kind": ("fail" if (p and (p.ci == "failure" or p.mergeable_state == "dirty")) else
                         "ok" if (p and p.ci == "success") else
                         "run" if (p and p.ci == "pending") else
                         "rust" if b.idle_days >= RUST_DAYS else "work"),
            }
            for b in active[:5]
            for p in [view.pull_for(b.name)]
        ],
    }


def status_text(view: RepoView) -> str:
    active = [b for b in view.branches if not view.completed_branch(b.name)]
    n = len(active)
    ready = sum(1 for p in view.pulls if p.ci == "success" and p.mergeable_state != "dirty")
    fail = sum(1 for p in view.pulls if p.ci == "failure")
    unavailable = sum(1 for p in view.pulls if p.ci == "unavailable")
    rust = sum(1 for b in active if not view.pull_for(b.name) and b.idle_days >= RUST_DAYS)
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
        out.append(f"作業台には GitHub に送っていないはんだ点が {unpushed} つ" + ("（GitHub のコピーが古いため、実際より多いかもしれません）。" if loc.origin_stale else "。"))
    if loc.origin_stale:
        out.append(stale_note(view))
    lag = view.run_behind
    if loc.run_exists and lag:
        out.append(f"GitHub には本番に届いていない変更が {lag} つあります。")
    elif loc.run_exists and lag is None:
        out.append("本番は表示範囲より古い版で動いています（自動デプロイを確認）。")
    return "".join(out)


def _act(action: str, label: str, repo: str, danger: bool = False, fields: list[dict] | None = None, **params) -> dict:
    return {"action": action, "label": label, "danger": danger, "params": {"repo": repo, **params}, "fields": fields or []}


def stale_note(view: RepoView) -> str:
    """作業台にある GitHub のコピー（origin/…）が古いときの説明。古くなければ空。"""
    loc = view.local
    if not loc.origin_stale:
        return ""
    lag = f"{loc.origin_behind} コミット" if loc.origin_behind else "かなり"
    return (f"作業台にある GitHub のコピー（origin/{view.default_branch}）が、GitHub の幹線より、{lag}古い状態です。"
            "そのため「未送電」の数は、実際より多く出ることがあります。fetch で最新にできます（作業台のファイルや分岐は変わりません）。")


def _fetch_act(view: RepoView, wd: str) -> list[dict]:
    return _term("GitHub のコピーを最新にする（fetch）", fetch_origin(wd)) if view.local.origin_stale else []


def _term(label: str, cmd: str) -> list[dict]:
    """作業台の操作。アプリは作業台に書き込まないので、コマンドをコピーして自分のターミナルで実行する（cmd が空なら出さない）。"""
    return [{"terminal": True, "label": label, "cmd": cmd, "danger": False, "fields": []}] if cmd else []


def parts(view: RepoView) -> dict[str, dict]:
    r = view.name
    out: dict[str, dict] = {}
    out["bus"] = {
        "title": f"幹線 {view.default_branch}",
        "term": f"{view.default_branch}（本線・デフォルトブランチ）",
        "body": "全員の土台になる配線。右端の本番ランプにつながっていて、ここに入った変更が本番に届きます。"
                "幹線に直接はんだ付けはしません。必ず分岐で作業し、スイッチ経由で合流します。",
        "actions": [],
    }
    out["lamp"] = {
        "title": "本番ランプ",
        "term": f"稼働クローン C:\\lab\\run\\{r}",
        "body": ("幹線に届いた電気で光る、いま動いている本番のツール。"
                 + ("このツールは自動デプロイ対象なので、幹線に合流した変更は数分以内に本番へ反映されます。"
                    if view.autodeploy else "自動デプロイ対象ではないので、本番への反映は手作業です。")
                 + "このアプリは本番フォルダには書き込みません。"),
        "actions": [],
    }
    lag = view.run_behind
    loc = view.local
    wd = loc.dev_dir or default_workdir(r)
    out["run"] = {
        "title": "本番マーカー「本番はここまで」",
        "term": f"稼働クローン C:\\lab\\run\\{r} の位置",
        "body": (("本番の稼働クローンがこの PC に見つかりません。" if not loc.run_exists else
                  "本番は表示範囲より古い版で動いています。自動デプロイが止まっていないか確認しましょう。" if lag is None else
                  "本番は幹線の先端と同じ版で動いています。" if lag == 0 else
                  f"本番で動いているのは、このはんだ点までの版です。その先の {lag} つは GitHub には入っていますが、まだ本番に届いていません（自動デプロイ待ち）。")
                 + ("本番フォルダに手で変えたファイルがあります（想定外の変更）。" if loc.run_dirty else "")),
        "actions": [],
    }
    out["dev"] = {
        "title": "作業台マーカー",
        "term": f"開発リポジトリ C:\\lab\\git\\{r}（いまの分岐: {loc.dev_branch or 'なし'}）",
        "body": ((f"あなたの PC の作業フォルダで、幹線 {view.default_branch} に直接はんだ付けしています。"
                  f"点線のはんだ点 {loc.dev_unpushed} つは、まだ GitHub に送っていない（push していない）変更です。"
                  "幹線には直接送らず、分岐を作ってスイッチ経由で合流させましょう。") if loc.dev_unpushed and loc.dev_branch == view.default_branch else
                 "あなたの PC の作業フォルダ。")
                + (f"分岐にまだ GitHub へ送っていないはんだ点が {loc.dev_unpushed} つあります。" if loc.dev_unpushed and loc.dev_branch != view.default_branch else "")
                + f"現在の位置は {loc.dev_head[:7] or '不明'}（分岐: {loc.dev_branch or 'detached HEAD'}）です。"
                + (f"GitHub の幹線より {view.dev_behind} コミット遅れています。" if view.dev_behind else
                   "GitHub の幹線の先端と同じ位置です。" if view.dev_behind == 0 else
                   "幹線の表示範囲には無い位置です。分岐のはんだ点も確認してください。")
                + (f"まだコミットしていない変更が {loc.dev_uncommitted} つあります。" if loc.dev_uncommitted else "")
                + stale_note(view),
        "actions": _fetch_act(view, wd),
    }
    for lb in loc.branches:
        for c in lb.commits:
            out[f"commit:{c.sha}"] = {
                "title": f"送っていないはんだ点「{c.headline}」",
                "term": f"コミット {c.short}（作業台だけにある・未 push）",
                "body": f"{c.date:%Y-%m-%d %H:%M} に作業台で記録した変更。まだ GitHub に送っていないので、GitHub にも本番にもありません。"
                        f"分岐 {lb.name} を push すると GitHub に届きます。",
                "actions": _term("GitHub に送る（push）", push_local(wd, lb.name, not lb.on_github)),
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
                     + "このアプリは作業台に書き込まないので、送るのはあなたのターミナルから行います（「ターミナルから実行」でコマンドをコピーできます）。"),
            "actions": _term("GitHub に送る（push）", push_local(wd, lb.name, True))
                       + ([] if here else _term("作業台をこの分岐に切り替える", switch_local(wd, lb.name))),
        }
    if loc.dev_exists:
        out["cleanup"] = _cleanup_part(view, wd)
    commits = {c.sha: c for c in view.main_commits}
    for b in view.branches:
        for c in b.commits:
            commits.setdefault(c.sha, c)
    for sha, c in commits.items():
        out[f"commit:{sha}"] = {
            "title": f"はんだ点「{c.headline}」",
            "term": f"コミット {c.short}（セーブポイント）",
            "body": f"{c.date:%Y-%m-%d %H:%M} に記録された変更。はんだ点は消えないので、あとからいつでもこの時点の状態を確認できます。",
            "actions": [],
        }
    for b in view.branches:
        pr = view.pull_for(b.name)
        merged = view.merged_for(b.name)
        n = branch_linked_issue(view, b.name)
        issue = view.issue(n) if n else None
        lb = view.local_branch(b.name)
        acts = []
        if not pr and not merged:
            default_title = issue.title if issue else b.name
            default_body = f"Fixes #{issue.number}" if issue else ""
            acts.append(_act("create_pr", "スイッチを付ける（プルリクエスト作成）", r, head=b.name, fields=[
                {"name": "title", "label": "スイッチの名前（PR タイトル）", "value": default_title},
                {"name": "body", "label": "説明（Fixes #番号 で付箋とつながる）", "value": default_body, "multiline": True},
            ]))
        acts.append(_act("delete_branch", "この分岐を撤去する", r, danger=True, name=b.name))
        if lb:
            here_now = lb.name == loc.dev_branch
            if lb.unpushed:
                acts += _term("GitHub に送る（push）", push_local(wd, lb.name, False))
            if not here_now:
                acts += _term("作業台をこの分岐に切り替える", switch_local(wd, lb.name))
        rusty = not pr and b.idle_days >= RUST_DAYS
        out[f"branch:{b.name}"] = {
            "title": ("錆びた分岐 " if rusty else "分岐 ") + b.name,
            "term": "ブランチ" + ("（放置）" if rusty else ""),
            "body": ((f"スイッチ #{merged.number} で合流済みです。現在の GitHub の先端は取り込み済みで、履歴上の進みを新しい作業としては数えません。"
                      if merged else
                      f"幹線から引き出した作業用の配線。ここで何度はんだ付け（コミット）しても、幹線と本番には影響しません。幹線より {b.ahead} 進み、{b.behind} 遅れています。")
                     + (f"{b.idle_days} 日間はんだ付けされていません。" if rusty else "")
                     + view.merge_evidence_errors.get(b.name, "")
                     + (f"付箋 #{issue.number} が差し込まれています。" if issue else "")
                     + (f"作業台には、まだ GitHub に送っていないはんだ点が {lb.unpushed} つあります（点線）。" if lb and lb.unpushed else "")
                     + ("GitHub 側に作業台に無いはんだ点があります（作業台に取り込むには git pull）。" if lb and lb.github_ahead else "")),
            "actions": acts,
        }
    for pr in view.pulls:
        n = linked_issue(pr)
        acts = [_act("merge_pr", "スイッチを閉じる（マージ）", r, number=pr.number,
                     expected_head_sha=pr.head_sha, expected_base=pr.base),
                _act("close_pr", "スイッチを外す（合流せずに閉じる）", r, danger=True, number=pr.number)]
        out[f"pr:{pr.number}"] = {
            "title": f"スイッチ #{pr.number}「{pr.title}」",
            "term": "プルリクエスト",
            "body": (f"分岐 {pr.head} の変更を幹線に入れてよいか確認するスイッチ。検査ランプは {CI_WORD.get(pr.ci, pr.ci)}。"
                     + ("ショート（コンフリクト）しています。" if pr.mergeable_state == "dirty" else "")
                     + (f"閉じると付箋 #{n} が自動で完了になります。" if n else "説明文に Fixes #番号 が無いので、付箋とはつながっていません。")),
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
            "url": pr.url + "/checks" if pr.url else "",
            "actions": led_acts,
        }
    for issue in view.issues:
        out[f"issue:{issue.number}"] = issue_part(view, issue)
    for part in out.values():
        _attach_commands(part, view)
    return out


CLEANUP_SHOWN = 10


def _cleanup_part(view: RepoView, wd: str) -> dict:
    """幹線に入りきっているのに作業台に残っている分岐。回路図には描かない済んだ分岐を、ここで片付ける。"""
    left = view.local.merged
    stale = view.local.stale_origin
    if not left:
        body = "この一覧で片付け候補になっている手元の分岐はありません。"
    else:
        body = (f"幹線に入りきっているのに、作業台に残っている分岐が {len(left)} 本あります。中身はすべて幹線にあるので、消しても作業は失われません。"
                "今いる分岐と、別の作業場所（worktree）で使っている分岐は含めていません。"
                "このアプリは作業台に書き込まないので、消すのはあなたのターミナルから行います（「ターミナルから実行」でコマンドをコピーできます）。"
                "幹線に入りきっていない分岐は、git が断ります。"
                + (f"ここには新しい順に {CLEANUP_SHOWN} 本を出しています。片付けると次の分岐が出ます。" if len(left) > CLEANUP_SHOWN else ""))
    if stale:
        names = "、".join(stale[:CLEANUP_SHOWN]) + ("…" if len(stale) > CLEANUP_SHOWN else "")
        body += (f"また、作業台にある GitHub のコピー（origin/…）に、GitHub にはもう無い古い記録が {len(stale)} 個あります（{names}）。"
                 "GitHub 側で分岐を撤去しても、手元の記録は自動では消えません。1つのコマンドでまとめて片付きます（手元の分岐やファイルは変わりません）。")
    completed = [b for b in view.branches if view.completed_branch(b.name)]
    if completed:
        body += "GitHub にも合流済みの分岐が残っています: " + "、".join(b.name for b in completed) + "。撤去操作はブレーカーで確認します。"
    body += stale_note(view)
    body += "この部品の対象は分岐・GitHubの追跡記録です。残っているフォルダとファイルは作業場所（worktree）タブで別に確認してください。"
    acts: list[dict] = []
    for b in left[:CLEANUP_SHOWN]:
        notes = ("・GitHub にも同名の分岐が残っています" if b.on_github else "") + ("・作ったばかりで、まだ何もはんだ付けしていないかもしれません" if b.fresh else "")
        acts += _term(f"{b.name}（最終 {b.date:%Y-%m-%d}）を消す{notes}", delete_local(wd, b.name))
    if stale:
        acts += _term("古い GitHub のコピー（origin/…）をまとめて掃除する", prune_origin(wd))
    for b in completed:
        acts.append(_act("delete_branch", f"合流済みの分岐 {b.name} を GitHub から撤去する", view.name, danger=True, name=b.name))
    acts += _fetch_act(view, wd)
    return {
        "title": "作業台の片付け",
        "term": "ローカルブランチ（幹線に入りきり済み）",
        "body": body,
        "actions": acts,
    }


def _attach_commands(part: dict, view: RepoView) -> None:
    """各操作ボタンに「コマンドで言うと」の文字列を付ける。入力欄は既定値で埋めた形（実際の値は確認画面で見える）。"""
    for a in part.get("actions", []):
        if a.get("terminal"):
            continue
        defaults = {f["name"]: f["value"] for f in a["fields"]}
        a["cmd"] = command(a["action"], {**a["params"], **defaults}, view.default_branch, view.local.dev_dir)


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
        "url": issue.url,
        "actions": acts,
    }
