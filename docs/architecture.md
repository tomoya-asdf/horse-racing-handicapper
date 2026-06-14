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
  | predictions, bets, job_runs, app_settings
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

- `collect`: 直近レースの収集、終了レースの結果更新、馬過去戦績の一部更新
- `collect_horses`: 馬の過去戦績と血統をまとめて更新
- `backfill`: 指定期間の過去レース、結果、出走馬の過去戦績、血統を補完

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

- 概要
- レース一覧 / 詳細
- 馬詳細
- 買い目一覧
- ジョブ
- 設定

ジョブページは手動実行、バックフィル、バックテスト、実行履歴を扱います。定期実行設定は設定ページで扱います。

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

`horses` は馬ごとの基本情報、`horse_results` は馬ごとの過去戦績です。過去戦績は特徴量生成に使うため、対象レース日より前の行だけを参照します。

### predictions

出走馬ごとの予測スコアです。`entry_id` と `model_version` の組み合わせで管理します。同じモデルバージョンの予測が既にある場合は重複保存しません。

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

### job_runs / app_settings

`job_runs` は手動実行とスケジュール実行の履歴を保存します。`app_settings` は Web UI から変更できる設定を保存します。

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

設定ページでは、画面上で値を変更しても保存ボタンを押すまで DB へ反映しません。

## ジョブ管理

ジョブは `job_runs` に登録されます。同じジョブがキュー済みまたは実行中の場合は、重複登録しません。一定時間以上 `running` のまま残ったジョブは失敗扱いに戻します。

手動ジョブは Web UI から `job_runs` に登録され、collector / predictor が短い間隔でポーリングして実行します。

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

### 馬の過去戦績と血統

馬 ID を持つ出走馬を対象に以下を取得します。

- `https://db.netkeiba.com/horse/result/{horse_id}/`
- `https://db.netkeiba.com/horse/ped/{horse_id}/`

過去戦績は馬ごとに既存行を削除してから最新一覧を保存します。血統は父馬 ID と父馬名を特徴量用に保存します。

## 特徴量生成

特徴量は `src/predictor/features.py` と `src/predictor/history.py` で作ります。

基本特徴量:

- `horse_number`
- `age`
- `weight`
- `horse_weight`
- `horse_weight_diff`
- `field_size`
- `distance`

季節特徴量:

- `season_sin`
- `season_cos`

開催日を 1 年周期(`2π × 通算日 / 365.25`)の sin / cos に写像します。月番号をそのまま使うと年末年始(12 月と 1 月)で不連続になるため、周期性を保ったまま季節変動をモデルへ渡します。

カテゴリ特徴量:

- `sex`
- `jockey_id`
- `trainer_id`
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

単勝オッズと人気は表示、データ品質確認、買い目判定、バックテストで使いますが、現在のモデル特徴量には含めていません。

## 学習と予測

学習対象は着順が確定しているレースです。各出走馬について `finish_position == 1` を正例にします。

学習手順:

1. 確定レースと出走馬を読み込みます。
2. レース日順に並べ、古いデータを学習、新しいデータを検証に分けます。
3. LightGBM の二値分類モデルを学習します。
4. 検証データで early stopping します。
5. 検証データの確率で `IsotonicRegression` による校正器を作ります。
6. 全データで最終モデルを学習し、モデル、特徴量一覧、カテゴリ特徴量、校正器、バージョンを `/app/data/model.pkl` に保存します。

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

期待値を満たす候補は期待値順に並べ、1 レース最大 3 点まで 100 円単位で配分します。レース詳細画面では、出走馬情報を主表示し、買い目候補とオッズ取得状況は開閉式の補助情報として確認できます。

同じレース、同じモードの買い目は重複して作成しません。

## Web API

主な API:

- `GET /api/races`
- `GET /api/races/{race_id}`
- `GET /api/horses/{horse_id}`
- `GET /api/bets`
- `GET /api/jobs`
- `POST /api/jobs/{job_name}/run`
- `PUT /api/jobs/schedule`
- `GET /api/settings`
- `PUT /api/settings`
- 認証関連エンドポイント

`PUT /api/jobs/schedule` はジョブ設定保存用の API として残っていますが、現在の Web UI は設定ページから `PUT /api/settings` にまとめて保存します。

## 運用上の注意

- netkeiba の DOM や API が変わると、スクレイパーの修正が必要です。
- Playwright による描画取得は通常の HTML/API 取得より重いため、必要な場合のフォールバックとして使います。
- 過去戦績の充実度がモデル特徴量に直結します。新しい DB では、まずモデル学習に使う期間を指定して `backfill` を実行してください。バックフィル後も不足がある場合や後続更新をまとめたい場合は `collect_horses` を追加で実行します。
- DB スキーマ変更履歴管理はまだ簡易方式です。破壊的なスキーマ変更を行う場合は、事前に DB バックアップを取ってください。
