"""工程表: 分岐ごとに「はんだ付け → 送電 → スイッチ → 検査 → 合流 → 本番」のどこまで来たかを並べる。

作業台（この PC でしか分からない未コミット・未 push）と GitHub（スイッチ・検査・合流）と
本番（稼働クローンの位置）を1行にまとめる。状態は読むだけで、操作は部品パネルから行う。
"""

from __future__ import annotations

from .model import RepoView

STAGES = [
    ("commit", "はんだ付け", "コミット"),
    ("push", "送電", "push"),
    ("switch", "スイッチ", "プルリクエスト"),
    ("check", "検査", "CI"),
    ("merge", "合流", "マージ"),
    ("prod", "本番", "デプロイ"),
]
MAX_ROWS = 8
MAX_MERGED = 3

# 段の状態: ok=済、todo=あなたの番、wait=待ち、bad=止まっている、na=分からない、off=まだ
_CI = {
    "success": ("ok", "合格"),
    "failure": ("bad", "失敗"),
    "pending": ("wait", "検査中"),
    "unavailable": ("na", "取得不可"),
    "none": ("ok", "検査なし"),
}


def _stage(state: str = "off", text: str = "") -> dict:
    return {"state": state, "text": text}


def local_totals(view: RepoView) -> tuple[int, int]:
    """作業台の（未コミットのファイル数, 未送電のはんだ点の数）。"""
    loc = view.local
    unpushed = sum(b.unpushed for b in loc.branches)
    if loc.dev_branch == view.default_branch:
        unpushed += loc.dev_unpushed
    return loc.dev_uncommitted, unpushed


def rows(view: RepoView) -> list[dict]:
    loc = view.local
    out: list[dict] = []

    if loc.dev_branch == view.default_branch and (loc.dev_uncommitted or loc.dev_unpushed):
        out.append(_main_row(view))

    names: list[str] = []
    if loc.dev_branch and loc.dev_branch != view.default_branch:
        names.append(loc.dev_branch)
    names += [b.name for b in loc.branches if b.name not in names]
    names += [b.name for b in view.branches if b.name not in names]
    merged_heads = {p.head for p in view.closed_pulls if p.merged}
    for name in names:
        gb, lb = view.branch(name), view.local_branch(name)
        if (name in merged_heads and name != loc.dev_branch and (gb is None or gb.ahead == 0)
                and not (lb and lb.unpushed) and not view.pull_for(name)):
            continue  # 合流済みで新しいはんだ点の無い分岐（撤去済みも含む）は、下の「合流済み」の行で見せる
        row = _branch_row(view, name)
        if row:
            out.append(row)

    merged = [p for p in view.closed_pulls if p.merged][:MAX_MERGED]
    out += [_merged_row(view, p) for p in merged]
    return out[:MAX_ROWS]


def _main_row(view: RepoView) -> dict:
    loc = view.local
    st = {k: _stage() for k, *_ in STAGES}
    st["commit"] = _stage("todo", f"未コミット {loc.dev_uncommitted}") if loc.dev_uncommitted else _stage("ok", "済")
    if loc.dev_unpushed:
        st["push"] = _stage("todo", f"未送電 {loc.dev_unpushed}")
    return {
        "name": f"幹線 {view.default_branch}",
        "part": "dev",
        "here": True,
        "stages": _ordered(st),
        "next": "幹線で直接作業しています。分岐を作ってそちらに移し、スイッチ経由で合流しましょう",
        "warn": True,
    }


def _branch_row(view: RepoView, name: str) -> dict | None:
    loc = view.local
    lb = view.local_branch(name)
    gb = view.branch(name)
    pr = view.pull_for(name)
    here = loc.dev_branch == name
    if lb and not gb and not lb.unpushed and not here:
        return None  # 送るものが無く GitHub にも無い（合流済みで撤去された分岐の残り）

    st = {k: _stage() for k, *_ in STAGES}
    nxt = ""
    if here and loc.dev_uncommitted:
        st["commit"] = _stage("todo", f"未コミット {loc.dev_uncommitted}")
        nxt = "作業台で変更をコミットする（はんだ付け）"
    else:
        st["commit"] = _stage("ok", "済")

    if lb and lb.unpushed:
        st["push"] = _stage("todo", f"未送電 {lb.unpushed}" + ("" if lb.on_github else " · 作業台のみ"))
        nxt = nxt or f"GitHub へ送る（git push{'' if lb.on_github else ' -u origin ' + name}）"
    elif lb and lb.github_ahead:
        st["push"] = _stage("na", "GitHub が先行")
        nxt = nxt or "GitHub 側に新しいはんだ点があります。作業台に取り込む（git pull）"
    elif gb or (lb and lb.on_github):
        st["push"] = _stage("ok", "済" if lb else "GitHub のみ")
    elif lb:
        st["push"] = _stage("off", "送るはんだ点なし")  # 幹線から分けただけ、または合流後に GitHub から撤去された分岐

    if pr:
        st["switch"] = _stage("ok", f"#{pr.number}")
        st["check"] = _stage(*_CI.get(pr.ci, ("na", pr.ci)))
        if pr.mergeable_state == "dirty":
            st["merge"] = _stage("bad", "ショート")
            nxt = nxt or "ショート（コンフリクト）を解消する"
        elif pr.ci in ("success", "none") and not pr.draft:
            st["merge"] = _stage("todo", "閉じられる")
            nxt = nxt or f"スイッチ #{pr.number} を閉じる（マージ）"
        else:
            nxt = nxt or {"failure": "検査ランプの失敗ログを見て、分岐に修正をはんだ付けする",
                          "pending": "検査が終わるのを待つ",
                          "unavailable": "検査結果を読める権限を用意する"}.get(pr.ci, "下書きを仕上げる")
    elif gb and gb.ahead == 0 and not (lb and lb.unpushed):
        nxt = nxt or "幹線に無いはんだ点がありません。はんだ付けするか、要らなければ分岐を撤去する"
    elif gb:
        st["switch"] = _stage("todo", "未")
        nxt = nxt or "スイッチを付ける（プルリクエスト作成）"

    part = f"pr:{pr.number}" if pr else (f"branch:{name}" if gb else f"local:{name}")
    return {"name": name, "part": part, "here": here, "stages": _ordered(st), "next": nxt or "—", "warn": False}


def _merged_row(view: RepoView, pr) -> dict:
    st = {k: _stage("ok", "済") for k, *_ in STAGES}
    st["switch"] = _stage("ok", f"#{pr.number}")
    st["check"] = _stage("ok", "—")
    st["prod"], nxt = _prod(view, pr.merge_commit_sha)
    part = f"commit:{pr.merge_commit_sha}" if any(c.sha == pr.merge_commit_sha for c in view.main_commits) else "run"
    return {"name": pr.head, "part": part, "here": False, "stages": _ordered(st), "next": nxt, "warn": False, "merged": True}


def _prod(view: RepoView, sha: str) -> tuple[dict, str]:
    loc = view.local
    shas = [c.sha for c in view.main_commits]
    idx = shas.index(sha) if sha in shas else None
    behind = view.run_behind
    wait = "本番の自動デプロイを待つ" if view.autodeploy else "本番へ反映する（手動デプロイ）"
    if not loc.run_exists:
        return _stage("na", "本番なし"), "この PC に本番（稼働クローン）がありません"
    if not sha:
        return _stage("na", "不明"), "本番の位置を確認する"
    if behind is None:
        return (_stage("wait", "反映待ち"), wait) if idx is not None else (_stage("na", "不明"), "本番の位置を確認する")
    if idx is None or idx <= len(shas) - 1 - behind:
        return _stage("ok", "反映済"), "完了"
    return _stage("wait", "反映待ち"), wait


def _ordered(st: dict[str, dict]) -> list[dict]:
    return [{"key": k, "label": label, "term": term, **st[k]} for k, label, term in STAGES]
