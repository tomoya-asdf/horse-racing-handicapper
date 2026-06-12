# horse-racing-handicapper

競馬のレースデータを定期的に収集し、予測モデルによる賭け判断を行う個人用システムです。
「本番」と「シミュレーション」の2モードで賭けを記録し、回収率などの実績や
ジョブの実行・設定変更をWeb管理コンソールから行えます。

## アーキテクチャ

`docker compose` で以下の4サービスを起動します。

| サービス | 役割 |
| --- | --- |
| `db` | PostgreSQL。レース・出走馬・予測・賭け履歴・ジョブ履歴・設定を保存 |
| `collector` | レース情報・オッズ・結果を定期取得しDBへ保存 |
| `predictor` | 予測モデルで賭け判断を行い、結果をDBに記録（本番/シミュレーション）。モデル学習・決済も担当 |
| `webui` | 管理コンソール（React + FastAPI）。状況確認・履歴・ジョブ手動実行・設定変更 |

詳細は [docs/architecture.md](docs/architecture.md) を参照してください。

## ディレクトリ構成

    .
    ├── docker/
    │   ├── Dockerfile             # collector 用
    │   ├── Dockerfile.predictor   # predictor用 (Playwright同梱)
    │   ├── Dockerfile.webui       # webui用 (Reactビルド + FastAPI)
    │   ├── requirements.txt
    │   ├── requirements-api.txt
    │   └── requirements-predictor.txt
    ├── src/
    │   ├── common/      # DBモデル・設定・ジョブ管理など共通コード
    │   ├── collector/   # データ収集サービス (netkeiba.com)
    │   ├── predictor/   # 予測・賭け判断サービス + 学習スクリプト
    │   └── api/         # 管理コンソールのバックエンドAPI (FastAPI)
    ├── webui/           # 管理コンソールのフロントエンド (React + TypeScript + Vite)
    ├── docs/
    │   └── architecture.md
    ├── data/            # モデルファイル(model.pkl)などの永続化用ボリューム
    ├── docker-compose.yml
    └── .env.example

## セットアップ

1. `.env.example` を `.env` にコピーし、必要に応じて値を変更する

   ```powershell
   Copy-Item .env.example .env
   ```

2. コンテナをビルド・起動する

   ```powershell
   docker compose up -d --build
   ```

3. 管理コンソールへアクセスする

   http://localhost:8000

   ※ 管理コンソール(8000)とPostgreSQL(5432)はループバックアドレス(127.0.0.1)のみに
   公開しています。他の端末からアクセスする必要がある場合のみ `docker-compose.yml` の
   `ports` を変更してください（認証は無いため、公開範囲の変更は慎重に）。

## 管理コンソール (webui)

http://localhost:8000 で以下の操作ができます。

- **概要**: モデルの学習状態、収集データ件数、モード別の回収率、各ジョブの最終実行
- **レース**: 収集済みレースの一覧と、出走馬・予測スコア・賭けの詳細
- **賭け履歴**: モード別の賭け一覧・回収率・累積投資/回収のグラフ
- **ジョブ**: データ収集・予測・決済・モデル学習の手動実行と実行履歴
  （手動・スケジュールを問わず全実行が記録されます）
- **設定**: 賭けモード・金額・スコア閾値・期待値下限の変更（再起動不要で次回ジョブから反映）

ジョブの手動実行は、APIが `job_runs` テーブルへ実行依頼を登録し、担当サービス
（collector / predictor）が数秒間隔のポーリングで取得して実行する仕組みです。
そのためwebuiが起動していなくても定期実行には影響しません。

## 設定の2層構造

- **.env**: DB接続・ジョブ間隔・IPAT認証情報などの静的設定。変更にはコンテナの再起動が必要
- **管理コンソールの「設定」(DBの `app_settings`)**: 賭けモード・賭け金額・スコア閾値・
  期待値下限。各ジョブが実行のたびに読み直すため再起動不要。未設定の項目は .env の値を使う

## 本番 / シミュレーションモードの切り替え

管理コンソールの「設定」、または `.env` の `BETTING_MODE` で切り替えます
（管理コンソールでの設定が優先されます）。

- `sim`: シミュレーション。賭けは `bets` テーブルに記録されるのみで、実際の購入は行わない
- `prod`: 本番。予測結果に基づき実際の購入操作（`src/predictor/betting.py` の `place_bet_production`）を呼び出す

`bets.mode` カラムで `prod` / `sim` を区別して保存するため、それぞれの回収率を
個別に確認できます。

## 賭け戦略

管理コンソールの「設定」から変更できます（詳細は `src/predictor/betting.py`）。

- スコア閾値（`bet_score_threshold`）: 賭けを行う予測スコア（1着になる確率）の下限
- 期待値下限（`bet_min_expected_value`）: 賭けを行う期待値（予測スコア×単勝オッズ）の下限。
  既定の `1.0` は「モデル上プラス期待値の時のみ賭ける」ことを意味します。
  スコアだけで賭けるとほぼ常に1番人気を買うことになり、長期回収率は控除率相当
  （約80%）に収束しやすいため、この条件を併用しています
- 賭け金額（`bet_amount`）: 1件あたりの賭け金額（100円以上・100円単位）

レースは数日先の分まで収集されますが（`.env` の `COLLECT_DAYS_AHEAD`、既定3日）、
賭け判断は発走まで `BET_WINDOW_MINUTES`（既定60分）以内のレースに限定されます。
それでも予測・賭け判断に使うオッズは collector が最後に取得した時点のもの
（最大 `COLLECT_INTERVAL_MINUTES` 分前）であり、購入時点の実オッズとは
ずれがある点に注意してください。

## 過去データの一括取得（バックフィル）

通常の収集ジョブは「これから発走するレース」を対象とするため、学習データ
（結果が確定したレース）が貯まるまで時間がかかります。**初回セットアップ時は、
管理コンソールの「ジョブ」画面にある「過去データ取得（バックフィル）」で
期間を指定して実行してください**（既定で直近2週間が入力されています）。
過去の開催日のレース・出走馬・最終オッズ・確定結果をさかのぼって取得します。

- 一度に指定できるのは31日分まで（netkeibaへの負荷を抑えるため。それ以上は分割実行）
- 開催の無い日（平日など）は空振りするだけなので、週をまたいで指定して問題ありません
- 1レースあたり約3リクエスト（間隔1秒）のため、1開催日あたり2〜3分かかります

コマンドラインからも実行できます。

```powershell
docker compose run --rm collector python -m src.collector.backfill 20260530 20260607
```

## モデルの学習

`predictor` は `data/model.pkl` が存在しない場合、「モデル未学習」のログを出して
待機します（コンテナはクラッシュしません）。`collector` がレース結果をある程度
蓄積したら（またはバックフィル後に）、管理コンソールの「ジョブ」から
**モデル学習** を実行してください。コマンドラインからも実行できます。

```powershell
docker compose run --rm predictor python -m src.predictor.train
```

学習データ（`entries.finish_position` が確定したレース）が20件未満の場合は
スキップされ、モデルファイルは作成されません。学習が完了すると
`data/model.pkl` に保存され、`predictor` の次回ジョブから自動的にそのモデルで
予測されます。

## データの永続化

収集したデータ・モデル・賭け履歴は、コンテナを停止・削除しても残ります。

- **PostgreSQLのデータ**（レース・賭け履歴・設定など）: 名前付きボリューム `db_data` に保存。
  `docker compose stop` / `docker compose down` / PCの再起動では消えません
- **モデルファイル**（`model.pkl`）: ホストの `./data` フォルダに保存

データが消えるのは `docker compose down -v`（ボリュームも削除）を明示的に実行した場合と、
`./data` を手動で削除した場合だけです。バックアップを取りたい場合は
`docker compose exec db pg_dump -U horse horse_racing > backup.sql` を利用してください。

## 注意事項

### netkeibaへのアクセス

`collector` は netkeiba.com の公開ページ・APIからレース情報・オッズ・結果を
取得します。サイトへの負荷軽減のため `.env` の `SCRAPER_REQUEST_INTERVAL_SECONDS`
（既定1秒）でリクエストごとに間隔を空けています。値を小さくしすぎないでください。

### IPAT自動購入 (`BETTING_MODE=prod`)

`prod` モードでは `src/predictor/betting.py` の `place_bet_production()` が
IPAT(JRA即時購入)へPlaywright(Chromium)でログイン・購入操作を行います。

- `.env` に `IPAT_SUBSCRIBER_NUMBER` / `IPAT_PIN` / `IPAT_PARS_NUMBER` を設定する必要があります
- `IPAT_DRY_RUN=true`（既定）の間は、購入内容の入力・確認画面への遷移までを行い、
  **最終的な購入ボタンは押さずログ出力のみ**を行います
- `betting.py` 内の `SELECTORS` はIPATの実画面で検証済みの値ではありません。
  実際に購入を有効化する前に、ログイン済みのIPAT画面で開発者ツールから
  実際のセレクタを確認し、コードを調整してください
- 上記の確認・調整が済むまでは `IPAT_DRY_RUN=false` にしないでください

賭けは購入操作の **前** に `bets.status=pending` としてDBへ記録され、購入成功で
`placed`、失敗で `failed` に更新されます。購入処理の途中でプロセスが停止すると
`pending` のまま残りますが、その場合も同一レースへの重複購入は行われません。
管理コンソールに `pending` の警告が表示された場合は、IPATの投票履歴と突き合わせて
実際に購入されたかを確認してください。

### DBスキーマの変更について

マイグレーションの仕組み（Alembic等）は導入していません。`init_db()` は
存在しないテーブルを作成するだけで、既存テーブルへの列・制約の追加は行いません。
モデル定義（`src/common/models.py`）を変更した場合は、次のいずれかが必要です。

- データを残す必要がなければDBを作り直す: `docker compose down -v`
- データを残す場合は手動でALTERを実行する。例（status列・ユニーク制約の追加時）:

  ```sql
  ALTER TABLE bets ADD COLUMN status VARCHAR(10) NOT NULL DEFAULT 'placed';
  ALTER TABLE entries ADD CONSTRAINT uq_entries_race_horse UNIQUE (race_id, horse_number);
  ALTER TABLE predictions ADD CONSTRAINT uq_predictions_entry_model UNIQUE (entry_id, model_version);
  ALTER TABLE job_runs ADD COLUMN params VARCHAR;
  ```

  ※ `job_runs` / `app_settings` のような新規テーブルは `init_db()` が自動作成するため対応不要です。

## 開発時のヒント

- サービス単体を手動実行: `docker compose run --rm collector python -m src.collector.main`
- DBの内容を確認: `docker compose exec db psql -U horse -d horse_racing`
- ログ確認: `docker compose logs -f predictor`
- イメージの再ビルドが必要な変更（依存追加など）をした場合: `docker compose up -d --build`
- フロントエンドの開発: `cd webui && npm install && npm run dev`
  （Vite開発サーバーが http://localhost:5173 で起動し、`/api` は localhost:8000 へプロキシされます。
  APIは `docker compose up -d webui` で起動しておくか、ローカルで
  `uvicorn src.api.main:app --reload` を実行してください）
