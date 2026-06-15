# horse-racing-handicapper

競馬レースデータを収集し、AI 予想、買い目判定、結果反映を Web UI から操作するローカル運用向けアプリケーションです。

## 構成

| サービス | 役割 |
| --- | --- |
| `db` | PostgreSQL。レース、出走馬、過去戦績、予測、モデルバージョン、買い目、ジョブ履歴、ジョブ予約、設定を保存します。 |
| `collector` | netkeiba からレース、出馬表、単勝オッズ、人気、結果、払戻を収集します。馬の過去戦績と血統は `collect_horses` ジョブで収集します。 |
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

## セットアップ

1. `.env.example` を `.env` にコピーする

   ```powershell
   Copy-Item .env.example .env
   ```

1. .envを編集して、管理者パスワードを変更する
   ```powershell
   ADMIN_PASSWORD = {変更したいパスワード}
   ```
   ※その他、必要に応じて値を変更してください。
   
1. コンテナをビルド・起動する

 
   ```powershell
   docker compose up -d --build
   ```

1. 管理コンソールへアクセスする

   http://localhost:8000

   ※ 管理コンソール(8000)とPostgreSQL(5432)はループバックアドレス(127.0.0.1)のみに
   公開しています。他の端末からアクセスする必要がある場合のみ `docker-compose.yml` の
   `ports` を変更してください（認証は無いため、公開範囲の変更は慎重に）。

## Web UI

管理画面では以下を確認、操作できます。

- 概要: ジョブ状況、直近のレース、買い目、モデル状態。モデルバージョンからモデル詳細ページを新規タブで開けます
- レース一覧: 日付、競馬場、レース番号、馬名、騎手、厩舎、予測、買い目などで絞り込み
- レース詳細: 出走馬(性齢、厩舎、馬体重を含む)、事前オッズ、確定オッズ、人気、予測スコア、順位表、買い目候補
- 馬詳細: 馬名リンクから過去戦績と血統を表示
- 買い目一覧: 判定済み、投票済み、失敗、払戻結果、判定に使ったモデルバージョン
- ジョブ: 手動実行、バックフィル、バックテスト、ジョブ予約、予約一覧、実行履歴
- 設定: 賭け設定、定期実行設定、環境設定の確認、システム再起動操作
- モデル詳細: モデルバージョンごとの学習日時、評価指標、使用特徴量、カテゴリ特徴量、特徴量重要度、学習パラメータ

設定ページの変更は、保存ボタンを押すまで反映されません。保存後、collector / predictor が次回ジョブ確認を行うタイミングで反映されます。

## ジョブと定期実行

主なジョブ:

| ジョブ | 役割 | 定期実行 |
| --- | --- | --- |
| `collect` | レース、出馬表、オッズ、結果を収集 | 確認間隔を設定 |
| `collect_horses` | 馬の過去戦績と血統を収集 | 確認間隔を設定。初期値は月1回程度 |
| `collect_jockeys` | 騎手の過去戦績を収集 | 確認間隔を設定。初期値は月1回程度 |
| `collect_trainers` | 調教師の過去戦績を収集 | 確認間隔を設定。初期値は月1回程度 |
| `predict` | 未確定レースに AI 予測を作成 | 確認間隔を設定 |
| `bet_decide` | 予測と最新オッズから買い目を判定 | 発走前 N 分で実行 |
| `settle` | 払戻、決済結果を反映 | 発走後 N 分で実行 |
| `train` | 確定済みレースからモデルを再学習 | 確認間隔を設定。初期値は月1回程度 |
| `backfill` | 指定期間の過去レース、出馬表、結果を補完 | 手動実行 |
| `backtest` | 指定期間で回収率を検証 | 手動実行 |

各定期実行ジョブは設定ページで有効 / 無効を切り替えられるほか、実行する曜日(月〜日)を個別に指定できます。対象外の曜日には実行されず、次回予定日時も対象曜日へ繰り上げて表示します。

`bet_decide` と `settle` は固定の実行間隔を設定しません。内部では短い間隔で対象レースを確認し、レース一覧の発走時刻を基準に、設定された発走前または発走後のタイミングで実行します。

`bet_decide` は判定直前に対象レースのデータを再収集し、できるだけ直前の単勝・複勝・馬連・ワイドのオッズで買い目を判断します。

手動ジョブは Web UI のジョブページから実行できます。過去データ取得(バックフィル)はモデル学習用の初期データ準備として使う想定で、指定期間のレース、出馬表、結果を取得します。馬の過去戦績と血統、騎手・調教師の過去戦績は、バックフィル後に必要に応じて `collect_horses`、`collect_jockeys`、`collect_trainers` を実行して収集します。

ジョブページでは、手動実行とは別にジョブ予約を作成できます。実行日時、ジョブ名、必要に応じたパラメータを指定しておくと、指定時刻以降に collector / predictor が予約を通常のジョブキューへ投入します。`backfill` と `backtest` の予約では開始日と終了日を指定します。予約一覧は最新5行、実行履歴は最新15行ずつ表示し、ページ移動で過去分を確認できます。

## 設定

`.env` は起動時のデフォルト値です。賭け設定と定期実行設定は Web UI の設定ページから変更でき、`app_settings` テーブルの値が `.env` より優先されます。

主な環境変数:

| 変数 | 内容 |
| --- | --- |
| `DATABASE_URL` | PostgreSQL 接続先 |
| `COLLECT_INTERVAL_MINUTES` | データ収集の既定間隔 |
| `PREDICT_INTERVAL_MINUTES` | AI 予想の既定間隔 |
| `COLLECT_HORSES_INTERVAL_MINUTES` | 馬過去戦績収集の既定間隔 |
| `COLLECT_JOCKEYS_INTERVAL_MINUTES` | 騎手過去戦績収集の既定間隔 |
| `COLLECT_TRAINERS_INTERVAL_MINUTES` | 調教師過去戦績収集の既定間隔 |
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
| `JOCKEY_RESULTS_PER_RUN` | 1回の騎手過去戦績収集で取得する騎手数 |
| `JOCKEY_RESULTS_REFRESH_DAYS` | 騎手過去戦績を再取得する間隔 |
| `TRAINER_RESULTS_PER_RUN` | 1回の調教師過去戦績収集で取得する調教師数 |
| `TRAINER_RESULTS_REFRESH_DAYS` | 調教師過去戦績を再取得する間隔 |
| `SCRAPER_REQUEST_INTERVAL_SECONDS` | スクレイピング間隔 |
| `IPAT_*` | IPAT 投票に使う認証情報 |
| `IPAT_DRY_RUN` | `true` の間は実購入ボタンを押さずに確認まで行う |
| `ADMIN_LOGIN_ID` / `ADMIN_PASSWORD` | Web UI 管理ログイン情報 |

`BET_DECIDE_INTERVAL_MINUTES` と `SETTLE_INTERVAL_MINUTES` は使いません。買い目判定と決済は発走時刻基準で制御します。

## 収集データ

`collector` は以下を保存します。

- レース情報: 日付、競馬場、レース番号、レース名、発走時刻、距離、芝/ダート、右左回り、馬場、天候、クラス
- 出走馬: 馬番、馬 ID、馬名、性別、馬齢、騎手、騎手 ID、調教師、調教師 ID、斤量、馬体重(増減)、事前単勝オッズ、確定単勝オッズ、人気、着順
- 券種別オッズ: 単勝、複勝、馬連、ワイドの判定時オッズ
- 結果: 着順、払戻、確定後オッズ、人気
- 馬データ: 馬 ID、馬名、父馬 ID、父馬名、過去戦績の取得日時
- 過去戦績: レース日、競馬場、頭数、枠番、馬番、オッズ、人気、着順、騎手、斤量、距離、馬場、タイム、着差、通過順、上がり 3F、馬体重
- 騎手データ: 騎手 ID、騎手名、過去戦績の取得日時
- 騎手過去戦績: レース日、競馬場、レース名、馬 ID、馬名、馬番、調教師、斤量、オッズ、人気、着順、距離、馬場
- 調教師データ: 調教師 ID、調教師名、過去戦績の取得日時
- 調教師過去戦績: レース日、競馬場、レース名、馬 ID、馬名、馬番、騎手、斤量、オッズ、人気、着順、距離、馬場

出馬表の通常 HTML だけで単勝オッズや人気が取れない場合があるため、通常の HTML/API 取得に加えて Playwright + Chromium で JavaScript 反映後の出馬表も確認します。

## モデルと買い目判定

モデルは LightGBM を使い、確定済みレースから各出走馬が 1 着になる確率を予測します。校正には `IsotonicRegression` を使います。学習済みモデルはコンテナ内の `/app/data/model.pkl` に保存されます。
モデルバージョンは `KByyyyMMdd-HHmmss` 形式です。学習時に `model_versions` テーブルへ評価指標、特徴量一覧、カテゴリ特徴量、特徴量重要度、学習パラメータを保存します。特徴量や指標は JSON として保存するため、今後項目が増減してもモデルバージョンごとの記録を残せます。

主な特徴量:

- 基本特徴量: `horse_number`, `age`, `weight`, `horse_weight`, `horse_weight_diff`, `field_size`, `distance`
- 季節特徴量: `season_sin`, `season_cos`(開催日を 1 年周期に写像し、年末年始の不連続を避けつつ季節変動を表現)
- カテゴリ特徴量: `sex`, `jockey_id`, `trainer_id`, `sire_id`
- 過去戦績特徴量: `career_starts`, `win_rate`, `place_rate`, `avg_finish_recent3`, `avg_finish_recent5`, `best_last3f_recent5`, `avg_last3f_recent5`, `days_since_last`, `distance_change`, `same_dist_starts`, `same_dist_avg_finish`, `same_surface_starts`, `same_surface_avg_finish`
- 騎手過去戦績特徴量: `jockey_starts`, `jockey_win_rate`, `jockey_place_rate`, `jockey_avg_finish_recent10`, `jockey_same_dist_starts`, `jockey_same_dist_win_rate`, `jockey_same_surface_starts`, `jockey_same_surface_win_rate`
- 調教師過去戦績特徴量: `trainer_starts`, `trainer_win_rate`, `trainer_place_rate`, `trainer_avg_finish_recent10`, `trainer_same_dist_starts`, `trainer_same_dist_win_rate`, `trainer_same_surface_starts`, `trainer_same_surface_win_rate`

過去戦績特徴量は対象レース日より前の `horse_results`、`jockey_results`、`trainer_results` のみから作ります。単勝オッズと人気は収集、表示、買い目判定、バックテストに使いますが、現在のモデル特徴量には含めていません。

買い目判定では、単勝・複勝・馬連・ワイドの候補を作り、予測確率とオッズから期待値を計算します。単勝はモデルの 1 着確率、複勝・馬連・ワイドはその確率からの近似を使います。作成した買い目には根拠になった予測モデルバージョンを保存します。Web UI では券種別回収率、オッズ取得状況、買い目候補、買い目ごとのモデルバージョンを確認できます。

買い目は `sim` と `prod` のモードを持ちます。`sim` は投票せず記録のみ、`prod` は IPAT 投票処理を試行します。`IPAT_DRY_RUN=true` の間は実購入直前までの確認に留めます。

## 注意

- netkeiba の画面構造や API レスポンスが変わると、スクレイピング処理の修正が必要になる場合があります。
- 馬・騎手・調教師の過去戦績の収集件数を増やすと外部サイトへのアクセスも増えます。`SCRAPER_REQUEST_INTERVAL_SECONDS` を適切に設定してください。
- モデルの性能は保存済みデータ量と過去戦績の充実度に大きく依存します。
- `prod` モードと `IPAT_DRY_RUN=false` の組み合わせは実購入につながります。必ず実機で画面遷移と購入フローを確認してから使ってください。

## ライセンス

[MIT License](LICENSE)
