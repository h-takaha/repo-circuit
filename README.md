# repo-circuit

GitHub を**配線図**で見て・触って・覚える、自作ツール別の課題管理 Web アプリ（ローカル専用・port 8017）。

- **配電盤**: 全ツールを1行ずつ。赤いランプ・錆びた配線・本番の遅れがある回路が上に来る
- **回路図**: 1ツールの配線図。部品を押すと解説とできる操作が出る。スイッチを閉じると電気が本番まで流れる
- **作業場所（worktree）**: 回路図の隣のタブで、主作業場所と追加の登録を全件表示。実際の先端、取り込みの根拠、残っているファイルの要約を確認する
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

## 作業場所を確認する

`/repo/<ツール名>/worktrees` は読み取り専用の一覧です。GitHub の合流と、手元のフォルダ・ファイルが残っていることを別々に示します。回路図と作業場所は通常のリンクで移動でき、ツールを切り替えても選択中のタブを保ちます。

取り込み状況は「確認済み」「合流記録あり・到達未確認」「取り込み未確認」「判定不明」の4状態です。ローカルの GitHub コピーを使うため、証拠の取得元と古さも確認してください。アプリは fetch しません。GitHub が取得できなくても、有効なローカル repo の登録を表示します。

ファイル数は変更済み・未追跡・無視対象の**要約パス件数**です。まとめて報告されるディレクトリは1件で、`.venv` の中などを再帰的に数えません。0は取得して該当なし、未取得は数が分からない状態です。詳細には各分類20件までのパス例を表示します。

整理前は、使用終了の確認 → 必要ファイルの退避と復元確認 → 管理元で作業場所の整理 → 必要なら残った分岐の整理、の順に進めます。管理元はパスから推定した候補です。登録の prune とフォルダ削除は別操作であり、この画面には削除・退避・整理コマンドのコピー機能はありません。

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

付箋から修正へ進むときは、付箋を押して「この付箋用に分岐を引く」を選びます。`fix/<番号>-...` の分岐がGitHubにあると「配線中」になります。ローカルだけの分岐は送電（push）するまで「未接続」のままです。修正した分岐のスイッチ（PR）の説明に `Fixes #番号` を書くと、合流時に付箋も自動で「完了」になります。ローカルで修正・検証しただけでは完了にしません。

合流済み分岐の撤去は、古いPRも分岐名で追加確認します。追加確認は最大3ページで、通常表示は最大60秒のcacheを含みます。確認・実行時は最新状態を読み直し、取得失敗や証拠不足はブレーカーで止めます。

## テスト

### 検査ランプが「取得不可」の場合

Checks API が 403 を返したときは、代わりに **GitHub Actions の実行結果**（PAT の Actions 読み取り権限）で検査ランプを決めます。

| Actions の状態 | 検査ランプ |
|---|---|
| その PR の先端で実行がある | 合格 / 失敗 / 検査中（ワークフローごとに最新の実行を見る） |
| repo にワークフローが1つも無い | 検査なし（合流は「注意」で確認すれば進める） |
| ワークフローはあるのに、その先端の実行が無い／Actions API も 403 | **取得不可** |

Actions 以外の外部 CI（GitHub App が付ける check など）の結果は Actions API では見えません。

取得不可のときも配電盤と回路図は表示します。「検査なし」や「合格」とは扱わず、
ブレーカーがアプリからの合流（マージ）を止めます。確認を押しても解除できません。

Fine-grained PAT は現在 Checks API に対応しておらず、設定画面で Checks 権限を追加することもできません
（[GitHub の Fine-grained PAT の制約](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens#fine-grained-personal-access-tokens-limitations)）。
検査結果を取得するには、GitHub App など Checks API に対応した認証方式と、必要な読み取り権限を検討してください。
認証方式の変更には別途設定が必要です。取得できるようになったら、画面を再読み込みしてください。
403 以外の Checks API エラーや、PR 詳細など他の API の 403 は通常の取得エラーとして扱います。

### 実行

```powershell
.venv\Scripts\pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
```

## 実機への導入チェックリスト（Hiroy）

1. **PAT を発行**: GitHub → Settings → Developer settings → Fine-grained tokens。対象 = h-takaha の全 repo、
   権限 = Issues RW / Pull requests RW / Contents RW / Actions RW / Metadata R（`.env.example` と同じ）
2. **稼働クローン**: `git clone https://github.com/h-takaha/repo-circuit C:\lab\run\repo-circuit`
3. **venv**: `C:\lab\git\agent-ops\setup\phase3\setup-tool-venv.ps1` で `C:\lab\run\repo-circuit\.venv` を作る
4. **.env**: `C:\lab\run\repo-circuit\.env` に PAT と Basic 認証値（画面・ログ・コミットに出さない）
5. **server_manager**: server-manager repo の `tools.json` に `repo_circuit`（port 8017。8014 は task_manager、8015 も使用中、`uvicorn app.main:app --port 8017 --env-file .env`）を追加（PR 経由）
6. **自動デプロイ（任意）**: agent-ops の `automation/config/autodeploy.json` に
   `"repo-circuit": { "restart": "server-manager", "serverManagerName": "repo_circuit" }`
7. **AI 起票 skill**: agent-ops で `node deploy.js --diff` → 内容確認 → `node deploy.js --apply --force`
8. **動作確認**: 配電盤に全ツールが並ぶ / 部品トレイから起票 → GitHub に Issue / Claude に「○○の△△を直したい」→ 同じトレイに出る /
   付箋から分岐 → 作業台でコミット・push → スイッチを付ける → 検査ランプ緑で閉じる → 付箋が自動で完了、本番の旗が追いつく
