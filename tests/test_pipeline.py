import asyncio

from app.demo import DemoSource
from app.explain import status_text, summary
from app.pipeline import local_totals, rows


def view(name="event-calendar"):
    return asyncio.run(DemoSource().repo_view(name))


def _row(rs, name):
    r = next(r for r in rs if r["name"] == name)
    return {s["key"]: (s["state"], s["text"]) for s in r["stages"]} | {"_next": r["next"], "_part": r["part"], "_here": r["here"]}


def test_rows_follow_each_branch_from_commit_to_production():
    rs = rows(view())
    assert rs[0]["name"] == "fix/14-csv-encoding" and rs[0]["here"]  # 作業中の分岐が先頭

    csv = _row(rs, "fix/14-csv-encoding")
    assert csv["commit"] == ("todo", "未コミット 2")
    assert csv["push"] == ("ok", "済")
    assert csv["switch"] == ("ok", "#6") and csv["check"] == ("bad", "失敗")
    assert csv["_next"].startswith("作業台で変更をコミットする")

    btn = _row(rs, "fix/12-button-size")
    assert btn["commit"] == ("ok", "済") and btn["push"] == ("todo", "未送電 1")
    assert btn["merge"] == ("todo", "閉じられる")
    assert "git push" in btn["_next"]

    idea = _row(rs, "exp/local-idea")
    assert idea["push"] == ("todo", "未送電 2 · 作業台のみ")
    assert idea["switch"][0] == "off" and idea["_part"] == "local:exp/local-idea"
    assert "git push -u origin exp/local-idea" in idea["_next"]

    old = _row(rs, "exp/old-layout")  # GitHub にだけある分岐
    assert old["push"] == ("ok", "GitHub のみ") and old["switch"] == ("todo", "未")

    log = _row(rs, "fix/9-log-format")  # 合流したが本番がまだ1つ手前
    assert log["merge"] == ("ok", "済") and log["prod"] == ("wait", "反映待ち")
    assert _row(rs, "fix/10-holiday")["prod"] == ("ok", "反映済")


def test_main_row_warns_when_working_directly_on_main():
    v = view()
    v.local.dev_branch, v.local.dev_uncommitted, v.local.dev_unpushed = "main", 1, 2
    r = rows(v)[0]
    assert r["name"] == "幹線 main" and r["warn"] and r["part"] == "dev"
    assert local_totals(v) == (1, 3 + 2)  # 分岐の未送電 3 ＋ 幹線の 2


def test_summary_and_status_mention_local_work():
    v = view()
    assert local_totals(v) == (2, 3)
    s = summary(v)
    assert (s["uncommitted"], s["unpushed"]) == (2, 3)
    assert "作業台（fix/14-csv-encoding）に未コミットの変更が2つ、GitHub へ送っていないはんだ点が3つ" in s["sentence"]
    assert "まだコミットしていない変更が 2 つ" in status_text(v)


def test_github_ahead_is_not_counted_as_done():
    v = view()
    lb = v.local_branch("fix/14-csv-encoding")
    lb.github_ahead = True
    assert _row(rows(v), "fix/14-csv-encoding")["push"] == ("na", "GitHub が先行")
