import { getJSON, formatDateTime, formatYen } from "../api";
import { ErrorNote, ModeBadge, usePolling } from "../components";
import type { AuthStatus, BetStats, Overview } from "../types";

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
      {stats.dry_run_count > 0 && (
        <div className="info-note">
          dry-runのため実購入していない賭けが {stats.dry_run_count} 件あります。
        </div>
      )}
      {stats.failed_count > 0 && (
        <div className="warn-note">購入に失敗した賭けが {stats.failed_count} 件あります。</div>
      )}
      {Object.keys(stats.by_type).length > 0 && (
        <div className="mini-stat-table">
          {Object.entries(stats.by_type).map(([betType, item]) => (
            <div key={betType}>
              <span>{betType}</span>
              <span>
                {item.recovery_rate === null ? "-" : `${item.recovery_rate.toFixed(1)}%`}
                <small> {item.settled_count}件</small>
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DataCard({
  title,
  metric,
  rows,
}: {
  title: string;
  metric: string;
  rows: { label: string; value: string }[];
}) {
  return (
    <div className="card data-summary-card">
      <div className="card-title">{title}</div>
      <div className="metric">{metric}</div>
      <div className="card-rows">
        {rows.map((row) => (
          <div key={row.label}>
            <span>{row.label}</span>
            <span>{row.value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function OverviewPage({ auth }: { auth: AuthStatus | null }) {
  const { data, error } = usePolling<Overview>(
    () => getJSON("/api/overview"),
    15000,
    [auth?.authenticated]
  );
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
      <section className="overview-section">
        <h2>予測モデル / レース</h2>
        <div className="card-grid">
          <div className="card model-card">
            <a
              className="link-button model-list-link"
              href="/models"
              target="_blank"
              rel="noopener noreferrer"
            >
             過去モデルの推移 → 
            </a>
            <div className="card-title">予測モデル</div>
            <div className="metric">{data.model.trained ? "学習済み" : "未学習"}</div>
            <div className="card-rows">
              <div>
                <span>バージョン</span>
                <span>
                  {data.model.version ? (
                    <a
                      className="link-button"
                      href={`/models/${encodeURIComponent(data.model.version)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {data.model.version}
                    </a>
                  ) : (
                    "-"
                  )}
                </span>
              </div>
              <div>
                <span>学習日時</span>
                <span>{formatDateTime(data.model.trained_at)}</span>
              </div>
              <div>
                <span>発走前予測済み</span>
                <span>
                  {data.data.predicted_upcoming_race_count.toLocaleString()} /{" "}
                  {data.data.upcoming_race_count.toLocaleString()} 件
                </span>
              </div>
            </div>
          </div>
          <DataCard
            title="レース"
            metric={`${data.data.race_count.toLocaleString()} レース`}
            rows={[
              { label: "結果確定済み", value: `${data.data.finished_race_count.toLocaleString()} レース` },
              { label: "発走前", value: `${data.data.upcoming_race_count.toLocaleString()} 件` },
              { label: "最終収集", value: formatDateTime(data.data.last_collected_at) },
            ]}
          />
        </div>
      </section>

      <section className="overview-section">
        <h2>戦績データ</h2>
        <div className="card-grid">
        <DataCard
          title="馬の戦績"
          metric={`${data.data.horse_result_horse_count.toLocaleString()} 頭`}
          rows={[
            { label: "収集対象", value: `${data.data.horse_target_count.toLocaleString()} 頭` },
            { label: "収集済み", value: `${data.data.horse_result_horse_count.toLocaleString()} 頭` },
            { label: "未収集", value: `${data.data.horse_uncollected_count.toLocaleString()} 頭` },
          ]}
        />
        </div>
        <p className="muted">
          騎手・調教師の戦績は収集済みの出走データ(レース×出走表)からそのまま集計するため、
          個別の収集は不要です。
        </p>
      </section>

      <section className="overview-section">
        <h2>回収率</h2>
        <div className="card-grid">
          {data.modes.sim && <RecoveryCard mode="sim" stats={data.modes.sim} />}
          {auth?.authenticated && data.modes.prod && <RecoveryCard mode="prod" stats={data.modes.prod} />}
        </div>
      </section>

    </div>
  );
}
