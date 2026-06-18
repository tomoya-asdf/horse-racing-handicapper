import { formatDateTime } from "../../api";
import type { SystemVersion } from "../../types";

export function AdminSection({
  version,
  onDeploy,
  onRestart,
}: {
  version: SystemVersion | null;
  onDeploy: () => void;
  onRestart: () => void;
}) {
  return (
    <section className="settings-admin-section">
      <h2>管理操作</h2>

      <div className="admin-block">
        <h3>
          ソフトウェア更新
          {version?.update_available && <span className="update-badge">更新あり</span>}
        </h3>
        {!version?.available ? (
          <p className="muted">
            デプロイエージェントが未検出です。ホストで{" "}
            <code>scripts/deploy_agent.sh</code>(Linux)または{" "}
            <code>scripts/deploy_agent.ps1</code>(Windows)を起動すると、
            現在のバージョンと更新有無が表示されます。
          </p>
        ) : (
          <>
            <table className="table deploy-status-table">
              <tbody>
                <tr>
                  <td>稼働中バージョン</td>
                  <td>
                    {version.current_sha ?? "-"}
                    {version.current_ref ? ` (${version.current_ref})` : ""}
                  </td>
                </tr>
                <tr>
                  <td>最新バージョン</td>
                  <td>
                    {version.remote_sha ?? "-"}
                    {version.update_available ? " — 更新あり" : " — 最新です"}
                  </td>
                </tr>
                <tr>
                  <td>更新確認</td>
                  <td>{formatDateTime(version.last_checked_at)}</td>
                </tr>
                <tr>
                  <td>デプロイ状態</td>
                  <td>
                    {version.state ?? "-"}
                    {version.last_deploy_at ? ` / 最終: ${formatDateTime(version.last_deploy_at)}` : ""}
                    {version.last_deploy_result ? ` (${version.last_deploy_result})` : ""}
                  </td>
                </tr>
              </tbody>
            </table>
            {version.message && <pre className="deploy-log">{version.message}</pre>}
            <button
              className="secondary danger-outline"
              disabled={version.state === "requested" || version.state === "running"}
              onClick={onDeploy}
            >
              {version.state === "running" ? "デプロイ中..." : "アップデートを実行"}
            </button>
          </>
        )}
      </div>

      <div className="admin-block">
        <h3>システム再起動</h3>
        <p className="muted">
          システム全体の再起動は通常の設定保存とは別操作です。必要な時だけ実行してください。
        </p>
        <button className="secondary danger-outline" onClick={onRestart}>
          システム全体を再起動
        </button>
      </div>
    </section>
  );
}
