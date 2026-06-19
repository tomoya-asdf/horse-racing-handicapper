# アーキテクチャ

このドキュメントは、現在の実装に合わせてデータ収集、DB 構造、特徴量生成、ジョブ実行、Web UI の全体像をまとめます。

## 全体像

```text
netkeiba
  |
  | race list / shutuba / odds API / result / horse DB
  v
collector
  |
  | races, entries, horses, horse_results
  v
PostgreSQL
  ^
  | predictions, model_versions, bets, job_runs, job_reservations, app_settings
  |
predictor  <---- model.pkl
  ^
  | API / settings / job queue
  |
webui
```

## サービス

### db

PostgreSQL です。各サービスは同じ DB を参照します。

### collector

レース、出馬表、オッズ、人気、結果、払戻、馬の過去戦績、血統を収集します。通常の HTML/API で不足する単勝オッズと人気は、Playwright + Chromium で出馬表を描画して補完します。

主なジョブ:

- `collect`: 直近レースの収集、終了レースの結果更新
- `collect_horses`: 馬の過去戦績と血統をまとめて更新
- `backfill`: 指定期間の過去レース、結果、出走馬の過去戦績、血統を補完

`collect` と `backfill` は出馬表を取りに行く前に netkeiba の開催カレンダーを取得・保存し、**開催日にだけ**リクエストします。これにより開催の無い平日への無駄なアクセスを省きます(後述「開催カレンダー」)。

騎手・調教師の過去戦績は専用ジョブを持ちません。収集済みの出走データ(レース×出走表)から特徴量生成時にそのまま集計します(後述)。

collector / predictor の各サービスは、`main.py` を「スケジューラ + ジョブ配線」だけに保ち、業務ロジックを責務ごとのモジュールに分けています(collector: `calendar_store` / `races_store` / `horse_results`、predictor: `tasks` / `scheduling`)。netkeiba スクレイパーも `src/collector/scraper/` パッケージに分割しています(`_core` 共有基盤 + `calendar` / `odds` / `rendered` / `races` / `results` / `horses`)。

### predictor

学習済みモデルを使って出走馬ごとの勝率を予測し、条件を満たす買い目を作成します。本番モードでは IPAT 投票処理も実行します。

主なジョブ:

- `train`: 確定結果のあるレースからモデルを学習
- `predict`: 今後の未確定レースに予測を保存
- `bet_decide`: 発走前の対象レースに買い目を作成
- `settle`: 発走後の購入済みレースに払戻結果を反映
- `backtest`: 指定期間でバックテスト

`bet_decide` は実行直前に対象レースの出馬表を再取得し、直前の単勝オッズを DB に反映したうえで、複勝・馬連・ワイドなどの券種別オッズも取得して買い目を判定します。

### webui

FastAPI の API と React のビルド済みフロントエンドを配信します。

主な画面:

- 概要(回収率や予測モデルの要約。予測モデルカードから学習モデル一覧へのリンクを持つ。戦績データの見出し横に「収集済み N / 収集対象 M レース」を表示)
- レース一覧 / 詳細(日付、競馬場、状態、馬名、騎手、厩舎などで検索・絞り込み。日付検索カレンダーは収集済みの日を青、netkeiba カレンダー上の開催日だが未収集の日を赤文字で示す)
- 馬詳細
- 買い目一覧(シミュレーション / 本番をボタンで切り替え。本番ボタンは赤で明示)
- 学習モデル一覧(過去に学習した全モデルの比較・分析。`/models`)
- ジョブ
- 設定
- モデル詳細

概要、レース詳細、買い目一覧に表示されるモデルバージョンはモデル詳細ページへリンクします。モデル詳細ページでは学習日時、評価指標、使用特徴量、カテゴリ特徴量、特徴量重要度(gain・欠損率併記)、確率較正(キャリブレーション)、学習パラメータを表示します。

学習モデル一覧ページは、全モデルバージョンの検証 AUC / logloss の推移グラフと一覧表を表示し、稼働中・ベストのモデルを示します。各ページのモデルバージョンリンクは新しいタブで詳細を開きます。

ジョブページは手動実行、バックフィル、長期バックフィル予約(開始年月・終了年月で期間指定)、バックテスト、ジョブ予約、予約一覧、実行履歴を扱います。定期実行設定とシステム再起動・ソフトウェア更新(デプロイ)操作は設定ページの「管理操作」で扱います。

設定ページの「管理操作」には、ホスト側デプロイエージェントと連携するソフトウェア更新があります。Web UI から `POST /api/system/deploy` を呼ぶとデプロイ要求ファイルが書かれ、ホストで常駐するデプロイエージェント(`scripts/deploy_agent.sh`(Linux/Debian)/ `scripts/deploy_agent.ps1`(Windows))が `git pull` とコンテナ再ビルド・再起動を実行します。エージェントは現在/最新バージョンとデプロイ状態をステータスファイルに書き出し、`GET /api/system/version` がそれを返して画面に表示します。要求・ステータスのやり取りは webui にマウントされた `./data` ディレクトリ上の JSON ファイルで行います。

コンテナ再起動(`POST /api/system/restart`)も同じエージェント方式で行います。webui コンテナは docker.sock を持たないため、再起動要求ファイル(`restart_request.json`)を `./data` に書き、エージェントが `docker compose restart collector predictor webui` を実行します。

## データモデル

### races

レース単位の情報です。

| カラム | 内容 |
| --- | --- |
| `race_key` | netkeiba のレース ID |
| `race_date` | 開催日 |
| `venue` | 競馬場 |
| `race_number` | レース番号 |
| `race_name` | レース名 |
| `start_time` | 発走時刻 |
| `distance` | 距離 |
| `track_type` | 芝、ダートなど |
| `direction` | 右、左など |
| `going` | 馬場状態 |
| `weather` | 天候 |
| `race_class` | クラス |

### entries

出走馬単位の情報です。`race_id` と `horse_number` の組み合わせがユニークです。

| カラム | 内容 |
| --- | --- |
| `race_id` | レース ID |
| `horse_number` | 馬番 |
| `horse_id` | 馬 ID |
| `horse_name` | 馬名 |
| `sex` | 性別(牡/牝/セ) |
| `age` | 馬齢 |
| `jockey` | 騎手名 |
| `jockey_id` | 騎手 ID |
| `trainer` | 調教師名 |
| `trainer_id` | 調教師 ID |
| `weight` | 斤量 |
| `horse_weight` | 馬体重(kg) |
| `horse_weight_diff` | 馬体重の前走比増減 |
| `odds` | 互換用の単勝オッズ(収集時点の最新値) |
| `pre_race_odds` | 発走前に取得した事前単勝オッズ |
| `final_odds` | 発走後に取得した確定単勝オッズ |
| `popularity` | 人気 |
| `finish_position` | 着順 |

### horses / horse_results

`horses` は馬ごとの基本情報、`horse_results` は馬ごとの過去戦績です。過去戦績は特徴量生成に使うため、対象レース日より前の行だけを参照します。騎手・調教師の過去戦績は専用テーブルを持たず、`entries`(出走表)×`races` から直接集計します。

### horse_pedigree

馬の血統(最大5代血統表)です。1行=1先祖で、`horse_id`・`generation`(1〜5)・`position`(世代内 0..2^generation−1, 父系先)・`ancestor_horse_id`(海外馬等は None)・`ancestor_name` を持ち、`(horse_id, generation, position)` が一意です。

### race_collection_status

馬過去戦績の収集進捗フラグです。`race_id`・`kind`(`horse_results`)・`collected_at` を持ち、`(race_id, kind)` が一意。`races` への列追加は `create_all` が反映しないため、別テーブルで持ちます。

### kaisai_dates

netkeiba 開催カレンダー(`top/calendar.html`)の開催日を保存します。`kaisai_date`(PK)と `fetched_at` を持ちます。`collect` / `backfill` は月単位でカレンダーを取得し、取得できた月はその月全体の開催日を保存します。収集側は開催日にだけ出馬表を取りに行き、レース日付検索カレンダーでは「開催日だが未収集」を判別するのにも使います。

### predictions

出走馬ごとの予測スコアです。`entry_id` と `model_version` の組み合わせで管理します。同じモデルバージョンの予測が既にある場合は重複保存しません。較正後の確率 `score` と、較正前の生スコア `raw_score` の両方を保存します。AI 順位は `raw_score` で付け、期待値は `score`(較正後確率)で計算します。競馬に準じてタイブレークは行わず、生スコアが同値なら同順位(同着)として表示します。

### model_versions

学習した予測モデルのメタデータです。`version` は `KByyyyMMdd-HHmmss` 形式です。

| カラム | 内容 |
| --- | --- |
| `version` | モデルバージョン |
| `trained_at` | 学習日時 |
| `race_count` / `row_count` / `valid_race_count` | 学習に使ったレース数、行数、検証レース数 |
| `auc` / `logloss` | 検証指標 |
| `n_estimators` | 学習後の推定器数 |
| `calibrated` | 校正器を作成できたか |
| `feature_columns` | 使用特徴量一覧(JSON) |
| `categorical_features` | カテゴリ特徴量一覧(JSON) |
| `feature_importances` | 特徴量重要度(JSON。gain 値と各特徴量の欠損率を含む) |
| `metrics` | 追加評価指標(JSON。全特徴量の欠損率 `feature_missing_rates` を含む) |
| `training_params` | 学習パラメータ(JSON) |
| `model_path` | モデルファイルパス |

特徴量や評価指標は今後増減する可能性があるため、可変項目は JSON として保存します。既存の古い予測だけに残っているモデルバージョンは、詳細メタデータがない最小情報として Web UI に表示します。

### bets

作成された買い目です。

| カラム | 内容 |
| --- | --- |
| `race_id` / `entry_id` | 対象レース / 馬 |
| `mode` | `sim` または `prod` |
| `status` | `pending`, `placed`, `dry_run`, `failed` |
| `bet_type` | 単勝、馬連など |
| `combination` | 馬連などの組み合わせ |
| `amount` | 購入金額 |
| `odds_at_bet` | 判定時オッズ |
| `model_version` | 買い目判定の根拠になった予測モデルバージョン |
| `payout` | 払戻 |
| `is_settled` | 精算済みか |

### race_odds

券種別のオッズです。単勝は従来通り `entries.odds` にも保存しますが、複勝・馬連・ワイドなど候補比較に使うオッズはこのテーブルに保存します。

| カラム | 内容 |
| --- | --- |
| `race_id` | レース ID |
| `bet_type` | 単勝、複勝、馬連、ワイドなど |
| `combination` | 単勝・複勝は馬番、馬連・ワイドは `4-9` 形式 |
| `odds` | 判定時オッズ |
| `fetched_at` | 取得時刻 |

### job_runs / job_reservations / app_settings

`job_runs` は手動実行、スケジュール実行、予約から投入された実行の履歴を保存します。

`job_reservations` は、指定日時に1回だけジョブを投入する予約を保存します。主なカラムは `job_name`, `run_at`, `params`, `status`, `queued_run_id`, `created_at`, `queued_at`, `cancelled_at` です。`status` は `pending`, `queued`, `cancelled` を使います。

`app_settings` は Web UI から変更できる設定を保存します。

## 設定モデル

`.env` は起動時の既定値です。Web UI から保存された `app_settings` の値がある場合は、ジョブ実行時に `app_settings` が優先されます。

Web UI で変更できる主な設定:

- 賭けモード
- 購入金額
- AI スコア下限
- 期待値下限
- 各定期実行ジョブの有効 / 無効
- 各定期実行ジョブを実行する曜日(月〜日の個別指定。対象外の曜日は実行しない)
- `collect`, `collect_horses`, `predict`, `train` の確認間隔
- `bet_decide` の発走前分数
- `settle` の発走後分数
- モデル学習パラメータ(LightGBM のハイパーパラメータ)
- 使用特徴量の選択(グループ別の ON/OFF。各特徴量には最新学習時点の欠損率を併記)
- 学習期間(学習に使う確定レースの開始日 / 終了日。空欄なら全期間)

設定ページでは、画面上で値を変更しても保存ボタンを押すまで DB へ反映しません。

## ジョブ管理

ジョブは `job_runs` に登録されます。同じジョブがキュー済みまたは実行中の場合は、重複登録しません。一定時間以上 `running` のまま残ったジョブは失敗扱いに戻します。

手動ジョブは Web UI から `job_runs` に登録され、collector / predictor が短い間隔でポーリングして実行します。

ジョブ予約は Web UI から `job_reservations` に登録されます。collector / predictor は自分が担当するジョブの予約をポーリングし、`run_at` を過ぎた `pending` 予約を `trigger=reserved` の `job_runs` に変換します。同じジョブがキュー済みまたは実行中の場合、その予約は次回ポーリングまで `pending` のまま残します。予約は `pending` の間だけキャンセルできます。

### 定期実行

定期実行対象:

| ジョブ | スケジュール方式 |
| --- | --- |
| `collect` | 前回実行から設定分数経過 |
| `collect_horses` | 前回実行から設定分数経過 |
| `predict` | 前回実行から設定分数経過 |
| `train` | 前回実行から設定分数経過 |
| `bet_decide` | 次の対象レースの発走時刻から N 分前 |
| `settle` | 未精算の購入済みレースの発走時刻から N 分後 |

`bet_decide` と `settle` はユーザー設定の確認間隔を持ちません。内部スケジューラは短い間隔で対象時刻に到達したかだけを確認します。Web UI には確認間隔ではなく、`発走前(分)` と `発走後(分)` を別カラムで表示します。

各ジョブには実行する曜日を設定でき、当日の曜日が対象外であればスケジューラは実行をスキップします。次回予定日時は、保存済み設定(曜日を含む)と DB のレース発走時刻 / ジョブ履歴から計算し、対象曜日でない場合は次の対象曜日へ繰り上げます。

## 収集フロー

### 開催カレンダー

`collect` / `backfill` は出馬表を取りに行く前に、対象期間の各月について netkeiba の開催カレンダー(`top/calendar.html`)を取得し、`kaisai_dates` に保存します。以降は開催日にだけ出馬表・結果を取得するため、開催の無い平日への無駄なリクエストを大幅に削減できます。月の取得に失敗した場合は安全側に倒し、その月の全日を開催日候補として扱います。バックフィルでも同じ仕組みで開催カレンダーが補完されるため、過去に遡る場合でも全日分をリクエストすることはありません。

### レースと出馬表

1. `race_list_sub.html` から対象日のレース ID を取得します。
2. `race/shutuba.html` からレース情報と出走馬を解析します。
3. 馬 ID、馬名、性別、馬齢、騎手 ID、騎手名、調教師、調教師 ID、斤量、馬体重(増減)を保存します。除外 / 取消馬は性齢セルにクラスが付かないため、性齢書式のセルを探すフォールバックで補完します。
4. 静的 HTML 上に単勝オッズ / 人気があれば保存します。
5. netkeiba のオッズ API から単勝オッズを取得します。
6. 不足があれば Playwright で出馬表を描画し、JavaScript 反映後の `odds-*` / `ninki-*` 要素から補完します。
7. 取得できた単勝オッズの順位から人気を補完します。

既存行を更新するとき、オッズや人気の新しい値が取得できた場合だけ上書きします。

### 結果と払戻

終了したレースは `result.html` から着順と払戻を取得します。直近の終了レースは定期収集時にも再確認します。

### 過去戦績の収集(馬: レース起点・漸進的)

馬の過去戦績は **races(レース一覧)を起点**に収集します。`RaceCollectionStatus` に `horse_results` の取得済みフラグを持ち、未収集のレースを新しい順に最大 `RESULTS_RACES_PER_RUN` 件処理し、そのレースの全出走馬の成績を取り切ったらフラグを立てます。各成績は**追記のみ**(既存 `(race_key, horse_id)` はスキップ)で保存し、履歴を消さずに積み増します。

- **馬**: `https://db.netkeiba.com/horse/result/{horse_id}/`(全キャリアが1ページ)。同一馬は run 内で重複取得せず、過去成績が `HORSE_RESULTS_REFRESH_DAYS` 日以内に取得済みかつ血統取得済みの馬はスキップします。

**騎手・調教師はスクレイピングしません。** 騎手/調教師は多くのレースに騎乗/出走するため、自前に蓄積した確定レース(`entries` × `races`)だけで「直近10走の平均着順」等の特徴量を十分カバーできます(個別ページ取得が不要で、最も重かった収集処理を丸ごと排除)。特徴量は `history.load_jockey_history` / `load_trainer_history` が `entries` から点推定(対象レース日より前のみ)で組み立てます。

### 血統(5代血統表)

馬 ID を持つ出走馬の血統を `https://db.netkeiba.com/horse/ped/{horse_id}/` から取得します。血統表(`table.blood_table`)は先祖セルの `rowspan` が世代を表す(5代なら gen1=16〜gen5=1)ため、`gen = total_gens − log2(rowspan)` で世代を、同一 rowspan のセルの文書順(上→下=父系先)で `position` を決め、最大5代(最大62先祖)を `HorsePedigree` に保存します。父(gen1/position0)は後方互換のため `horses.sire_id` / `sire_name` にも保存します。血統が未取得の馬だけ取得します(`HorsePedigree` 行の有無で判定)。

## 特徴量生成

特徴量の集合・ラベル・グループは `src/common/feature_catalog.py` に一元管理し、実際の値は `src/predictor/features.py` と `src/predictor/history.py` で作ります。`feature_catalog.py` は pandas / numpy / lightgbm に依存しません。API イメージが ML 系ライブラリを持たないため、特徴量の「定義」だけを切り出して predictor 側(features/history)と API 側(dynamic_config)の双方から参照します。

学習・予測・バックテストはいずれも `history.build_entries_frame` → `features.build_features` を共通で通すため、特徴量の追加はこの 2 つに実装すれば 3 経路すべてに反映されます。

基礎(出馬表):

- `horse_number`, `age`, `weight`, `horse_weight`, `horse_weight_diff`, `field_size`, `distance`
- 季節 `season_sin` / `season_cos`(開催日を 1 年周期 `2π × 通算日 / 365.25` の sin / cos に写像。年末年始の不連続を避ける)

枠順・相対値(レース内の出走馬から算出):

- `draw_ratio`(馬番 / 頭数), `weight_rel`(斤量 − レース平均), `horse_weight_rel`(馬体重 − レース平均)

レース条件(カテゴリ + `race_number`):

- `track_type`, `going`, `weather`, `direction`, `race_class`, `venue`

カテゴリ(ID 等):

- `sex`, `jockey_id`, `trainer_id`, `sire_id`

馬の過去戦績特徴量:

- `career_starts`, `win_rate`, `place_rate`, `avg_finish_recent3`, `avg_finish_recent5`, `best_last3f_recent5`, `avg_last3f_recent5`, `days_since_last`, `distance_change`, `same_dist_starts`, `same_dist_avg_finish`, `same_surface_starts`, `same_surface_avg_finish`
- 直近 10 走 `win_rate_recent10` / `place_rate_recent10`、同馬場状態 `same_going_starts` / `same_going_place_rate`

騎手・調教師の過去戦績特徴量(`jockey_*` / `trainer_*`):

- `*_starts`, `*_win_rate`, `*_place_rate`, `*_avg_finish_recent10`, `*_same_dist_starts` / `*_same_dist_win_rate`, `*_same_surface_starts` / `*_same_surface_win_rate`, `*_same_going_starts` / `*_same_going_win_rate`

履歴特徴量は対象レース日より前の過去戦績だけで作り、未来結果のリークを避けます。該当が無い項目は欠損(NaN)のまま LightGBM に渡します。

### 使用特徴量の選択と既定値

設定画面で特徴量をグループ単位で ON/OFF できます。既定 ON は「汎化しやすく欠損の少ない最適セット」(`feature_catalog.DEFAULT_ENABLED_FEATURES`)で、高カードナリティで過学習しやすい生 ID(`jockey_id` / `trainer_id` / `sire_id`)、弱い / 冗長な素性、今回追加した拡張素性(同馬場状態・直近 10 走)は既定 OFF です。生 ID の代わりに騎手・調教師の勝率などの集計特徴量を既定で使います。すべて外した場合は安全のため全特徴量で学習します。

単勝オッズと人気は表示、データ品質確認、買い目判定、バックテストで使いますが、モデル特徴量には含めていません。

## 学習と予測

学習対象は着順が確定しているレースです。各出走馬について `finish_position == 1` を正例にします。設定で学習期間(開始日 / 終了日)を指定でき、未指定なら全期間を使います。

学習手順:

1. 確定レースと出走馬を読み込みます(学習期間が指定されていれば期間で絞り込みます)。
2. レース日順に並べ、古いデータを学習、新しいデータを検証に分けます。
3. LightGBM の二値分類モデルを学習します。
4. 検証データで early stopping します。
5. 検証データの確率で `IsotonicRegression` による校正器を作ります。
6. 全データで最終モデルを学習し、モデル、特徴量一覧、カテゴリ特徴量、校正器、`KB` 付きバージョンを `/app/data/model.pkl` に保存します。
7. 評価指標、特徴量一覧、特徴量重要度、学習パラメータを `model_versions` に保存します。

特徴量重要度は gain(利得)で記録します。LightGBM 既定の split(分岐回数)は高カードナリティな ID が不当に高く出るため、寄与の大きさを表す gain を使います。あわせて学習データの特徴量ごとの欠損率を算出し、`model_versions` に保存して設定画面・モデル詳細で表示します。

予測時は最新モデルを読み込み、未確定の今後レースに対して `predictions` を保存します。

## 買い目判定

買い目判定は発走前の設定分数に到達したレースを対象にします。判定対象の最大ウィンドウは `BET_DECISION_WINDOW_MINUTES` で制限します。

判定前に対象レースの出馬表を再取得し、直前の単勝オッズを DB に反映します。その後、券種別オッズを取得して単勝・複勝・馬連・ワイドの買い目候補を作ります。

単勝:

- 予測スコアが最も高い馬を候補にします。
- `BET_SCORE_THRESHOLD` 以上である必要があります。
- `score * odds` が `BET_MIN_EXPECTED_VALUE` 以上である必要があります。

複勝:

- 各馬の 3 着内確率を Harville 近似で作ります。
- 複勝オッズとの期待値が `BET_MIN_EXPECTED_VALUE` 以上である必要があります。

馬連:

- 上位候補の組み合わせと馬連オッズから期待値を計算します。
- 条件を満たした組み合わせを買い目として保存します。

ワイド:

- 上位候補ペアがともに 3 着内に入る確率を Harville 近似で作ります。
- ワイドオッズとの期待値が `BET_MIN_EXPECTED_VALUE` 以上である必要があります。

期待値を満たす候補は期待値順に並べ、1 レース最大 3 点まで 100 円単位で配分します。レース詳細画面では、出走馬順位表の下、購入済み買い目表示の上に、買い目候補とオッズ取得状況を常時表示します。

同じレース、同じモードの買い目は重複して作成しません。買い目には判定時に使った予測の `model_version` を保存し、Web UI の買い目一覧やレース詳細からモデル詳細を確認できます。

## Web API

主な API:

- `GET /api/races`
- `GET /api/races/{race_id}`
- `GET /api/race-dates`(収集済み・開催予定の日付集合。日付検索カレンダーの色分けに使用)
- `GET /api/horses/{horse_id}`
- `GET /api/bets`
- `GET /api/models`
- `GET /api/models/{version}`
- `GET /api/models/{version}/calibration`
- `GET /api/jobs`
- `POST /api/jobs/{job_name}/run`
- `POST /api/job-reservations`
- `POST /api/job-reservations/{reservation_id}/cancel`
- `PUT /api/jobs/schedule`
- `GET /api/settings`
- `PUT /api/settings`
- `GET /api/system/version` / `POST /api/system/deploy` / `POST /api/system/restart`(バージョン確認・デプロイ・再起動)
- 認証関連エンドポイント

API は `src/api/main.py` を薄く保ち、機能ごとの `APIRouter`(`src/api/routers/` 配下の auth / overview / races / horses / people / models / bets / jobs / settings / system)を取り込みます。共通の依存・直列化は `src/api/deps.py` と `src/api/serializers.py` にまとめています。

`PUT /api/jobs/schedule` はジョブ設定保存用の API として残っていますが、現在の Web UI は設定ページから `PUT /api/settings` にまとめて保存します。

## 運用上の注意

- netkeiba の DOM や API が変わると、スクレイパーの修正が必要です。
- Playwright による描画取得は通常の HTML/API 取得より重いため、必要な場合のフォールバックとして使います。
- 過去戦績の充実度がモデル特徴量に直結します。新しい DB では、まずモデル学習に使う期間を指定して `backfill` を実行してください。バックフィル後も不足がある場合や後続更新をまとめたい場合は `collect_horses` を追加で実行します。
- DB スキーマ変更履歴管理はまだ簡易方式です。破壊的なスキーマ変更を行う場合は、事前に DB バックアップを取ってください。

## 実装上の補足

- 起動時設定(`src/common/config.py`)は pydantic-settings で型・範囲を検証し、不正値は起動時に落とします。
- DB セッションは `src/common/db.py` の `session_scope()`(コンテキストマネージャ)/ `get_db`(FastAPI 依存)で取得し、close と例外時 rollback を一元化します。接続プールは `DB_POOL_SIZE` / `DB_MAX_OVERFLOW` で調整できます。
- 動的設定(`src/common/dynamic_config`)は configs / defaults / parsing / store / schedule / views に分割したパッケージです。外部からは従来どおり `from src.common.dynamic_config import ...` で参照します。
- 管理ログインはサーバ側でセッションの有効期限を持ち(`ADMIN_SESSION_SECONDS`)、ログイン失敗にはレート制限(`ADMIN_LOGIN_MAX_ATTEMPTS` / `ADMIN_LOGIN_WINDOW_SECONDS`)を設けています。HTTPS 配信時は `ADMIN_COOKIE_SECURE=true` で Cookie に Secure 属性を付与します。
- netkeiba への HTTP 取得は一時的な失敗(タイムアウト / 5xx / 429)を指数バックオフで再試行します(`SCRAPER_MAX_RETRIES` / `SCRAPER_RETRY_BACKOFF_SECONDS`)。

## テスト

`tests/` に pytest のユニットテストを置いています(設定検証、動的設定のパース、特徴量カタログ、スクレイパーのパース/リトライ、管理セッション/レート制限)。pandas / lightgbm に依存しない範囲を対象にしており、`pytest` で実行できます。CI(`.github/workflows/ci.yml`)は push / PR で共通・API・collector の依存をインストールして実行します。
