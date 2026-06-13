# アーキテクチャ

このドキュメントは、現在の実装に合わせたデータ収集、DB 構造、特徴量生成、ジョブ実行、Web UI の全体像をまとめます。

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
  | predictions, bets, job_runs, settings
  |
predictor  <---- model.pkl
  |
  | API
  v
webui
```

## サービス

### db

PostgreSQL です。全サービスが同じ DB を参照します。

### collector

レース、出走馬、オッズ、人気、結果、払戻、馬の過去戦績、血統を収集します。

通常の HTML/API で足りない単勝オッズと人気は、Playwright + Chromium で出馬表を描画して補完します。

主なジョブ:

- `collect`: 直近のレース収集、終了レースの結果更新、馬戦績の一部更新
- `collect_horses`: 馬の過去戦績と血統をまとめて更新
- `backfill`: 指定期間の過去レースを補完

### predictor

学習済みモデルを使って出走馬ごとの勝率を予測し、期待値条件を満たす買い目を作成します。

本番モードでは IPAT 投票処理も実行します。

主なジョブ:

- `train`: 確定結果のあるレースからモデルを学習
- `predict`: 今後のレースに予測を保存
- `bet_decide`: 発走前のレースに買い目を作成
- `settle`: 払戻結果を反映
- `backtest`: 指定期間でバックテスト

### webui

FastAPI が API と React のビルド成果物を配信します。

主な画面:

- 概要
- レース一覧/詳細
- 馬詳細
- 買い目一覧
- ジョブ実行
- 設定

## データモデル

### races

レース単位の情報です。

| カラム | 内容 |
| --- | --- |
| `race_key` | netkeiba のレース ID。ユニーク |
| `race_date` | 開催日 |
| `venue` | 競馬場 |
| `race_number` | レース番号 |
| `race_name` | レース名 |
| `start_time` | 発走時刻 |
| `distance` | 距離 |
| `track_type` | 芝/ダートなど |
| `direction` | 右/左など |
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
| `jockey` | 騎手名 |
| `jockey_id` | 騎手 ID |
| `weight` | 斤量 |
| `odds` | 単勝オッズ |
| `popularity` | 人気 |
| `finish_position` | 着順 |

### horses

馬ごとの基本情報です。

| カラム | 内容 |
| --- | --- |
| `horse_id` | 馬 ID。主キー |
| `name` | 馬名 |
| `sire_id` | 父馬 ID |
| `sire_name` | 父馬名 |
| `results_fetched_at` | 過去戦績を最後に取得した時刻 |

### horse_results

馬ごとの過去戦績です。`horse_id` と `race_key` の組み合わせがユニークです。

| カラム | 内容 |
| --- | --- |
| `horse_id` | 馬 ID |
| `race_key` | レース ID |
| `race_date` | レース日 |
| `venue` | 競馬場 |
| `race_name` | レース名 |
| `field_size` | 頭数 |
| `post_position` | 枠番 |
| `horse_number` | 馬番 |
| `odds` | 単勝オッズ |
| `popularity` | 人気 |
| `finish_position` | 着順 |
| `jockey` / `jockey_id` | 騎手 |
| `weight` | 斤量 |
| `distance` | 距離 |
| `track_type` | 芝/ダート |
| `going` | 馬場状態 |
| `time_seconds` | 走破タイム秒 |
| `margin` | 着差 |
| `passing` | 通過順 |
| `last_3f` | 上がり 3F |
| `horse_weight` | 馬体重 |

### predictions

出走馬ごとの予測スコアです。`entry_id` と `model_version` の組み合わせがユニークです。

### bets

作成された買い目です。

| カラム | 内容 |
| --- | --- |
| `race_id` / `entry_id` | 対象レース/馬 |
| `mode` | `sim` または `prod` |
| `status` | `pending`, `placed`, `dry_run`, `failed` |
| `bet_type` | 単勝、馬連など |
| `combination` | 馬連などの組み合わせ |
| `amount` | 購入金額 |
| `odds_at_bet` | 判定時オッズ |
| `payout` | 払戻 |
| `is_settled` | 精算済みか |

### job_runs / app_settings

ジョブ履歴とアプリ設定を保存します。ジョブ履歴にはパラメータも保存します。

## DB 初期化とマイグレーション

起動時に SQLAlchemy の `Base.metadata.create_all()` で未作成テーブルを作成します。

既存 DB に対しては、以下のような追加カラムやインデックスを簡易的に補完します。

- `entries`: `jockey_id`, `popularity`, `horse_id`
- `races`: `distance`, `track_type`, `direction`, `going`, `weather`, `race_class`
- `horses`: `sire_id`, `sire_name`
- `bets`: `combination`
- `entries.horse_id` のインデックス

Alembic のような履歴付きマイグレーションは未導入です。

## 収集フロー

### レースと出馬表

1. `race_list_sub.html` から対象日のレース ID を取得します。
2. `race/shutuba.html` からレース情報と出走馬を解析します。
3. 出走馬の馬 ID、馬名、騎手 ID、騎手名、斤量を保存します。
4. 静的 HTML 上に単勝オッズ/人気があれば保存します。
5. netkeiba のオッズ API から単勝オッズを取得します。
6. 不足があれば Playwright で出馬表を描画し、JavaScript 反映後の `odds-*` / `ninki-*` 要素から未確定オッズと人気を取得します。
7. まだ人気が不足している場合は、単勝オッズの昇順から補完します。

出走馬の既存行を更新するとき、オッズと人気は新しい値が取得できた場合だけ上書きします。

### 結果と払戻

終了したレースは `result.html` から着順と払戻を取得します。

直近の終了レースは定期収集時に再確認されます。

### 馬の過去戦績と血統

馬 ID を持つ出走馬を対象に、以下を取得します。

- `https://db.netkeiba.com/horse/result/{horse_id}/`
- `https://db.netkeiba.com/horse/ped/{horse_id}/`

過去戦績は馬ごとに既存行を削除してから最新の一覧を保存します。血統は現在、父馬 ID と父馬名を特徴量用に保存します。

## 特徴量生成

特徴量は `src/predictor/features.py` と `src/predictor/history.py` で作られます。

### 基本特徴量

- `horse_number`
- `weight`
- `field_size`
- `distance`

### カテゴリ特徴量

- `jockey_id`
- `sire_id`

未取得の場合は `unknown` として扱います。

### 過去戦績特徴量

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

対象レースより前の `horse_results` だけを使用します。

同距離実績は対象距離から 200m 以内を同距離として扱います。

現在のモデルでは、単勝オッズと人気を特徴量に入れていません。これらは表示、収集品質確認、買い目判定、バックテストで使います。

## 学習と予測

学習対象は着順が確定しているレースです。各出走馬について `finish_position == 1` を正例にします。

学習手順:

1. 確定レースと出走馬を読み込みます。
2. レース日順に並べ、古いデータを学習、新しいデータを検証に分けます。
3. LightGBM の二値分類モデルを学習します。
4. 検証データで early stopping します。
5. 検証データの確率で `IsotonicRegression` による校正器を作ります。
6. 全データで最終モデルを学習し、モデル、特徴量一覧、カテゴリ特徴量、校正器、バージョンを `/app/data/model.pkl` に保存します。

予測時は最新モデルを読み込み、未確定の今後レースに対して `predictions` を保存します。同じ `model_version` の予測が既にある場合は重複保存しません。

## 買い目判定

買い目判定は発走前の一定時間内にあるレースを対象にします。時間幅は `BET_DECISION_WINDOW_MINUTES` で設定します。

単勝:

- 予測スコアが最も高い馬を候補にします。
- `BET_SCORE_THRESHOLD` を満たす必要があります。
- `score * odds` が `BET_MIN_EXPECTED_VALUE` 以上である必要があります。

馬連:

- 上位候補の組み合わせと馬連オッズを使って期待値を計算します。
- 条件を満たした組み合わせを買い目として保存します。

同じレース、同じモードの買い目は重複して作成しません。

## ジョブ管理

ジョブは `job_runs` に記録されます。主なジョブ名は以下です。

- `collect`
- `backfill`
- `collect_horses`
- `predict`
- `bet_decide`
- `settle`
- `train`
- `backtest`

同じジョブがキュー済みまたは実行中の場合は重複登録しません。一定時間以上 `running` のまま残ったジョブは失敗扱いに戻します。

`predictor` は起動時に `predict` と `bet_decide` を一度実行し、その後は設定された間隔で定期実行します。キュー済みジョブは短い間隔でポーリングされます。

## Web API

主な API:

- `GET /api/races`
- `GET /api/races/{race_id}`
- `GET /api/horses/{horse_id}`
- `GET /api/bets`
- `GET /api/jobs`
- `POST /api/jobs/{job_name}/run`
- `GET /api/settings`
- `POST /api/settings`
- 認証関連エンドポイント

フロントエンドの `/horses/{horse_id}` は馬詳細ページです。レース一覧の馬名リンクから別ページで開けます。

## 運用上の注意

- netkeiba の DOM や API が変わると、スクレイパーの修正が必要です。
- Playwright による描画取得は通常の HTML/API 取得より重いため、必要な場合だけフォールバックとして使います。
- 過去戦績の充実度がモデル特徴量に直結します。新しい DB ではまず `backfill` と `collect_horses` を十分に実行してください。
- DB スキーマの変更履歴管理はまだ簡易方式です。破壊的なスキーマ変更を行う場合は、事前に DB バックアップを取ってください。
