import asyncio
from datetime import timedelta

from app.circuit import layout
from app.demo import DemoSource
from app.explain import parts, summary
from app.issues import board_state, fixes_numbers
from app.model import Branch, now


def view(name="event-calendar"):
    return asyncio.run(DemoSource().repo_view(name))


def _segments(scene):
    """分岐の配線（M x y V y2 H x2）を縦線・横線に分解する。"""
    import re

    segs = []
    for p in scene.paths:
        if p["cls"] not in ("w", "rust", "dev"):
            continue
        m = re.fullmatch(r"M(\d+) (\d+)V(\d+)H(\d+)", p["d"])
        if m:
            x, y0, y1, x1 = map(int, m.groups())
            segs.append(("v", x, y0, y1))
            segs.append(("h", y1, x, x1))
        m = re.fullmatch(r"M(\d+) (\d+)H(\d+)V(\d+)", p["d"])
        if m:
            x0, y, x1, y1 = map(int, m.groups())
            segs.append(("h", y, x0, x1))
            segs.append(("v", x1, y1, y))
    return segs


def test_branch_wires_do_not_cross():
    v = view()
    for i in range(3):  # 分岐を増やしても交差しない
        v.branches.append(Branch(f"fix/{20 + i}-x", "s", 1, 0, v.branches[0].commits[:1], v.main_commits[i + 2].sha, now()))
    s = layout(v)
    segs = _segments(s)
    for a in segs:
        for b in segs:
            if a[0] == "v" and b[0] == "h":
                _, x, y0, y1 = a
                _, y, x0, x1 = b
                lo, hi = sorted((y0, y1))
                if min(x0, x1) < x < max(x0, x1) and lo < y < hi:
                    raise AssertionError(f"交差: {a} と {b}")


def test_markers_and_switches_present():
    s = layout(view())
    assert {h["part"] for h in s.hits} >= {"bus", "lamp", "run", "local:exp/local-idea", "pr:5", "pr:6", "issue:12", "issue:14"}
    assert len(s.switches) == 2 and len(s.flows) == 2
    assert any(d["cls"] == "commit-pending" for d in s.dots)  # 本番に届いていないはんだ点
    assert sum(d["cls"] == "commit-dev" for d in s.dots) == 3  # 未送電: ボタンの分岐に1、作業台だけの分岐に2
    assert any(t["t"] == "作業台のみ · 未送電 2" for t in s.texts)
    assert "dev" not in {h["part"] for h in s.hits}  # 分岐で作業中なので、幹線の上の作業台旗は出さない


def test_dev_flag_on_bus_only_when_working_on_main():
    v = view()
    v.local.dev_branch, v.local.dev_unpushed = "main", 2
    s = layout(v)
    assert "dev" in {h["part"] for h in s.hits}
    assert any("作業台 · 未送電 2" == t["t"] for t in s.texts)


def test_board_state_and_fixes():
    v = view()
    assert board_state(v, v.issue(12)) == "wired"
    assert board_state(v, v.issue(15)) == "unwired"
    assert board_state(v, v.issue(10)) == "done"
    assert fixes_numbers("Fixes #3 and closes #4, resolved #5") == {3, 4, 5}


def test_explanations_cover_every_clickable_part():
    v = view()
    p = parts(v)
    for h in layout(v).hits:
        assert h["part"] in p, h["part"]
    assert summary(v)["alert"] is True
    assert summary(view("job-watcher"))["next"] == "やることはありません"


def test_rusty_branch_label():
    v = view()
    v.branches[0].last_date = now() - timedelta(days=40)
    s = layout(v)
    assert any("日放置" in t["t"] for t in s.texts)
