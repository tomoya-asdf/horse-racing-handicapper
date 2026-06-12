# アーキテクチャ詳細

## 全体フロー

    [collector] --定期実行--> レース/オッズ/結果を取得 --> DB(races, entries)

    [predictor] --定期実行--> 未予測レースの特徴量生成 --> モデル推論 --> DB(predictions)
                          --> 賭け戦略で賭け対象を決定
                                ├─ mode=sim:  DBに記録のみ
                                └─ mode=prod: 実際に購入 + DBに記録
                          --> 確定済みレースの結果から payout を計算 --> DB(bets更新)

    [webui] (React + FastAPI)
        ├─ 参照: 状況・レース・賭け履歴・ジョブ履歴を表示
        ├─ 実行依頼: DB(job_runs)へ登録 --> collector/predictorがポーリングして実行
        └─ 設定変更: DB(app_settings)へ保存 --> 各ジョブが実行のたびに読み直す

## データモデル

| テーブル | 内容 |
| --- | --- |
| `races` | レース基本情報（開催日・競馬場・レース番号・レース名など） |
| `entries` | 出走馬情報（馬番・馬名・騎手・オッズ・確定着順）。`(race_id, horse_number)` でユニーク |
| `predictions` | モデルによる予測スコア（`model_version` でモデルを識別）。`(entry_id, model_version)` でユニーク |
| `bets` | 賭け記録（`mode`=`prod`/`sim`、`status`、賭け式・金額・払戻金・確定フラグ） |
| `job_runs` | ジョブの実行キュー兼実行履歴（手動・スケジュール両方） |
| `app_settings` | WebUIから変更できる設定のキー/値ストア（.envの値を上書き） |

`src/common/models.py` にSQLAlchemyモデルとして定義されており、各サービス起動時の
`init_db()` 呼び出しでテーブルが自動作成されます（マイグレーションの仕組みは無いため、
既存テーブルへの列・制約の追加は反映されません。スキーマ変更時は README の
「DBスキーマの変更について」を参照してください）。

### `bets.status`（購入状態）

実購入(prod)の安全性のため、`bets` は購入操作の **前** にDBへコミットされ、
`status` で購入状態を管理します。

- `pending`: prodで購入操作の開始前に記録された状態。購入処理の途中でプロセスが
  落ちるとこの状態のまま残る（実際に購入されたかはIPATの投票履歴で要確認）
- `placed`: simでの記録、またはprodで購入操作が成功した状態
- `failed`: prodで購入操作が失敗した状態（実際のお金は動いていない）

次回の予測ジョブは同一レース・同一モードの `bets` が存在すれば賭けをスキップするため、
途中でクラッシュしても同じレースへ重複購入しません（フェイルクローズ）。
決済(`settlement`)と回収率の集計は `status=placed` のみを対象とします。

## 時刻の扱い

レースの発走時刻はJSTのため、DB内のdatetimeはすべて **naiveなJST** で統一します。
現在時刻の取得は `src/common/timeutils.py` の `now_jst()` を使用し、コンテナの
システムタイムゾーンに依存しません（Dockerfileでも `TZ=Asia/Tokyo` を設定）。

## モードの分離

`bets.mode` カラムで `prod` / `sim` を区別します。`predictor` サービスは
賭けモード（WebUIの設定、未設定なら `.env` の `BETTING_MODE`）に応じて、
予測結果に基づく賭けを

- `sim` モード: `bets` テーブルに記録するのみ
- `prod` モード: `bets` テーブルへの記録に加えて `betting.place_bet_production()` で実際の購入操作を行う

という形で扱います。WebUIは `mode` でフィルタして回収率を個別に算出するため、
本番運用と並行してシミュレーション（バックテスト）の実績を蓄積・比較できます。

## 設定の2層構造（`src/common/dynamic_config.py`）

- `.env`（`src/common/config.py`）: DB接続・ジョブ間隔・IPAT認証情報などの静的設定。
  変更にはコンテナの再起動が必要
- `app_settings`: 賭けモード・賭け金額・スコア閾値・期待値下限。WebUIから変更でき、
  各ジョブが実行のたびに `load_betting_config()` で読み直すため再起動不要。
  値が無い・不正な場合は `.env` の値にフォールバックする

## スケジューリングとジョブ実行（`src/common/jobs.py`）

`collector` / `predictor` はコンテナ内で `APScheduler` の `BlockingScheduler` を起動し、
`.env` の `COLLECT_INTERVAL_MINUTES` / `PREDICT_INTERVAL_MINUTES` に従ってジョブを
定期実行します（タイムゾーンは `Asia/Tokyo`）。`predictor` は予測ジョブと、確定レースの
払い戻しを反映する決済ジョブの2つを持ちます。

すべてのジョブ実行は `job_runs` テーブルに記録されます。WebUIからの手動実行は
APIが `status=queued` の行を登録し、担当サービス（collect→collector、
predict/settle/train→predictor）が5秒間隔のポーリングで取得して実行します。

- 同名ジョブが実行待ち/実行中の間は、新たな実行依頼を積まない（重複実行の防止）
- サービス起動時、前回の異常終了で `running` のまま残った行は `failed` に倒す
- ジョブの実体は `collect` / `predict` / `settle`（定期実行あり）と
  `backfill` / `train`（手動またはCLIのみ）の5つ
- ジョブへの引数は `job_runs.params`（JSON）で渡す。`backfill` は
  `{"start_date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD"}` を取り、
  日付範囲のバリデーション（過去日付のみ・最大31日）はAPI側で行う

## webuiサービス（`src/api/` + `webui/`）

FastAPI製のAPIが参照・実行依頼・設定変更のエンドポイント（`/api/*`）を提供し、
ビルド済みのReactフロントエンド（`webui/dist`）も同じプロセスから配信します。
ジョブはAPIプロセス内では実行しないため、webuiの停止・再起動は収集・予測の
定期実行に影響しません。認証は無いため、ポートはループバックのみに公開しています。

## 実装メモ

### `src/collector/scraper.py`（netkeiba.com）

- `fetch_upcoming_races(target_date)`: `race_list_sub.html` から指定日の `race_id`
  一覧・発走時刻を取得し、各 `race_id` の `shutuba.html`（出走馬）と
  `api_get_jra_odds.html`（単勝オッズAPI）を組み合わせて出走馬情報を構築する
  （発走時刻を過ぎたレースは除外する）
- collectorは当日から `COLLECT_DAYS_AHEAD` 日先（既定3日）までを毎回収集する。
  JRAは主に土日開催のため、当日のみだと平日は常に0件になる。先読み収集した
  レースの出走馬・オッズは収集のたびにupsertで最新化される
- 過去レースの一括取得は `src/collector/backfill.py`（CLI専用）。
  `include_started=True` で発走済みレースも収集し、確定結果まで反映する。
  netkeibaのオッズAPIは過去レースにも最終オッズを返すため特徴量も揃う
- `fetch_race_results(race_key)`: `result.html` から確定着順と払い戻し
  （`単勝`/`複勝`）を取得する。出走取消・除外・競走中止の馬は着順が数値に
  ならないため結果に含まれない（`entries.finish_position` はNoneのまま残る）
- collectorの結果反映は「1頭でも着順が反映済みのレース」をスキップし、
  発走から7日を過ぎたレースは（結果が取れないままでも）対象から外す
  （開催中止等のレースを無限に再取得しないため）
- `race_key`（= `race_id`）は12桁の文字列で、`YYYY` + 競馬場コード(2) + 回(2) +
  日(2) + レース番号(2) から成る。`parse_race_key()` で各要素に分解でき、
  `betting.py` のIPATレース選択でも利用する
- `.env` の `SCRAPER_REQUEST_INTERVAL_SECONDS` でリクエスト間隔を制御する
  （サイトへの負荷軽減のため）

### `src/predictor/features.py` / `model.py` / `train.py`

- 特徴量は `horse_number` / `weight` / `odds` / `implied_prob`(`=1/odds`) /
  `odds_rank`（レース内オッズ順位） / `field_size`（出走数）の6列で、
  `build_features()` が学習・推論の両方から共通利用される
- モデルは LightGBM（`LGBMClassifier`, `objective="binary"`）で、
  `data/model.pkl` に `{"model", "feature_columns", "version"}` の辞書として
  `joblib` で保存・読み込みする
- 学習は `python -m src.predictor.train` で実行する。確定済み
  （`finish_position` が設定済み）のレースが20件未満の場合は学習をスキップする

### `src/predictor/betting.py`

- 予測・賭け判断の対象は「発走まで `BET_WINDOW_MINUTES` 分以内（既定60分）」の
  レースに限定する。レース自体は数日前から収集されるが、収集時点の古いオッズに
  基づいて予測・賭けをしないようにするため
- `decide_bets(race, predictions, config)`: 予測スコアが最も高い馬について、
  「スコアが閾値以上」かつ「期待値（スコア×単勝オッズ）が下限以上」の場合に
  単勝で指定金額を賭ける `Bet` を返す（オッズ未取得の馬には賭けない）。
  設定値は `dynamic_config.load_betting_config()` で取得したものを渡す
- `place_bet_production(bet)`: IPAT(JRA即時購入)へのPlaywright(Chromium)
  自動操作。安全装置や注意点は [README](../README.md) を参照

### AI予想と賭け対象決定

- `predict` ジョブはAI予想のみを行う。対象は未確定レースすべてで、オッズは不要。最新モデルと同じ `model_version` の予測が既にあるレースはスキップする。
- AI予想モデルの特徴量は `horse_number` / `weight` / `field_size` / `jockey`。オッズはモデルに入れない。
- `bet_decide` ジョブは賭け対象決定のみを行う。対象は「未確定」「発走まで `BET_DECISION_WINDOW_MINUTES` 分以内」「最新予測あり」「全出走馬のオッズ入力済み」のレース。
- `BET_DECISION_WINDOW_MINUTES` が未設定の場合、既存 `.env` 互換のため `BET_WINDOW_MINUTES` を fallback として読む。
- 賭け判断は `score × odds` の期待値を使うため、オッズ未入力のレースでは `bets` を作成しない。

### `src/predictor/settlement.py`

- `settle_pending_races()`: 未確定の `bets`（`status=placed` のみ）のうち、
  対象レースの `entries.finish_position` が確定済みのものについて
  `fetch_race_results()` の `payouts` から該当馬番の払戻金を取得し、
  `bets.payout` / `is_settled` を更新する
- レース結果は確定しているのに賭けた馬の着順が無い場合（出走取消・除外）は、
  IPATの返還にならい賭け金をそのまま `payout` として確定する
