[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

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
src/api/         Web UI 用 API
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

## 注意

- netkeiba の画面構造や API レスポンスが変わると、スクレイピング処理の修正が必要になる場合があります。
- 馬・騎手・調教師の過去戦績の収集件数を増やすと外部サイトへのアクセスも増えます。`SCRAPER_REQUEST_INTERVAL_SECONDS` を適切に設定してください。
- モデルの性能は保存済みデータ量と過去戦績の充実度に大きく依存します。
- `prod` モードと `IPAT_DRY_RUN=false` の組み合わせは実購入につながります。必ず実機で画面遷移と購入フローを確認してから使ってください。

## ライセンス

[MIT License](LICENSE)
