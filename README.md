# horse-racing-handicapper

JRA レースデータを収集し、AI 予測、買い目判定、結果確認を Web UI から行うローカル運用向けアプリケーションです。

## 構成

| サービス | 役割 |
| --- | --- |
| `db` | PostgreSQL。レース、出走馬、過去戦績、予測、買い目、ジョブ履歴、設定を保存します。 |
| `collector` | netkeiba からレース、出馬表、単勝オッズ、人気、結果、払戻、馬の過去戦績、血統を収集します。 |
| `predictor` | モデル学習、予測、買い目判定、投票、払戻反映、バックテストを実行します。 |
| `webui` | 管理画面と API を提供します。 |

主なディレクトリ:

```text
src/common/      DB モデル、設定、ジョブ管理
src/collector/   スクレイピング、収集、過去データ補完
src/predictor/   特徴量生成、学習、予測、買い目判定、バックテスト
src/api/         FastAPI と Web UI 用 API
webui/           React フロントエンド
docker/          各サービスの Dockerfile
docs/            設計ドキュメント
```

## 起動

```powershell
docker compose up -d --build
```

Web UI:

```text
http://localhost:8000
```

主な手動ジョブは Web UI のジョブ画面から実行できます。CLI で直接実行する場合は次のようにします。

```powershell
docker compose run --rm collector python -m src.collector.main collect
docker compose run --rm collector python -m src.collector.main collect_horses
docker compose run --rm predictor python -m src.predictor.main train
docker compose run --rm predictor python -m src.predictor.main predict
docker compose run --rm predictor python -m src.predictor.main bet_decide
docker compose run --rm predictor python -m src.predictor.main settle
```

過去データの補完:

```powershell
docker compose run --rm collector python -m src.collector.main backfill --start 2025-01-01 --end 2025-12-31
```

バックテスト:

```powershell
docker compose run --rm predictor python -m src.predictor.main backtest --start 2025-01-01 --end 2025-12-31
```

## 収集データ

`collector` は以下を保存します。

- レース情報: 日付、競馬場、レース番号、レース名、発走時刻、距離、芝/ダート、右左回り、馬場、天候、クラス
- 出走馬: 馬番、馬 ID、馬名、騎手、騎手 ID、斤量、単勝オッズ、人気、着順
- 結果: 着順、払戻、確定後のオッズ/人気
- 馬データ: 馬 ID、馬名、父馬 ID、父馬名、過去戦績の取得日時
- 過去戦績: レース日、競馬場、頭数、枠番、馬番、オッズ、人気、着順、騎手、斤量、距離、芝/ダート、馬場、タイム、着差、通過順、上がり 3F、馬体重

出馬表の静的 HTML だけで単勝オッズや人気が取れない場合があります。そのため現在は、通常の HTML/API 取得に加えて Playwright + Chromium で出馬表を描画し、JavaScript 反映後の未確定オッズと人気も取得するフォールバックを持っています。

描画後も不足がある場合は、取得できた単勝オッズの昇順から人気を補完します。

馬の過去戦績と血統は `collect_horses` ジョブで更新します。更新対象は、未取得または `HORSE_RESULTS_REFRESH_DAYS` より古い馬です。

## Web UI

管理画面では以下を確認、操作できます。

- 概要: ジョブ状況、直近のレース/買い目
- レース一覧: 年を含む日付表示、レース番号、競馬場、馬名、騎手、予測、買い目などで絞り込み
- レース詳細: 出走馬、単勝オッズ、人気、予測スコア、買い目
- 馬詳細: 馬名リンクから別ページで過去戦績と血統を表示
- 買い目一覧: 判定済み、投票済み、失敗、払戻結果
- ジョブ: 収集、学習、予測、買い目判定、払戻反映、バックテストなどの実行
- 設定: ベット金額、スコア閾値、期待値閾値、シミュレーション/本番モードなど

## モデルと特徴量

学習は確定着順のあるレースを使い、各出走馬が 1 着になるかを二値分類します。モデルは LightGBM です。

検証は時系列で古いデータを学習、新しいデータを検証に分け、検証データで early stopping と確率校正を行います。校正には `IsotonicRegression` を使います。

現在の特徴量は以下です。

基本特徴量:

- `horse_number`
- `weight`
- `field_size`
- `distance`

カテゴリ特徴量:

- `jockey_id`
- `sire_id`

過去戦績特徴量:

- `career_starts`
- `win_rate`
- `place_rate`
- `avg_finish_recent3`
- `avg_finish_recent5`
- `best_last3f_recent5`
- `avg_last3f_recent5`
- `days_since_last`
- `distance_change`
- `same_dist_starts`
- `same_dist_avg_finish`
- `same_surface_starts`
- `same_surface_avg_finish`

過去戦績特徴量は、対象レース日より前の `horse_results` のみから作ります。未来情報の混入を避けるため、対象レース当日以降の成績は使いません。

単勝オッズと人気は収集・表示・買い目判定には使いますが、現在のモデル特徴量には含めていません。

学習済みモデルはコンテナ内の `/app/data/model.pkl` に保存されます。

## 買い目判定

`predictor` は最新モデルの予測スコアとオッズを使い、設定条件を満たす場合に買い目を作成します。

- 単勝: 最高スコアの馬について、スコア閾値と期待値閾値を満たす場合に作成
- 馬連: 上位候補と馬連オッズから期待値を計算し、条件を満たす場合に作成

買い目は `sim` と `prod` のモードを持ちます。`sim` は投票せず記録のみ、`prod` は IPAT 投票処理を試行します。

ステータス:

- `pending`: 投票前
- `placed`: 投票済み、またはシミュレーション上の投票済み
- `dry_run`: ドライラン
- `failed`: 投票失敗

## データベース

主要テーブル:

- `races`
- `entries`
- `horses`
- `horse_results`
- `predictions`
- `bets`
- `job_runs`
- `app_settings`

起動時に SQLAlchemy の `create_all()` でテーブルを作成します。既存 DB への簡易マイグレーションとして、一部の追加カラムとインデックスは `ALTER TABLE ... ADD COLUMN IF NOT EXISTS` で補完します。

本格的な履歴付きマイグレーションはまだ導入していません。

## 主な環境変数

`.env` で設定します。

| 変数 | 内容 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 接続先 |
| `COLLECT_DAYS_AHEAD` | 先何日分のレースを収集するか |
| `HORSE_RESULTS_PER_RUN` | 1 回の馬戦績収集件数 |
| `HORSE_RESULTS_REFRESH_DAYS` | 馬戦績を再取得する間隔 |
| `SCRAPER_REQUEST_INTERVAL_SECONDS` | スクレイピング間隔 |
| `BET_DECISION_WINDOW_MINUTES` | 発走何分前から買い目判定するか |
| `BET_AMOUNT` | 1 点あたりの購入金額 |
| `BET_SCORE_THRESHOLD` | 買い目候補にする最低予測スコア |
| `BET_MIN_EXPECTED_VALUE` | 最低期待値 |
| `BETTING_MODE` | `sim` または `prod` |
| `IPAT_*` | IPAT 投票に使う認証情報 |
| `ADMIN_USERNAME` / `ADMIN_PASSWORD` | Web UI ログイン情報 |

## 注意点

- netkeiba の画面構造や API レスポンスが変わると、スクレイピング処理の修正が必要になる場合があります。
- 未確定オッズと人気は JavaScript 描画後に出ることがあるため、collector イメージには Playwright と Chromium を入れています。
- 過去戦績の収集件数を増やすと外部サイトへのアクセスも増えます。`SCRAPER_REQUEST_INTERVAL_SECONDS` を適切に設定してください。
- モデルの性能は保存済みデータ量と過去戦績の充実度に大きく依存します。
