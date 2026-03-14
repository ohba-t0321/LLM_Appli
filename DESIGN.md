# 仕事で使える LLM バッチ分析アプリ設計

## 1. 目的

業務ファイル（例: PDF、Word、Excel、CSV、テキスト）をインプットとして受け取り、**CSV で定義された複数プロンプト**を順次実行し、1ファイルに対して複数の分析結果を返すアプリを構築する。

---

## 2. 想定ユースケース

- 契約書レビュー（条項抽出、リスク判定、要約）
- 顧客問い合わせログ分析（分類、感情判定、改善提案）
- 提案書チェック（必須要素の有無、誤記検出、改善提案）

---

## 3. 要件整理

### 機能要件

1. ユーザーが分析対象ファイルをアップロードできる。
2. ユーザーが複数プロンプトを記載した CSV をアップロードできる。
3. CSV 内の各プロンプトを順次実行できる。
4. プロンプトごとの結果を一覧で返せる。
5. 結果を CSV / JSON でダウンロードできる。

### 非機能要件

- セキュリティ: アップロードファイルの隔離、アクセス制御、監査ログ。
- 可観測性: 実行ログ、プロンプトごとの実行時間・トークン消費。
- 拡張性: LLM プロバイダ切替（OpenAI / Azure OpenAI / Claude 等）。
- 再現性: どの入力・プロンプト・モデルで結果が出たか追跡可能。

---

## 4. 全体アーキテクチャ

```text
[Web UI]
   │
   ├─(1) ファイルアップロード
   ├─(2) プロンプトCSVアップロード
   │
[API Server]
   ├─ File Parser Service
   ├─ Prompt Loader (CSV)
   ├─ Orchestrator / Job Queue
   ├─ LLM Gateway
   └─ Result Aggregator
   │
[Storage]
   ├─ Object Storage (入力ファイル原本)
   ├─ DB (ジョブ、プロンプト、結果、監査ログ)
   └─ Cache (ジョブ進捗)
```

### コンポーネント責務

- **Web UI**: 入力ファイル・CSVアップロード、ジョブ実行、結果閲覧。
- **API Server**: ジョブ生成、検証、進捗管理、結果返却。
- **File Parser Service**: ファイル形式ごとのテキスト抽出。
- **Prompt Loader**: CSV 構文・必須カラム検証。
- **Orchestrator**: プロンプトごとの LLM 呼び出し制御（並列/直列）。
- **LLM Gateway**: モデル呼び出し、リトライ、レート制限。
- **Result Aggregator**: プロンプト単位の結果を統合して出力。

---

## 5. CSV 仕様（提案）

プロンプトCSVは以下カラムを推奨:

| カラム名 | 必須 | 説明 |
|---|---|---|
| prompt_id | ○ | 一意なID |
| prompt_name | ○ | 画面表示用の名前 |
| system_prompt | △ | 任意。モデルへの共通指示 |
| user_prompt_template | ○ | 実際に実行する指示文 |
| output_schema | △ | JSONスキーマ（任意） |
| temperature | △ | 推論温度 |
| max_tokens | △ | 応答トークン上限 |
| enabled | ○ | true/false |

### CSVサンプル

```csv
prompt_id,prompt_name,system_prompt,user_prompt_template,output_schema,temperature,max_tokens,enabled
P001,要約,あなたは業務文書アナリストです,"次の文書を200文字以内で要約してください: {{document_text}}",,0.2,500,true
P002,重要論点抽出,あなたはリスク管理担当です,"次の文書から重要な論点を最大5件、箇条書きで出してください: {{document_text}}",,0.2,800,true
P003,判定,"","次の文書はコンプライアンス違反の懸念がありますか？理由とともに Yes/No で回答: {{document_text}}",{"type":"object","properties":{"decision":{"type":"string"},"reason":{"type":"string"}}},0.0,300,true
```

---

## 6. 処理フロー

1. ユーザーが入力ファイルとプロンプトCSVをアップロード。
2. APIがジョブを作成し、ストレージへ保存。
3. Parserが入力ファイルをテキスト化。
4. CSVを読み込み、enabled=true のプロンプトを抽出。
5. Orchestratorが各プロンプトを実行（直列または制限付き並列）。
6. 各実行結果を DB に保存（レスポンス本文、トークン、時間、ステータス）。
7. Aggregator が最終結果を整形し、UI/APIで返却。
8. ユーザーは結果を CSV/JSON でエクスポート。

---

## 7. API 設計（例）

- `POST /jobs`
  - 入力: `file`, `prompt_csv`, `run_mode`, `model`
  - 出力: `job_id`

- `GET /jobs/{job_id}`
  - ジョブ状態（queued/running/completed/failed）、進捗率

- `GET /jobs/{job_id}/results`
  - プロンプトごとの結果一覧

- `GET /jobs/{job_id}/export?format=csv|json`
  - 結果のダウンロード

---

## 8. データモデル（最小構成）

### jobs
- id
- user_id
- file_path
- prompt_csv_path
- model_name
- status
- created_at / completed_at

### prompts
- id
- job_id
- prompt_id
- prompt_name
- system_prompt
- user_prompt_template
- output_schema
- temperature
- max_tokens
- enabled

### prompt_results
- id
- job_id
- prompt_id
- status
- raw_response
- parsed_response
- tokens_input
- tokens_output
- latency_ms
- error_message
- created_at

---

## 9. エラー設計

- CSVフォーマット不正 → ジョブ作成時に 400 を返却。
- ファイル解析失敗 → `job.status=failed` + 詳細ログ。
- LLM一時エラー（429/5xx）→ 指数バックオフでリトライ。
- 1プロンプト失敗時 → 他プロンプトは継続し、部分成功として返却。

---

## 10. セキュリティ設計

- 入力ファイルをウイルススキャン。
- ファイル暗号化保存（at-rest）と TLS（in-transit）。
- ユーザー単位の RBAC（他ユーザーのジョブ閲覧不可）。
- 個人情報を含む出力のマスキングオプション。
- 監査ログ（誰がいつどのファイルを処理したか）。

---

## 11. 技術スタック案

- **フロントエンド**: Next.js
- **バックエンド**: Python (FastAPI)
- **非同期処理**: Celery or RQ + Redis
- **DB**: PostgreSQL
- **オブジェクトストレージ**: S3 互換
- **LLM SDK**: LangChain もしくは自前 Gateway

---

## 12. 実装ステップ（MVP）

1. ファイル + CSV アップロード API 作成。
2. CSV バリデータ実装。
3. ファイル解析（まずは txt / pdf）を実装。
4. 単一モデル固定で Orchestrator 実装。
5. 結果一覧 API と CSV エクスポート実装。
6. ログ・メトリクス追加。
7. 失敗時のリトライと部分成功返却を追加。

---

## 13. 将来拡張

- プロンプトテンプレート管理（再利用）。
- A/B比較（複数モデル同時実行）。
- RAG連携（社内ナレッジ検索）。
- 評価基盤（期待回答との自動比較）。

