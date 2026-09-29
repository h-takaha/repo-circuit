"""RepoView を配線図の部品配置（座標つき）に変換する。描画はテンプレートが行う。

配置の規則（交差させないため）:
- 幹線（main）は横一直線。はんだ点は左から古い順。右端は本番ランプ。
- 分岐は「引き出し位置が左のものほど深い段」に置き、「戻り口は右のものほど深い段」にする。
  こうすると分岐の配線は入れ子になり、互いに交差しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .issues import branch_linked_issue
from .model import Branch, RepoView

WIDTH = 780
BUS_Y = 100
LANE_GAP = 96
MAX_LANES = 6
MAIN_LEFT, MAIN_RIGHT = 44, 440
LAMP_X = 746
RETURN_RIGHT = 696
RETURN_STEP = 50
RUST_DAYS = 21
MAX_DOTS = 5


@dataclass
class Scene:
    width: int
    height: int
    bus_y: int
    paths: list[dict] = field(default_factory=list)  # {d, cls}
    dots: list[dict] = field(default_factory=list)  # {x, y, r, cls}
    chips: list[dict] = field(default_factory=list)  # {x, y, n, cls}
    switches: list[dict] = field(default_factory=list)  # {px, cx, y, bx, by, cls, n}
    leds: list[dict] = field(default_factory=list)  # {x, y, cls}
    texts: list[dict] = field(default_factory=list)  # {x, y, t, cls}
    flows: list[dict] = field(default_factory=list)  # {id, d}
    hits: list[dict] = field(default_factory=list)  # {x, y, w, h, part, label}
    lamp: dict = field(default_factory=dict)
    hidden_branches: int = 0


def _main_x(i: int, n: int) -> float:
    if n <= 1:
        return MAIN_LEFT
    return MAIN_LEFT + i * (MAIN_RIGHT - MAIN_LEFT) / (n - 1)


def layout(view: RepoView) -> Scene:
    main = view.main_commits
    n_main = len(main)
    main_x = {c.sha: _main_x(i, n_main) for i, c in enumerate(main)}
    index = {c.sha: i for i, c in enumerate(main)}

    # GitHub にまだ無い、作業台だけの分岐も点線で描く
    local_only = [
        Branch(lb.name, lb.head, lb.unpushed, 0, lb.commits, lb.base, lb.last_date)
        for lb in view.local.branches
        if not lb.on_github and lb.unpushed and view.branch(lb.name) is None
    ]
    local_names = {b.name for b in local_only}
    branches = sorted(view.branches + local_only, key=lambda b: index.get(b.merge_base, -1))
    hidden = max(0, len(branches) - MAX_LANES)
    branches = branches[:MAX_LANES]
    lanes = len(branches)
    height = BUS_Y + LANE_GAP * lanes + 90 if lanes else BUS_Y + 110
    s = Scene(width=WIDTH, height=height, bus_y=BUS_Y, hidden_branches=hidden)

    # 幹線
    s.texts.append({"x": 24, "y": BUS_Y - 44, "t": f"幹線 {view.default_branch}", "cls": "lbl"})
    s.paths.append({"d": f"M24 {BUS_Y}H{LAMP_X - 20}", "cls": "bus"})
    s.hits.append({"x": 24, "y": BUS_Y - 12, "w": 20, "h": 24, "part": "bus", "label": "幹線"})

    # 分岐（先に描き、幹線のはんだ点を上に重ねる）
    with_pr = [b for b in branches if view.pull_for(b.name)]
    return_x: dict[str, float] = {}
    for k, b in enumerate(with_pr):
        return_x[b.name] = RETURN_RIGHT - k * RETURN_STEP
    prev_jx = 0.0
    for k, b in enumerate(branches):
        lane_y = BUS_Y + LANE_GAP * (lanes - k)
        base_x = main_x.get(b.merge_base, MAIN_LEFT - 20)
        jx = max(base_x, prev_jx + 28, 30)
        prev_jx = jx
        _branch(s, view, b, jx, lane_y, return_x.get(b.name), b.name in local_names)

    # 幹線のはんだ点と、本番マーカー・作業台マーカー
    behind = view.run_behind
    for i, c in enumerate(main):
        pending = behind is not None and i > n_main - 1 - behind
        x = main_x[c.sha]
        s.dots.append({"x": x, "y": BUS_Y, "r": 7, "cls": "commit-pending" if pending else "commit"})
        s.hits.append({"x": x - 12, "y": BUS_Y - 12, "w": 24, "h": 24, "part": f"commit:{c.sha}", "label": f"はんだ点 {c.headline}"})
    _markers(s, view, main_x)

    # 本番ランプ
    s.lamp = {"x": LAMP_X, "y": BUS_Y, "lit": view.local.run_exists or view.autodeploy, "auto": view.autodeploy}
    s.texts.append({"x": LAMP_X, "y": BUS_Y + 44, "t": "本番", "cls": "lbl mid"})
    s.texts.append({"x": LAMP_X, "y": BUS_Y + 62, "t": "自動デプロイ" if view.autodeploy else "手動デプロイ", "cls": "lblm mid"})
    s.hits.append({"x": LAMP_X - 26, "y": BUS_Y - 26, "w": 52, "h": 96, "part": "lamp", "label": "本番ランプ"})
    return s


def _branch(s: Scene, view: RepoView, b: Branch, jx: float, y: float, rx: float | None, local_only: bool = False) -> None:
    """local_only: GitHub にまだ無い、作業台だけの分岐（点線で描く）。"""
    pr = view.pull_for(b.name)
    lb = view.local_branch(b.name)
    rusty = not local_only and pr is None and b.idle_days >= RUST_DAYS
    wire_cls = "dev" if local_only else ("rust" if rusty else "w")
    pivot = (rx - 76) if rx else min(RETURN_RIGHT - 60, max(jx + 220, 420))
    if pivot < jx + 120:
        pivot = jx + 120
    part = f"local:{b.name}" if local_only else f"branch:{b.name}"

    s.paths.append({"d": f"M{jx:.0f} {s.bus_y}V{y:.0f}H{pivot:.0f}", "cls": wire_cls})
    s.dots.append({"x": jx, "y": s.bus_y, "r": 6, "cls": "jn"})
    s.hits.append({"x": jx - 10, "y": s.bus_y + 14, "w": 20, "h": y - s.bus_y - 14, "part": part, "label": f"分岐 {b.name}"})

    start = jx + 24
    linked = branch_linked_issue(view, b.name)
    issue = view.issue(linked) if linked else None
    if issue and issue.state == "open":
        s.chips.append({"x": jx + 22, "y": y - 14, "n": issue.number, "cls": "chipr"})
        s.hits.append({"x": jx + 16, "y": y - 20, "w": 76, "h": 40, "part": f"issue:{issue.number}", "label": f"付箋 #{issue.number}"})
        start = jx + 110

    # GitHub にある分岐でも、作業台で足してまだ送っていないはんだ点は点線で右に続ける
    unsent = lb.commits[-3:] if lb and not local_only else []
    kept = b.commits[-(MAX_DOTS - len(unsent)):]
    base_cls = "commit-dev" if local_only else ("commit-rust" if rusty else "commit")
    dots = [(c, base_cls) for c in kept] + [(c, "commit-dev") for c in unsent]
    span = max(pivot - 24 - start, 1)
    for i, (c, cls) in enumerate(dots):
        x = start + span * (i + 0.5) / len(dots)
        s.dots.append({"x": x, "y": y, "r": 6, "cls": cls})
        s.hits.append({"x": x - 12, "y": y - 12, "w": 24, "h": 24, "part": f"commit:{c.sha}", "label": f"はんだ点 {c.headline}"})
    hidden = (b.ahead if local_only else len(b.commits)) - len(kept)
    if hidden > 0:
        s.texts.append({"x": start, "y": y - 14, "t": f"+{hidden}", "cls": "lblm"})
    if local_only:
        # 下の段の検査ランプとぶつからないよう、ラベルは名前だけにし、「作業台のみ」は配線の上に書く
        label = b.name
        s.texts.append({"x": start + (30 if hidden > 0 else 0), "y": y - 14, "t": f"作業台のみ · 未送電 {b.ahead}", "cls": "lblm devt"})
    else:
        label = b.name + (f" · {b.idle_days}日放置" if rusty else "") + (f" · 未送電 {lb.unpushed}" if lb and lb.unpushed else "")
    # スイッチがある分岐は「スイッチ #N」の手前まで、無い分岐は図の右端までに収める
    room = (pivot - 22 if pr and rx else WIDTH - 10) - (jx + 22)
    label = _fit(label, room)
    s.texts.append({"x": jx + 22, "y": y + 32, "t": label, "cls": "lblm"})
    s.hits.append({"x": jx + 22, "y": y + 18, "w": max(min(_text_w(label) + 10, room), 60), "h": 20, "part": part, "label": f"分岐 {b.name}"})

    if pr and rx:
        cx = pivot + 44
        closed = False
        s.switches.append({"px": pivot, "cx": cx, "y": y, "bx": pivot + 36, "by": y - 24, "cls": "blade", "n": pr.number, "closed": closed})
        s.paths.append({"d": f"M{cx:.0f} {y:.0f}H{rx:.0f}V{s.bus_y}", "cls": wire_cls})
        led_cls = {"success": "led-g", "failure": "led-r", "pending": "led-y"}.get(pr.ci, "led-off")
        s.leds.append({"x": pivot + 22, "y": y - 50, "cls": led_cls})
        # 文字はランプの左に置く。右に置くと自分の戻り配線や本番ランプの文字に重なる
        s.texts.append({"x": pivot + 10, "y": y - 46, "t": {"success": "合格", "failure": "失敗", "pending": "検査中", "unavailable": "取得不可"}.get(pr.ci, "検査なし"), "cls": "lbl end"})
        s.texts.append({"x": pivot + 22, "y": y + 32, "t": f"スイッチ #{pr.number}", "cls": "lblm mid"})
        s.hits.append({"x": pivot - 52, "y": y - 66, "w": 90, "h": 30, "part": f"led:{pr.number}", "label": f"検査ランプ #{pr.number}"})
        s.hits.append({"x": pivot - 10, "y": y - 32, "w": 64, "h": 46, "part": f"pr:{pr.number}", "label": f"スイッチ #{pr.number}"})
        if pr.mergeable_state == "dirty":
            sx = pivot + 22
            s.paths.append({"d": f"M{sx - 8:.0f} {y + 8:.0f}L{sx + 2:.0f} {y - 4:.0f}L{sx - 4:.0f} {y - 8:.0f}L{sx + 8:.0f} {y - 20:.0f}", "cls": "spark"})
        s.flows.append({"id": f"flow-{pr.number}", "d": f"M{jx:.0f} {s.bus_y}V{y:.0f}H{rx:.0f}V{s.bus_y}H{LAMP_X - 20}"})
    else:
        s.paths.append({"d": f"M{pivot - 8:.0f} {y - 8:.0f}L{pivot + 8:.0f} {y + 8:.0f}M{pivot + 8:.0f} {y - 8:.0f}L{pivot - 8:.0f} {y + 8:.0f}", "cls": "w"})
        s.hits.append({"x": pivot - 16, "y": y - 16, "w": 32, "h": 32, "part": part, "label": f"分岐 {b.name} の終端"})


def _text_w(t: str) -> float:
    """12px 等幅ラベルのおおよその幅。全角は英数字の約 1.7 倍。"""
    return sum(12 if ord(ch) > 0x2000 else 7.3 for ch in t)


def _fit(t: str, room: float) -> str:
    """幅 room に収まらなければ末尾を「…」にする。"""
    if _text_w(t) <= room:
        return t
    while t and _text_w(t + "…") > room:
        t = t[:-1]
    return t + "…"


def _markers(s: Scene, view: RepoView, main_x: dict[str, float]) -> None:
    y = s.bus_y
    main = view.main_commits
    if view.local.run_exists:
        behind = view.run_behind
        if behind is not None:
            x = main_x[main[len(main) - 1 - behind].sha]
            s.paths.append({"d": f"M{x - 7:.0f} {y - 30}H{x + 7:.0f}L{x:.0f} {y - 18}Z", "cls": "mark-run"})
            s.texts.append({"x": x, "y": y - 38, "t": "本番はここまで", "cls": "lblm mid runt"})
            if behind:
                s.texts.append({"x": (x + MAIN_RIGHT) / 2 + 6, "y": y + 30, "t": f"未反映 {behind}", "cls": "lblm mid"})
            s.hits.append({"x": x - 50, "y": y - 52, "w": 100, "h": 34, "part": "run", "label": "本番マーカー"})
        else:
            s.texts.append({"x": MAIN_LEFT, "y": y + 30, "t": "本番: この表示より古い版", "cls": "lblm runt"})
            s.hits.append({"x": MAIN_LEFT - 4, "y": y + 16, "w": 180, "h": 20, "part": "run", "label": "本番マーカー"})
    # 幹線の上の「作業台」旗は、作業台で幹線そのものにいるときだけ（分岐の未送電は分岐の配線に点線で描く）
    if view.local.dev_exists and view.local.dev_branch == view.default_branch and view.local.dev_unpushed > 0 and main:
        lx = main_x[main[-1].sha]
        k = min(view.local.dev_unpushed, 3)
        end = lx + 40 + 44 * k
        s.paths.append({"d": f"M{lx:.0f} {y}L{lx + 30:.0f} {y - 42}H{end:.0f}", "cls": "dev"})
        for i in range(k):
            s.dots.append({"x": lx + 64 + 44 * i, "y": y - 42, "r": 6, "cls": "commit-dev"})
        s.paths.append({"d": f"M{end + 4:.0f} {y - 56}H{end + 16:.0f}L{end + 10:.0f} {y - 46}Z", "cls": "mark-dev"})
        s.texts.append({"x": end + 22, "y": y - 46, "t": f"作業台 · 未送電 {view.local.dev_unpushed}", "cls": "lblm devt"})
        s.hits.append({"x": lx + 26, "y": y - 72, "w": end - lx + 40, "h": 40, "part": "dev", "label": "作業台マーカー"})
