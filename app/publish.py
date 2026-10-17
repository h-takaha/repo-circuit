"""本番までの案内。状態を読むだけで、GitHubの操作は既存Actionsへ渡す。"""

from .commands import default_workdir, fetch_origin, publish_local, safe_ref
from .explain import _act, _attach_commands, _term
from .issues import branch_linked_issue
from .model import RepoView
from .pipeline import _prod


def guide(view: RepoView, state: dict) -> dict:
    loc, branch = view.local, state.get("branch") or ""
    out = {"title": "変更を記録して本番まで届ける", "term_label": "作業台では ",
           "term": f"{branch or '分岐なし'} · {loc.dev_dir}", "guided": True, "repo": view.name,
           "actions": [], "body": "", "phase": "blocked"}

    def phase(name, body, actions=None):
        out.update(phase=name, body=body, actions=actions or [])
        _attach_commands(out, view)
        return out

    def local(operation, label, **extra):
        return {"terminal": True, "workbench": operation, "label": label, "cmd": "", "fields": [],
                "params": {"repo": view.name, "expected_branch": branch, "expected_head": state["head"]}, **extra}

    if not loc.dev_exists or state.get("error"):
        return phase("unknown", state.get("error") or "このPCに作業台がありません。")
    if loc.dev_head != state.get("head") or loc.dev_branch != branch:
        return phase("unknown", "確認中に作業台の位置が変わりました。状態を読み直してください。")
    if not branch or not safe_ref(branch) or branch == view.default_branch:
        return phase("branch", "先に付箋から修正用の分岐を引き、作業台をその分岐へ切り替えてください。幹線へ直接コミット・pushする案内は出しません。")
    if state.get("conflicted"):
        return phase("conflict", "ショート（コンフリクト）を解消してから、状態を読み直してください。")

    if state["staged"]:
        out["files"] = state["staged"]
        out["index"] = state.get("index", "")
        actions = [local("commit", "選択済みの変更を記録する", fields=[
            {"name": "message", "label": "変更の名前（コミットメッセージ）", "value": ""}])]
        if state["unstaged"]:
            actions.append(local("stage", "追加の変更を記録対象に加える", files=state["unstaged"]))
        return phase("commit", "2. はんだ付けする（コミット）。選択済みのファイルを確認して、変更内容が分かる名前を付けます。未選択の変更はこのコミットに入りません。自分で順番に操作するため、名前に [no-push] [no-deploy] を付け、コミット直後の自動pushを抑えます。",
                     actions)
    if state["unstaged"]:
        return phase("stage", "1. 記録するファイルを選ぶ（git add）。一覧から今回の修正に含めるものだけ選びます。この段では選択をGitに保存するだけで、まだコミットやpushはしません。",
                     [local("stage", "記録するファイルを選ぶ", files=state["unstaged"])])

    lb, gb, pr = view.local_branch(branch), view.branch(branch), view.pull_for(branch)
    if loc.dev_unpushed or (lb and lb.unpushed):
        return phase("push", "3. はんだ点を送電する（push）。記録した変更を同名のGitHub分岐へ送ります。幹線への合流は次のスイッチ操作で行います。",
                     [local("push", "記録した変更をGitHubへ送る")])
    if lb and lb.github_ahead:
        return phase("fetch", "GitHub側が先行しているか、手元に先端の情報がありません。まずfetchして読み直します。まだ先行している場合は、変更内容を確認してから取り込んでください。",
                     _term("最新情報を受け取る（fetch）", fetch_origin(loc.dev_dir or default_workdir(view.name))))
    if pr:
        if pr.head_sha != state["head"]:
            return phase("unknown", "作業台とスイッチの先端が一致しません。最新情報と変更内容を確認してください。")
        if pr.draft or pr.ci not in ("success", "none") or pr.mergeable_state == "dirty":
            out["url"] = pr.url
            return phase("check", "5. 検査ランプを確認する。検査中・失敗・取得不可、下書き、ショートがある場合は先に解消してください。解消したら状態を読み直します。")
        return phase("merge", "6. 合流する（マージ）。合流先と先端、検査と変更内容を確認します。本番反映は別の段階です。自動配布対象では別の処理で更新・再起動される場合があるため、確認画面の注意も読んでください。",
                     [_act("merge_pr", "合流する（マージ）", view.name, number=pr.number,
                           expected_head_sha=pr.head_sha, expected_base=pr.base)])
    merged = view.merged_for(branch)
    if merged and merged.head_sha == state["head"]:
        prod, _ = _prod(view, merged.merge_commit_sha)
        if prod["state"] == "ok":
            return phase("done", "7. 本番まで反映済みです。回路図を再読み込みして本番の旗を確認し、実際の画面で変更を確かめてください。次の作業に備えるときは「作業台を最新の幹線に揃える」を使います。")
        return phase("deploy", "7. 本番の反映を確認する。自動配布対象では定期配布を待って状態を読み直します。本番の旗が追いついたら、実際の画面で変更を確認します。アプリは本番更新を実行しません。")
    if not gb or gb.sha != state["head"]:
        return phase("unknown", "記録済みの変更がGitHubに届いたことを確認できません。送電状況や最新情報を確認してください。")
    if gb.ahead == 0:
        return phase("work", "まだ幹線に入れる新しいはんだ点がありません。修正したら、この案内を開き直してください。")
    number = branch_linked_issue(view, branch)
    issue = view.issue(number) if number else None
    return phase("pr", "4. スイッチを付ける（プルリクエスト）。変更の名前と説明を入力します。付箋を完了につなぐときは説明に Fixes #番号 を書きます。作成後、検査ランプと合流へ進みます。",
                 [_act("create_pr", "スイッチを付ける（プルリクエスト作成）", view.name, head=branch, fields=[
                     {"name": "title", "label": "スイッチの名前（PRタイトル）", "value": issue.title if issue else branch},
                     {"name": "body", "label": "説明（Fixes #番号で付箋とつながる）", "value": f"Fixes #{number}" if issue else "", "multiline": True}])])


def local_command(view: RepoView, state: dict, data: dict) -> str:
    """コピー直前に現在の分岐・先端・選択可能ファイルを確認する。"""
    if state.get("error") or state.get("conflicted"):
        raise ValueError(state.get("error") or "ショートを解消してから読み直してください。")
    branch, head = state.get("branch"), state.get("head")
    if (not branch or branch == view.default_branch or branch != data.get("expected_branch")
            or head != data.get("expected_head")):
        raise ValueError("作業台の位置が変わったか、幹線を開いています。状態を読み直してください。")
    operation = data.get("operation")
    files, message = data.get("files"), data.get("message", "")
    if operation == "stage":
        if not isinstance(files, list) or not files or any(not isinstance(p, str) or p not in state["unstaged"] for p in files):
            raise ValueError("今回記録するファイルを一覧から選んでください。")
    elif operation == "commit":
        if (not state["staged"] or data.get("expected_files") != state["staged"]
                or not state.get("index") or data.get("expected_index") != state["index"]):
            raise ValueError("選択済みのファイルが変わりました。内容を読み直してください。")
        if not isinstance(message, str) or not message.strip() or len(message) > 500:
            raise ValueError("変更の名前を1〜500文字で入力してください。")
    elif operation == "push":
        if guide(view, state)["phase"] != "push":
            raise ValueError("いまは送電の段ではありません。状態を読み直してください。")
    else:
        raise ValueError("未対応の作業台操作です。")
    cmd = publish_local(view.local.dev_dir or default_workdir(view.name), branch, head, operation, files, message,
                        state.get("index", ""))
    if not cmd:
        raise ValueError("安全なコマンドを生成できませんでした。")
    return cmd
