import { useEffect, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { getJSON, formatDateTime, formatYen } from "../api";
import { ErrorNote, StatusBadge, usePolling } from "../components";
import type { AuthStatus, BetsResponse } from "../types";

export default function BetsPage({ auth }: { auth: AuthStatus | null }) {
  const [mode, setMode] = useState<"sim" | "prod">("sim");
  const canViewProd = Boolean(auth?.authenticated);

  useEffect(() => {
    if (!canViewProd && mode === "prod") {
      setMode("sim");
    }
  }, [canViewProd, mode]);

  const { data, error } = usePolling<BetsResponse>(
    () => getJSON(`/api/bets?mode=${mode}`),
    15000,
    [mode]
  );

  return (
    <div>
      <div className="page-header">
        <h2>賭け履歴</h2>
        <div className="mode-switch">
          <button className={mode === "sim" ? "active" : ""} onClick={() => setMode("sim")}>
            シミュレーション
          </button>
          <button
            className={mode === "prod" ? "active" : ""}
            onClick={() => setMode("prod")}
            style={{ display: canViewProd ? undefined : "none" }}
          >
            本番
          </button>
        </div>
      </div>
      <ErrorNote message={error} />
      {!data ? (
        <div className="loading">読み込み中...</div>
      ) : (
        <>
          <div className="card-grid">
            <div className="card">
              <div className="card-title">総投資額(決済済み)</div>
              <div className="metric">{formatYen(data.stats.invested)}</div>
            </div>
            <div className="card">
              <div className="card-title">総回収額</div>
              <div className="metric">{formatYen(data.stats.payout)}</div>
            </div>
            <div className="card">
              <div className="card-title">回収率</div>
              <div className="metric">
                {data.stats.recovery_rate === null
                  ? "-"
                  : `${data.stats.recovery_rate.toFixed(1)} %`}
              </div>
            </div>
            <div className="card">
              <div className="card-title">件数</div>
              <div className="metric">{data.bets.length} 件</div>
              <div className="card-rows">
                <div>
                  <span>未決済</span>
                  <span>{data.stats.unsettled_count} 件</span>
                </div>
                {data.stats.failed_count > 0 && (
                  <div>
                    <span>購入失敗</span>
                    <span>{data.stats.failed_count} 件</span>
                  </div>
                )}
                {data.stats.dry_run_count > 0 && (
                  <div>
                    <span>dry-run</span>
                    <span>{data.stats.dry_run_count} 件</span>
                  </div>
                )}
              </div>
            </div>
          </div>

          {Object.keys(data.stats.by_type).length > 0 && (
            <>
              <h2>券種別回収率</h2>
              <table className="table compact-stats-table">
                <thead>
                  <tr>
                    <th>券種</th>
                    <th>投資額</th>
                    <th>回収額</th>
                    <th>回収率</th>
                    <th>決済済み</th>
                    <th>未決済</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.stats.by_type).map(([betType, stats]) => (
                    <tr key={betType}>
                      <td>{betType}</td>
                      <td>{formatYen(stats.invested)}</td>
                      <td>{formatYen(stats.payout)}</td>
                      <td>
                        {stats.recovery_rate === null
                          ? "-"
                          : `${stats.recovery_rate.toFixed(1)} %`}
                      </td>
                      <td>{stats.settled_count} 件</td>
                      <td>{stats.unsettled_count} 件</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}

          {data.cumulative.length > 1 && (
            <>
              <h2>累積 投資額 / 回収額</h2>
              <div className="chart-card">
                <ResponsiveContainer width="100%" height={260}>
                  <LineChart data={data.cumulative}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#2a3441" />
                    <XAxis
                      dataKey="placed_at"
                      tickFormatter={(v: string) => formatDateTime(v)}
                      stroke="#8b98a9"
                      fontSize={12}
                    />
                    <YAxis stroke="#8b98a9" fontSize={12} />
                    <Tooltip
                      labelFormatter={(v) => formatDateTime(String(v))}
                      formatter={(value: number, name: string) => [formatYen(value), name]}
                      contentStyle={{ background: "#1a212b", border: "1px solid #2a3441" }}
                    />
                    <Legend />
                    <Line
                      type="monotone"
                      dataKey="invested"
                      name="投資額"
                      stroke="#8b98a9"
                      dot={false}
                    />
                    <Line
                      type="monotone"
                      dataKey="payout"
                      name="回収額"
                      stroke="#4f9cf9"
                      dot={false}
                    />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </>
          )}

          <h2>履歴一覧</h2>
          <table className="table">
            <thead>
              <tr>
                <th>賭けた日時</th>
                <th>レース</th>
                <th>馬</th>
                <th>式別</th>
                <th>状態</th>
                <th>金額</th>
                <th>オッズ</th>
                <th>払戻</th>
              </tr>
            </thead>
            <tbody>
              {data.bets.length === 0 && (
                <tr>
                  <td colSpan={8} className="muted">
                    賭けデータがまだありません
                  </td>
                </tr>
              )}
              {data.bets.map((b) => (
                <tr key={b.id}>
                  <td>{formatDateTime(b.placed_at)}</td>
                  <td>
                    {b.race_date?.slice(5)} {b.venue} {b.race_number}R
                  </td>
                  <td>
                    {b.combination ? b.combination : `${b.horse_number}番 ${b.horse_name ?? ""}`}
                  </td>
                  <td>{b.bet_type}</td>
                  <td>
                    <StatusBadge status={b.status} />
                    {b.status === "placed" && !b.is_settled && (
                      <span className="muted"> (未決済)</span>
                    )}
                  </td>
                  <td>{formatYen(b.amount)}</td>
                  <td>{b.odds_at_bet ?? "-"}</td>
                  <td>{b.is_settled ? formatYen(b.payout) : "-"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
