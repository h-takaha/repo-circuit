"""デモモード（GITHUB_TOKEN 未設定）用のサンプル回路。書き込みはメモリ上で擬似実行する。"""

from __future__ import annotations

import copy
import hashlib
from datetime import timedelta

from .model import Branch, Commit, Issue, LocalBranch, LocalState, MergedBranch, PullRequest, RepoView, now


def _sha(seed: str) -> str:
    return hashlib.sha1(seed.encode()).hexdigest()


def _commits(repo: str, prefix: str, messages: list[str], start_days: int) -> list[Commit]:
    t = now()
    return [
        Commit(sha=_sha(f"{repo}:{prefix}:{i}:{m}"), message=m, date=t - timedelta(days=start_days - i, hours=i))
        for i, m in enumerate(messages)
    ]


def _issue(repo: str, n: int, title: str, kind: str, pri: str, state: str = "open", days: int = 3) -> Issue:
    return Issue(
        repo=repo,
        number=n,
        title=title,
        body=f"## 気づいた状況\n{title}\n\n_起票元: web_",
        state=state,
        labels=["feedback", kind, pri],
        created_at=now() - timedelta(days=days),
        url=f"https://github.com/h-takaha/{repo}/issues/{n}",
    )


def _branch(repo: str, name: str, base: Commit, msgs: list[str], behind: int, idle_days: int) -> Branch:
    commits = _commits(repo, name, msgs, idle_days + len(msgs))
    for c in commits:
        c.date = now() - timedelta(days=idle_days)
    return Branch(
        name=name,
        sha=commits[-1].sha if commits else base.sha,
        ahead=len(commits),
        behind=behind,
        commits=commits,
        merge_base=base.sha,
        last_date=commits[-1].date if commits else base.date,
    )


def _pr(repo: str, n: int, title: str, head: str, sha: str, ci: str, fixes: int | None, ms: str = "clean") -> PullRequest:
    body = f"Fixes #{fixes}" if fixes else ""
    return PullRequest(
        number=n, title=title, body=body, head=head, base="main", state="open", merged=False, draft=False,
        head_sha=sha, ci=ci, mergeable_state=ms, url=f"https://github.com/h-takaha/{repo}/pull/{n}",
    )


def build_demo() -> dict[str, RepoView]:
    views: dict[str, RepoView] = {}

    r = "event-calendar"
    main = _commits(r, "main", ["初期構成", "スクレイパーを分離", "祝日を反映 (#11)", "日付の表示を修正", "依存を更新", "ログ出力を整理"], 40)
    views[r] = RepoView(
        name=r, default_branch="main", main_commits=main,
        branches=[
            _branch(r, "exp/old-layout", main[1], ["レイアウト試作", "カード表示の試作"], 4, 32),
            _branch(r, "fix/14-csv-encoding", main[2], ["CSV を UTF-8 BOM 付きで出力", "テスト追加"], 3, 1),
            _branch(r, "fix/12-button-size", main[3], ["ボタンを大きく", "余白を調整", "スマホ幅を確認"], 2, 0),
        ],
        pulls=[],
        issues=[
            _issue(r, 12, "追加ボタンが小さくて押しにくい", "improvement", "p2"),
            _issue(r, 14, "CSV 出力が文字化けする", "bug", "p1"),
            _issue(r, 15, "夜間更新のログが見づらい", "improvement", "p3"),
            _issue(r, 10, "祝日が反映されない", "bug", "p1", state="closed", days=20),
        ],
        closed_pulls=[
            PullRequest(9, "ログ出力を整理", "", "fix/9-log-format", "main", "closed", True, False, _sha("y"), "none", "unknown",
                        merge_commit_sha=main[-1].sha),
            PullRequest(11, "祝日を反映", "Fixes #10", "fix/10-holiday", "main", "closed", True, False, _sha("x"), "none", "unknown",
                        merge_commit_sha=main[2].sha),
        ],
        local=LocalState(dev_exists=True, run_exists=True, run_head=main[-2].sha, run_dirty=False),
        autodeploy=True,
        url=f"https://github.com/h-takaha/{r}",
    )
    b = views[r].branches
    # 作業台: CSV の分岐で作業中（未コミット 2）。ボタンの分岐は未送電 1、GitHub にまだ無い試作の分岐が1本
    unsent = _commits(r, "local:fix/12-button-size", ["押下時の色を調整"], 0)
    idea = _commits(r, "local:exp/local-idea", ["週表示の試作", "週表示のテスト"], 1)
    views[r].local = LocalState(
        dev_exists=True, dev_head=b[1].sha, dev_branch="fix/14-csv-encoding", dev_uncommitted=2,
        run_exists=True, run_head=main[-2].sha,
        branches=[
            LocalBranch("fix/14-csv-encoding", b[1].sha, on_github=True),
            LocalBranch("fix/12-button-size", unsent[-1].sha, unpushed=1, on_github=True, commits=unsent),
            LocalBranch("exp/local-idea", idea[-1].sha, unpushed=2, commits=idea, base=main[-1].sha, last_date=idea[-1].date),
        ],
        merged=[
            MergedBranch("fix/9-old-icon", main[-3].sha, main[-3].date),
            MergedBranch("fix/11-typo", main[-2].sha, main[-2].date, on_github=True),
        ],
        stale_origin=["fix/2-old-label"],
    )
    views[r].pulls = [
        _pr(r, 5, "追加ボタンを押しやすくする", "fix/12-button-size", b[2].sha, "success", 12),
        _pr(r, 6, "CSV の文字化けを直す", "fix/14-csv-encoding", b[1].sha, "failure", 14),
    ]

    r = "investment-tracker"
    main = _commits(r, "main", ["銘柄一覧", "銘柄検索を高速化 (#28)", "評価額グラフ", "日次更新の修正"], 20)
    br = _branch(r, "fix/31-rounding", main[-1], ["端数を小数2桁にそろえる"], 0, 0)
    views[r] = RepoView(
        name=r, default_branch="main", main_commits=main, branches=[br],
        pulls=[_pr(r, 32, "評価額の端数表示をそろえる", br.name, br.sha, "success", 31)],
        issues=[
            _issue(r, 31, "評価額の端数表示をそろえたい", "improvement", "p2"),
            _issue(r, 33, "配当の履歴も見たい", "idea", "p3"),
            _issue(r, 27, "銘柄検索を速くしたい", "improvement", "p2", state="closed", days=15),
        ],
        local=LocalState(dev_exists=True, dev_head=main[-1].sha, run_exists=True, run_head=main[-1].sha),
        autodeploy=True, url=f"https://github.com/h-takaha/{r}",
    )

    r = "hotel-watcher"
    main = _commits(r, "main", ["監視を追加", "通知を整形", "価格の比較"], 60)
    views[r] = RepoView(
        name=r, default_branch="main", main_commits=main,
        branches=[_branch(r, "exp/new-parser", main[0], ["新しいパーサ"], 2, 45)],
        pulls=[], issues=[_issue(r, 3, "通知の時間帯を選びたい", "improvement", "p2", days=21)],
        local=LocalState(dev_exists=True, dev_head=main[-1].sha, run_exists=True, run_head=main[-1].sha),
        autodeploy=True, url=f"https://github.com/h-takaha/{r}",
    )

    r = "deck-studio"
    main = _commits(r, "main", ["スライド生成", "テーマ切替", "書き出し修正"], 12)
    br = _branch(r, "feat/export-pdf", main[-1], ["PDF 書き出し"], 0, 0)
    views[r] = RepoView(
        name=r, default_branch="main", main_commits=main, branches=[br],
        pulls=[_pr(r, 9, "PDF で書き出せるようにする", br.name, br.sha, "pending", None)],
        issues=[_issue(r, 8, "PDF で書き出したい", "idea", "p2")],
        local=LocalState(dev_exists=True, dev_head=main[-1].sha, run_exists=True, run_head=main[-1].sha),
        autodeploy=True, url=f"https://github.com/h-takaha/{r}",
    )

    r = "file-converter"
    main = _commits(r, "main", ["変換の基本", "画像対応", "バッチ変換"], 30)
    views[r] = RepoView(
        name=r, default_branch="main", main_commits=main, branches=[], pulls=[],
        issues=[_issue(r, 8, "PDF の向きが保存されない", "bug", "p2", days=7), _issue(r, 9, "変換履歴を残したい", "idea", "p3", days=14)],
        local=LocalState(dev_exists=True, dev_head=main[-1].sha, run_exists=True, run_head=main[-1].sha),
        autodeploy=True, url=f"https://github.com/h-takaha/{r}",
    )

    r = "job-watcher"
    main = _commits(r, "main", ["求人の監視", "通知", "除外条件"], 25)
    views[r] = RepoView(
        name=r, default_branch="main", main_commits=main, branches=[], pulls=[], issues=[],
        local=LocalState(dev_exists=True, dev_head=main[-1].sha, run_exists=True, run_head=main[-1].sha),
        autodeploy=True, url=f"https://github.com/h-takaha/{r}",
    )
    return views


class DemoSource:
    """GitHubSource と同じ口を持つ、メモリ上の擬似 GitHub。"""

    def __init__(self) -> None:
        self.views = build_demo()
        self.comments: list[tuple[str, int, str]] = []

    def invalidate(self, repo: str | None = None) -> None:  # キャッシュは無い
        return None

    async def repo_names(self) -> list[str]:
        return list(self.views)

    async def repo_view(self, name: str) -> RepoView:
        if name not in self.views:
            raise KeyError(name)
        return copy.deepcopy(self.views[name])

    def _v(self, repo: str) -> RepoView:
        return self.views[repo]

    async def create_issue(self, repo: str, title: str, body: str, labels: list[str]) -> dict:
        v = self._v(repo)
        numbers = [i.number for i in v.issues] + [p.number for p in v.pulls + v.closed_pulls]
        n = max(numbers, default=0) + 1
        issue = Issue(repo=repo, number=n, title=title, body=body, state="open", labels=labels, created_at=now(),
                      url=f"https://github.com/h-takaha/{repo}/issues/{n}")
        v.issues.insert(0, issue)
        return {"number": n, "html_url": issue.url}

    async def set_issue_state(self, repo: str, number: int, state: str) -> dict:
        issue = self._v(repo).issue(number)
        if issue:
            issue.state = state
        return {"number": number}

    async def comment(self, repo: str, number: int, body: str) -> dict:
        self.comments.append((repo, number, body))
        return {"id": len(self.comments)}

    async def create_branch(self, repo: str, name: str, sha: str) -> dict:
        v = self._v(repo)
        base = next((c for c in v.main_commits if c.sha == sha), v.main_commits[-1])
        v.branches.append(Branch(name=name, sha=base.sha, ahead=0, behind=0, commits=[], merge_base=base.sha, last_date=now()))
        return {"ref": f"refs/heads/{name}"}

    async def delete_branch(self, repo: str, name: str) -> None:
        v = self._v(repo)
        v.branches = [b for b in v.branches if b.name != name]

    async def create_pr(self, repo: str, head: str, base: str, title: str, body: str) -> dict:
        v = self._v(repo)
        numbers = [i.number for i in v.issues] + [p.number for p in v.pulls + v.closed_pulls]
        n = max(numbers, default=0) + 1
        b = v.branch(head)
        v.pulls.append(PullRequest(n, title, body, head, base, "open", False, False, b.sha if b else "", "success", "clean",
                                   url=f"https://github.com/h-takaha/{repo}/pull/{n}"))
        return {"number": n, "html_url": v.pulls[-1].url}

    async def merge_pr(self, repo: str, number: int) -> dict:
        from .issues import linked_issue

        v = self._v(repo)
        pr = next(p for p in v.pulls if p.number == number)
        b = v.branch(pr.head)
        if b:
            v.main_commits.extend(b.commits)
            v.main_commits.append(Commit(sha=_sha(f"merge:{number}"), message=f"Merge pull request #{number}", date=now()))
            pr.merge_commit_sha = v.main_commits[-1].sha
            b.commits, b.ahead, b.behind = [], 0, 0
        pr.state, pr.merged = "closed", True
        v.pulls.remove(pr)
        v.closed_pulls.insert(0, pr)
        n = linked_issue(pr)
        if n and v.issue(n):
            v.issue(n).state = "closed"
        return {"merged": True}

    async def close_pr(self, repo: str, number: int) -> dict:
        v = self._v(repo)
        pr = next(p for p in v.pulls if p.number == number)
        pr.state = "closed"
        v.pulls.remove(pr)
        v.closed_pulls.insert(0, pr)
        return {"number": number}

    async def rerun_failed(self, repo: str, head_sha: str) -> int:
        for p in self._v(repo).pulls:
            if p.head_sha == head_sha and p.ci == "failure":
                p.ci = "pending"
                return 1
        return 0
