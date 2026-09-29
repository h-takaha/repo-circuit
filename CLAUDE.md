# repo-circuit — Claude 開発方針（L3: この repo 固有）

1. まず `C:\lab\git\agent-ops\configs\shared\OPERATING_RULES.md` を Read（全体ルール）。
2. 次に本 repo の `README.md` / `CONTEXT.md` を Read。

## この repo 固有の規律

- GitHub への書き込みは必ず `app/actions.py` の `Actions.run()` を通す。ブレーカー（`app/guards.py`）を迂回する経路を作らない。
- 作業台（`C:\lab\git\*`）と本番（`C:\lab\run\*`）は**読むだけ**。`app/localgit.py` に fetch / pull / checkout など状態を変える git コマンドを足さない。
  作業台の操作（switch / branch -d / push）は「ターミナルから実行」ボタンでコマンドを**コピーさせるだけ**。アプリは実行しない（`app/commands.py`）。
- 「ターミナルから実行」でコピーさせる文字列は、GitHub 由来の値（タイトル・本文・分岐名）を PowerShell で解釈されない形にする（`commands.q()` / `safe_ref()`）。GitHub に書く操作のコピーは、必ず `/actions/check`（ブレーカー）を通してから。
- 画面の文言は「比喩が先、用語は後」（例: スイッチ（プルリクエスト））。新しい部品を足したら `templates/guide.html` の凡例も更新する。
- ブレーカーのルールを足したら `tests/test_guards.py` に発火・非発火の両方のケースを足す。
