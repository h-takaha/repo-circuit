"""ブレーカー（ガードレール）。書き込み操作の前に必ず evaluate() を通す。

各ルールは「どの操作に」「どんな条件で」「トリップ(止める) / 注意(確認すれば進める)」かと、
なぜダメか・代わりに何をするかをデータとして持つ。文言は初めて GitHub を使う人向け。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Callable

from .issues import fixes_numbers, merged_fix_for
from .model import RepoView

TRIP = "trip"
WARN = "warn"

ACTIONS = {
    "create_issue": "付箋を貼る（Issue 作成）",
    "comment_issue": "付箋にメモを足す（コメント）",
    "close_issue": "付箋を完了にする（Issue クローズ）",
    "reopen_issue": "付箋を戻す（Issue 再オープン）",
    "create_branch": "分岐を引く（ブランチ作成）",
    "delete_branch": "分岐を撤去する（ブランチ削除）",
    "create_pr": "スイッチを付ける（プルリクエスト作成）",
    "merge_pr": "スイッチを閉じる（マージ）",
    "close_pr": "スイッチを外す（プルリクエストを閉じる）",
    "rerun_ci": "検査をやり直す（CI 再実行）",
}

TEXT_FIELDS = ("title", "situation", "want", "note", "body")

SECRET_PATTERNS = [
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\s*[:=]\s*\S{4,}"),
    re.compile(r"(パスワード|暗証番号|トークン)\s*[:=：は]\s*\S{4,}"),
]

_BRANCH_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


@dataclass
class Verdict:
    id: str
    level: str
    rule: str
    why: str
    steps: list[str] = field(default_factory=list)
    cmd: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Rule:
    id: str
    level: str
    actions: tuple[str, ...]
    when: Callable[[dict, RepoView], bool]
    rule: str
    why: str
    steps: tuple[str, ...] = ()
    cmd: str = ""


def _pr(p: dict, v: RepoView):
    n = int(p.get("number") or 0)
    return next((x for x in v.pulls if x.number == n), None)


def _merge_branch(p: dict, v: RepoView):
    pr = _pr(p, v)
    return v.branch(pr.head) if pr else None


def _has_secret(p: dict, v: RepoView) -> bool:
    text = "\n".join(str(p.get(k, "")) for k in TEXT_FIELDS)
    return any(rx.search(text) for rx in SECRET_PATTERNS)


def _bad_branch_name(p: dict, v: RepoView) -> bool:
    name = str(p.get("name", ""))
    return (
        not _BRANCH_OK.match(name)
        or ".." in name
        or "//" in name
        or name.endswith(("/", ".", ".lock"))
        or len(name) > 100
    )


def _run_lagging(p: dict, v: RepoView) -> bool:
    if not v.local.run_exists:
        return False
    lag = v.run_behind
    return lag is None or lag >= 3


RULES: list[Rule] = [
    # ---- 全書き込み ----
    Rule(
        "secret", TRIP, tuple(ACTIONS), _has_secret,
        "パスワードやトークンらしき文字列は書けません",
        "GitHub に書いた内容は履歴に残り、あとから消しても通知メールや他人の手元に残ることがあります。漏れたトークンは悪用されます。",
        ("値そのものは書かず「.env の BASIC_AUTH_PASSWORD が効いていない」のように名前だけ書く",
         "もし本物の値を書いてしまったら、その値は無効化（作り直し）する"),
    ),
    # ---- 合流 ----
    Rule(
        "merge_ci_unavailable", TRIP, ("merge_pr",), lambda p, v: bool(_pr(p, v)) and _pr(p, v).ci == "unavailable",
        "検査ランプが取得不可のスイッチは閉じられません",
        "GitHub の検査結果を取得できないため、安全に合流できるか確認できません。検査なしとは異なり、確認してもこのアプリからはマージできません。",
        ("GitHub で検査結果とトークンの Checks 読み取り権限を確認する", "検査結果を取得できるようになったら、画面を再読み込みする"),
    ),
    Rule(
        "merge_ci_failure", TRIP, ("merge_pr",), lambda p, v: bool(_pr(p, v)) and _pr(p, v).ci == "failure",
        "検査ランプが赤いスイッチは閉じられません",
        "自動チェック（CI）が失敗しています。壊れているかもしれない変更を幹線に入れると、幹線につながった本番まで壊れます。",
        ("赤ランプをクリックして、どの検査が失敗したか読む", "分岐に修正をはんだ付け（コミット）する", "ランプが緑になったら、もう一度スイッチを閉じる"),
        "gh pr checks {number}",
    ),
    Rule(
        "merge_ci_pending", TRIP, ("merge_pr",), lambda p, v: bool(_pr(p, v)) and _pr(p, v).ci == "pending",
        "検査中のスイッチは閉じられません",
        "自動チェックがまだ終わっていません。結果を見ずに合流すると、壊れた変更が本番に届くかもしれません。",
        ("数分待って、ランプが緑になるのを確認する",),
        "gh pr checks {number} --watch",
    ),
    Rule(
        "merge_conflict", TRIP, ("merge_pr",), lambda p, v: bool(_pr(p, v)) and _pr(p, v).mergeable_state == "dirty",
        "ショート（コンフリクト）したままでは閉じられません",
        "幹線とこの分岐が、同じファイルの同じ場所を別々に変えています。どちらを残すか人が決めるまで、GitHub は合流できません。",
        ("作業台で分岐に幹線を取り込み（git merge main）、ぶつかった箇所を直す", "直したものをはんだ付けして GitHub に送る", "ショートが消えたらスイッチを閉じる"),
        "git switch {head} && git merge origin/main",
    ),
    Rule(
        "merge_draft", TRIP, ("merge_pr",), lambda p, v: bool(_pr(p, v)) and _pr(p, v).draft,
        "下書き中のスイッチは閉じられません",
        "このプルリクエストは「まだ途中」の印（Draft）が付いています。",
        ("作業が終わったら GitHub で Ready for review にする",),
        "gh pr ready {number}",
    ),
    Rule(
        "merge_no_ci", WARN, ("merge_pr",), lambda p, v: bool(_pr(p, v)) and _pr(p, v).ci == "none",
        "この回路には検査ランプがありません",
        "自動チェック（CI）が設定されていないので、壊れていないかは自分で確かめる必要があります。",
        ("作業台で動かして確認してから閉じる",),
    ),
    Rule(
        "merge_autodeploy", WARN, ("merge_pr",), lambda p, v: v.autodeploy,
        "閉じると本番が自動で更新されます",
        "このツールは自動デプロイ対象です。幹線に合流すると、数分以内に本番（C:\\lab\\run）が新しい版に入れ替わり、再起動することがあります。",
        ("いまこのツールを使っている最中なら、使い終わってから閉じる",),
    ),
    Rule(
        "merge_behind", WARN, ("merge_pr",), lambda p, v: bool(_merge_branch(p, v)) and _merge_branch(p, v).behind >= 10,
        "幹線から大きく遅れた分岐です",
        "分岐を引いた後に幹線がかなり進んでいます。古い土台の上で作った変更なので、合流後に思わぬ組み合わせで壊れることがあります。",
        ("先に分岐へ幹線を取り込み、動作を確認してから閉じる",),
    ),
    Rule(
        "merge_run_lagging", WARN, ("merge_pr",), _run_lagging,
        "本番が幹線に追いついていません",
        "本番マーカーが幹線の先端から遅れています（自動デプロイが止まっている疑い）。さらに合流すると、本番に届いていない変更が積み上がります。",
        ("C:\\lab\\git\\agent-ops\\automation の LAST_FAILURE やログを確認する",),
    ),
    # ---- 分岐の撤去 ----
    Rule(
        "delete_default", TRIP, ("delete_branch",), lambda p, v: p.get("name") == v.default_branch,
        "幹線は撤去できません",
        "幹線（main）は全員の土台で、本番につながっています。消すと repo 全体が壊れます。",
    ),
    Rule(
        "delete_open_pr", TRIP, ("delete_branch",), lambda p, v: v.pull_for(str(p.get("name"))) is not None,
        "スイッチが付いた分岐は撤去できません",
        "この分岐には開いているプルリクエストがあります。分岐だけ消すと、スイッチが宙に浮きます。",
        ("先にスイッチを閉じる（合流）か、スイッチを外す（プルリクエストを閉じる）",),
    ),
    Rule(
        "delete_unmerged", TRIP, ("delete_branch",),
        lambda p, v: bool(v.branch(str(p.get("name")))) and v.branch(str(p.get("name"))).ahead > 0,
        "合流していないはんだ点がある分岐は撤去できません",
        "この分岐には、幹線にまだ入っていないはんだ点（コミット）があります。撤去するとこの作業が GitHub から消え、取り戻すのが難しくなります。",
        ("はんだ点をクリックして、何を変えたか確認する",
         "必要な変更なら、スイッチ（プルリクエスト）を付けて幹線へ合流する",
         "本当に不要なら、理由を付箋に書いて残してから、ターミナルで明示的に削除する（このアプリでは消せません）"),
        "git log origin/main..origin/{name}",
    ),
    # ---- スイッチを付ける ----
    Rule(
        "pr_no_diff", TRIP, ("create_pr",),
        lambda p, v: bool(v.branch(str(p.get("head")))) and v.branch(str(p.get("head"))).ahead == 0,
        "まだ何もはんだ付けしていない分岐です",
        "幹線との違いが無いので、取り込むものがありません。",
        ("作業台でこの分岐に切り替えて直し、コミットして GitHub に送る",),
        "git switch {head}",
    ),
    Rule(
        "pr_exists", TRIP, ("create_pr",), lambda p, v: v.pull_for(str(p.get("head"))) is not None,
        "この分岐にはもうスイッチが付いています",
        "1本の分岐に付けられるスイッチ（開いているプルリクエスト）は1つです。",
    ),
    Rule(
        "pr_no_fixes", WARN, ("create_pr",), lambda p, v: not fixes_numbers(str(p.get("body", ""))),
        "付箋とつながっていません",
        "説明文に「Fixes #番号」が無いので、スイッチを閉じても付箋は自動で完了になりません。",
        ("直す付箋があるなら、説明文に「Fixes #番号」を書く",),
    ),
    # ---- 付箋を閉じる ----
    Rule(
        "close_without_fix", WARN, ("close_issue",),
        lambda p, v: merged_fix_for(v, int(p.get("number") or 0)) is None,
        "直した記録が無いまま付箋を閉じようとしています",
        "この付箋につながった合流済みのスイッチがありません。直さずに閉じると「なぜ閉じたか」を後で思い出せません。",
        ("閉じる前にメモ（コメント）で理由を書く（例: 仕様どおりだった / 別の付箋にまとめた）",),
    ),
    # ---- 分岐を引く ----
    Rule(
        "branch_bad_name", TRIP, ("create_branch",), _bad_branch_name,
        "その分岐名は使えません",
        "分岐名には英数字と - _ . / だけが使えます（空白・日本語・「..」は不可）。",
        ("例: fix/12-button-size のように「種類/付箋番号-短い英語」にする",),
    ),
    Rule(
        "branch_exists", TRIP, ("create_branch",), lambda p, v: v.branch(str(p.get("name"))) is not None,
        "同じ名前の分岐がもうあります",
        "同じ名前の分岐は2本作れません。",
        ("既存の分岐をそのまま使うか、別の名前にする",),
    ),
]


def evaluate(action: str, params: dict, view: RepoView) -> list[Verdict]:
    out: list[Verdict] = []
    fmt = {k: v for k, v in params.items()}
    pr = _pr(params, view)
    if pr:
        fmt.setdefault("head", pr.head)
    for r in RULES:
        if action not in r.actions:
            continue
        try:
            hit = r.when(params, view)
        except (TypeError, ValueError, AttributeError):
            hit = False
        if hit:
            out.append(Verdict(r.id, r.level, r.rule, r.why, list(r.steps), _safe_format(r.cmd, fmt)))
    return out


def _safe_format(template: str, values: dict) -> str:
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template
