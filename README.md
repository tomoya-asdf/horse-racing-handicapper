# horse-racing-handicapper

JRA レースデータを収集し、AI 予想、買い目判定、結果反映を Web UI から操作するローカル運用向けアプリケーションです。

## 構成

| サービス | 役割 |
| --- | --- |
| `db` | PostgreSQL。レース、出走馬、過去戦績、予測、買い目、ジョブ履歴、設定を保存します。 |
| `collector` | netkeiba からレース、出馬表、単勝オッズ、人気、結果、払戻、馬の過去戦績、血統を収集します。 |
| `predictor` | モデル学習、AI 予想、買い目判定、投票、払戻反映、バックテストを実行します。 |
| `webui` | FastAPI と React の管理画面を提供します。 |

主なディレクトリ:

```text
src/common/      DBモデル、設定、ジョブ管理
src/collector/   スクレイピング、データ収集、過去データ補完
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

このプロジェクトは通常、ソースをコンテナへコピーしてイメージを作ります。コードや Web UI を変更した場合は、対象イメージを再ビルドしてコンテナを再作成してください。

```powershell
docker compose build collector predictor webui
docker compose up -d --force-recreate collector predictor webui
```

## Web UI

管理画面では以下を確認、操作できます。

- 概要: ジョブ状況、直近のレース、買い目、モデル状態
- レース一覧: 日付、競馬場、レース番号、馬名、騎手、予測、買い目などで絞り込み
- レース詳細: 出走馬、単勝オッズ、人気、予測スコア、買い目
- 馬詳細: 馬名リンクから過去戦績と血統を表示
- 買い目一覧: 判定済み、投票済み、失敗、払戻結果
- ジョブ: 手動実行、バックフィル、バックテスト、実行履歴
- 設定: 賭け設定、定期実行設定、環境設定の確認

設定ページの変更は、保存ボタンを押すまで反映されません。保存後、collector / predictor が次回ジョブ確認を行うタイミングで反映されます。

## ジョブと定期実行

主なジョブ:

| ジョブ | 役割 | 定期実行 |
| --- | --- | --- |
| `collect` | レース、出馬表、オッズ、結果を収集 | 確認間隔を設定 |
| `collect_horses` | 馬の過去戦績と血統を収集 | 確認間隔を設定。初期値は月1回程度 |
| `predict` | 未確定レースに AI 予測を作成 | 確認間隔を設定 |
| `bet_decide` | 予測と最新オッズから買い目を判定 | 発走前 N 分で実行 |
| `settle` | 払戻、決済結果を反映 | 発走後 N 分で実行 |
| `train` | 確定済みレースからモデルを再学習 | 確認間隔を設定。初期値は月1回程度 |
| `backfill` | 指定期間の過去レース、結果、出走馬の過去戦績、血統を補完 | 手動実行 |
| `backtest` | 指定期間で回収率を検証 | 手動実行 |

`bet_decide` と `settle` は固定の実行間隔を設定しません。内部では短い間隔で対象レースを確認し、レース一覧の発走時刻を基準に、設定された発走前または発走後のタイミングで実行します。

`bet_decide` は判定直前に対象レースのデータを再収集し、できるだけ直前の単勝オッズと馬連オッズで買い目を判断します。

手動ジョブは Web UI のジョブページから実行できます。過去データ取得(バックフィル)はモデル学習用の初期データ準備として使う想定で、指定期間のレース取得後、その期間に出走した馬の過去戦績と血統も続けて取得します。

## 設定

`.env` は起動時のデフォルト値です。賭け設定と定期実行設定は Web UI の設定ページから変更でき、`app_settings` テーブルの値が `.env` より優先されます。

主な環境変数:

| 変数 | 内容 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 接続先 |
| `COLLECT_INTERVAL_MINUTES` | データ収集の既定間隔 |
| `PREDICT_INTERVAL_MINUTES` | AI 予想の既定間隔 |
| `COLLECT_HORSES_INTERVAL_MINUTES` | 馬過去戦績収集の既定間隔 |
| `TRAIN_INTERVAL_MINUTES` | モデル学習の既定間隔 |
| `COLLECT_DAYS_AHEAD` | 何日先のレースまで収集するか |
| `BET_DECISION_WINDOW_MINUTES` | 買い目判定の対象にする発走前ウィンドウ |
| `BET_DECISION_LEAD_MINUTES` | 買い目判定を発走何分前に行うか |
| `SETTLE_DELAY_MINUTES` | 決済確認を発走何分後に行うか |
| `BET_AMOUNT` | 1件あたり購入金額 |
| `BET_SCORE_THRESHOLD` | 買い目候補にする最低予測スコア |
| `BET_MIN_EXPECTED_VALUE` | 最低期待値 |
| `BETTING_MODE` | `sim` または `prod` |
| `HORSE_RESULTS_PER_RUN` | 1回の馬過去戦績収集で取得する馬数 |
| `HORSE_RESULTS_REFRESH_DAYS` | 馬過去戦績を再取得する間隔 |
| `SCRAPER_REQUEST_INTERVAL_SECONDS` | スクレイピング間隔 |
| `IPAT_*` | IPAT 投票に使う認証情報 |
| `IPAT_DRY_RUN` | `true` の間は実購入ボタンを押さずに確認まで行う |
| `ADMIN_LOGIN_ID` / `ADMIN_PASSWORD` | Web UI 管理ログイン情報 |

`BET_DECIDE_INTERVAL_MINUTES` と `SETTLE_INTERVAL_MINUTES` は使いません。買い目判定と決済は発走時刻基準で制御します。

## 収集データ

`collector` は以下を保存します。

- レース情報: 日付、競馬場、レース番号、レース名、発走時刻、距離、芝/ダート、右左回り、馬場、天候、クラス
- 出走馬: 馬番、馬 ID、馬名、騎手、騎手 ID、斤量、単勝オッズ、人気、着順
- 結果: 着順、払戻、確定後オッズ、人気
- 馬データ: 馬 ID、馬名、父馬 ID、父馬名、過去戦績の取得日時
- 過去戦績: レース日、競馬場、頭数、枠番、馬番、オッズ、人気、着順、騎手、斤量、距離、馬場、タイム、着差、通過順、上がり 3F、馬体重

出馬表の通常 HTML だけで単勝オッズや人気が取れない場合があるため、通常の HTML/API 取得に加えて Playwright + Chromium で JavaScript 反映後の出馬表も確認します。

## モデルと買い目判定

モデルは LightGBM を使い、確定済みレースから各出走馬が 1 着になる確率を予測します。校正には `IsotonicRegression` を使います。学習済みモデルはコンテナ内の `/app/data/model.pkl` に保存されます。

主な特徴量:

- 基本特徴量: `horse_number`, `weight`, `field_size`, `distance`
- カテゴリ特徴量: `jockey_id`, `sire_id`
- 過去戦績特徴量: `career_starts`, `win_rate`, `place_rate`, `avg_finish_recent3`, `avg_finish_recent5`, `best_last3f_recent5`, `avg_last3f_recent5`, `days_since_last`, `distance_change`, `same_dist_starts`, `same_dist_avg_finish`, `same_surface_starts`, `same_surface_avg_finish`

過去戦績特徴量は対象レース日より前の `horse_results` のみから作ります。単勝オッズと人気は収集、表示、買い目判定、バックテストに使いますが、現在のモデル特徴量には含めていません。

買い目は `sim` と `prod` のモードを持ちます。`sim` は投票せず記録のみ、`prod` は IPAT 投票処理を試行します。`IPAT_DRY_RUN=true` の間は実購入直前までの確認に留めます。

## 注意

- netkeiba の画面構造や API レスポンスが変わると、スクレイピング処理の修正が必要になる場合があります。
- 馬過去戦績の収集件数を増やすと外部サイトへのアクセスも増えます。`SCRAPER_REQUEST_INTERVAL_SECONDS` を適切に設定してください。
- モデルの性能は保存済みデータ量と過去戦績の充実度に大きく依存します。
- `prod` モードと `IPAT_DRY_RUN=false` の組み合わせは実購入につながります。必ず実機で画面遷移と購入フローを確認してから使ってください。
