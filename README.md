# repo-circuit

GitHub を**配線図**で見て・触って・覚える、自作ツール別の課題管理 Web アプリ（ローカル専用・port 8017）。

- **配電盤**: 全ツールを1行ずつ。赤いランプ・錆びた配線・本番の遅れがある回路が上に来る
- **回路図**: 1ツールの配線図。部品を押すと解説とできる操作が出る。スイッチを閉じると電気が本番まで流れる
- **部品トレイ**: 全ツールの付箋（Issue）を 未接続 / 配線中 / 完了 の3列で。ここから起票できる
- **凡例**: 回路記号と GitHub 用語の対応、ブレーカーの一覧

危ない操作（赤ランプのまま合流、未合流のはんだ点がある分岐の撤去、秘密情報の書き込み など）は
**ブレーカーが落ちて止まり、なぜダメか・代わりに何をするかを表示**する。設計の詳細は [CONTEXT.md](CONTEXT.md)。

## 起動

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # GITHUB_TOKEN と BASIC_AUTH_PASSWORD を記入
.venv\Scripts\python -m uvicorn app.main:app --port 8017 --env-file .env
```

`GITHUB_TOKEN` が空なら**デモモード**（サンプルの回路。書き込みはメモリ上の擬似実行）で動く。
`BASIC_AUTH_PASSWORD` が空だと全ページ 401（fail-closed）。`/healthz` だけ認証なし。

## AI（Claude / Codex）からの起票

```http
POST http://localhost:8017/api/issues
Content-Type: application/json
Authorization: Basic ...

{"repo": "event-calendar", "title": "保存ボタンが見つけにくい", "situation": "…", "want": "…",
 "kind": "improvement", "priority": "p2", "source": "claude"}
```

- `kind`: `bug` / `improvement` / `idea`、`priority`: `p1` / `p2` / `p3`
- 付くラベル: `feedback` ＋ kind ＋ priority（無ければ自動作成）
- 秘密情報らしき文字列を含むと 422（ブレーカー）
- 共通 skill は agent-ops の `configs/shared/skills/tool-issue/SKILL.md`。アプリが動いていないときは同じラベル・同じ本文テンプレで `gh issue create` する

読み取り: `GET /api/repos`、`GET /api/issues?repo=<name>&state=open|closed|all`

## テスト

### 検査ランプが「取得不可」の場合

Checks API が 403 を返しても、配電盤と回路図は表示します。該当スイッチの検査状態は
**取得不可**となり、「検査なし」や「合格」とは扱いません。取得不可の間は、ブレーカーが
アプリからの合流（マージ）を止めます。確認を押しても解除できません。

Fine-grained PAT で検査結果を読むには、Actions 権限とは別に **Checks: Read** が必要です
（[GitHub の Checks API ドキュメント](https://docs.github.com/en/rest/checks/runs#list-check-runs-for-a-git-reference)）。
権限などを確認して取得できるようになったら、画面を再読み込みしてください。
403 以外の Checks API エラーや、PR 詳細など他の API の 403 は通常の取得エラーとして扱います。

### 実行

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## 実機への導入チェックリスト（Hiroy）

1. **PAT を発行**: GitHub → Settings → Developer settings → Fine-grained tokens。対象 = h-takaha の全 repo、
   権限 = Issues RW / Pull requests RW / Contents RW / Actions RW / Checks R / Metadata R（`.env.example` と同じ）
2. **稼働クローン**: `git clone https://github.com/h-takaha/repo-circuit C:\lab\run\repo-circuit`
3. **venv**: `C:\lab\git\agent-ops\setup\phase3\setup-tool-venv.ps1` で `C:\lab\run\repo-circuit\.venv` を作る
4. **.env**: `C:\lab\run\repo-circuit\.env` に PAT と Basic 認証値（画面・ログ・コミットに出さない）
5. **server_manager**: server-manager repo の `tools.json` に `repo_circuit`（port 8017。8014 は task_manager、8015 も使用中、`uvicorn app.main:app --port 8017 --env-file .env`）を追加（PR 経由）
6. **自動デプロイ（任意）**: agent-ops の `automation/config/autodeploy.json` に
   `"repo-circuit": { "restart": "server-manager", "serverManagerName": "repo_circuit" }`
7. **AI 起票 skill**: agent-ops で `node deploy.js --diff` → 内容確認 → `node deploy.js --apply --force`
8. **動作確認**: 配電盤に全ツールが並ぶ / 部品トレイから起票 → GitHub に Issue / Claude に「○○の△△を直したい」→ 同じトレイに出る /
   付箋から分岐 → 作業台でコミット・push → スイッチを付ける → 検査ランプ緑で閉じる → 付箋が自動で完了、本番の旗が追いつく
