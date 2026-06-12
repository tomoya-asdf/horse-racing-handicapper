import { getJSON, formatDateTime, formatYen } from "../api";
import { ErrorNote, ModeBadge, StatusBadge, usePolling } from "../components";
import type { BetStats, Overview } from "../types";

function RecoveryCard({ mode, stats }: { mode: string; stats: BetStats }) {
  return (
    <div className="card">
      <div className="card-title">
        回収率 <ModeBadge mode={mode} />
      </div>
      <div className="metric">
        {stats.recovery_rate === null ? "-" : `${stats.recovery_rate.toFixed(1)} %`}
      </div>
      <div className="card-rows">
        <div>
          <span>投資額(決済済み)</span>
          <span>{formatYen(stats.invested)}</span>
        </div>
        <div>
          <span>回収額</span>
          <span>{formatYen(stats.payout)}</span>
        </div>
        <div>
          <span>決済済み / 未決済</span>
          <span>
            {stats.settled_count} / {stats.unsettled_count} 件
          </span>
        </div>
      </div>
      {stats.pending_count > 0 && (
        <div className="warn-note">
          購入結果が未確認の賭けが {stats.pending_count} 件あります。IPATの投票履歴を確認してください。
        </div>
      )}
      {stats.failed_count > 0 && (
        <div className="warn-note">購入に失敗した賭けが {stats.failed_count} 件あります。</div>
      )}
    </div>
  );
}

export default function OverviewPage() {
  const { data, error } = usePolling<Overview>(() => getJSON("/api/overview"), 15000);

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  const mode = data.settings.editable.betting_mode;

  return (
    <div>
      {!data.model.trained && (
        <div className="info-note">
          モデルが未学習です。初回セットアップは「ジョブ」画面から
          <b>「過去データ取得(バックフィル)」で結果確定済みレースを20件以上集めた後、
          「モデル学習」を実行</b>してください。
        </div>
      )}
      {mode === "prod" && (
        <div className={data.settings.readonly.ipat_dry_run ? "warn-note" : "danger-note"}>
          現在 <b>本番モード(prod)</b> で動作しています
          {data.settings.readonly.ipat_dry_run
            ? "(IPAT_DRY_RUN=true のため実際の購入は行われません)"
            : "。実際の購入が行われます!"}
        </div>
      )}
      <div className="card-grid">
        <div className="card">
          <div className="card-title">予測モデル</div>
          <div className="metric">{data.model.trained ? "学習済み" : "未学習"}</div>
          <div className="card-rows">
            <div>
              <span>バージョン</span>
              <span>{data.model.version ?? "-"}</span>
            </div>
            <div>
              <span>学習日時</span>
              <span>{formatDateTime(data.model.trained_at)}</span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-title">収集データ</div>
          <div className="metric">{data.data.race_count} レース</div>
          <div className="card-rows">
            <div>
              <span>結果確定済み</span>
              <span>{data.data.finished_race_count} レース</span>
            </div>
            <div>
              <span>発走前レース</span>
              <span>{data.data.upcoming_race_count} 件</span>
            </div>
            <div>
              <span>最終収集</span>
              <span>{formatDateTime(data.data.last_collected_at)}</span>
            </div>
          </div>
        </div>
        <RecoveryCard mode="sim" stats={data.modes.sim} />
        <RecoveryCard mode="prod" stats={data.modes.prod} />
      </div>

      <h2>ジョブの最終実行</h2>
      <table className="table">
        <thead>
          <tr>
            <th>ジョブ</th>
            <th>状態</th>
            <th>実行種別</th>
            <th>開始</th>
            <th>結果</th>
          </tr>
        </thead>
        <tbody>
          {data.latest_jobs.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                まだ実行履歴がありません
              </td>
            </tr>
          )}
          {data.latest_jobs.map((job) => (
            <tr key={job.id}>
              <td>{job.label}</td>
              <td>
                <StatusBadge status={job.status} />
              </td>
              <td>{job.trigger === "manual" ? "手動" : "スケジュール"}</td>
              <td>{formatDateTime(job.started_at ?? job.created_at)}</td>
              <td className="detail-cell">{job.detail ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
