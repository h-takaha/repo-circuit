# repo-circuit — Claude 開発方針（L3: この repo 固有）

1. まず `C:\lab\git\agent-ops\configs\shared\OPERATING_RULES.md` を Read（全体ルール）。
2. 次に本 repo の `README.md` / `CONTEXT.md` を Read。

## この repo 固有の規律

- GitHub への書き込みは必ず `app/actions.py` の `Actions.run()` を通す。ブレーカー（`app/guards.py`）を迂回する経路を作らない。
- 作業台（`C:\lab\git\*`）と本番（`C:\lab\run\*`）は**読むだけ**。`app/localgit.py` に fetch / pull / checkout など状態を変える git コマンドを足さない。
- 画面の文言は「比喩が先、用語は後」（例: スイッチ（プルリクエスト））。新しい部品を足したら `templates/guide.html` の凡例も更新する。
- ブレーカーのルールを足したら `tests/test_guards.py` に発火・非発火の両方のケースを足す。
