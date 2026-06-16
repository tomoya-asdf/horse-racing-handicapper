import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatDate, formatDateTime, getJSON } from "../api";
import { ErrorNote, usePolling } from "../components";
import type { ModelsListResponse, Overview } from "../types";

// グラフ配色(Model.tsx と揃える)
const AXIS = "#8b98a9";
const GRID = "#2a3441";
const ACCENT = "#4f9cf9";
const ACCENT2 = "#f0a35e";
const TOOLTIP_STYLE = { background: "#1a212b", border: "1px solid #2a3441" };

function fmtNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return "-";
  return value.toFixed(digits);
}

function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString();
}

export default function ModelsPage() {
  const { data, error } = usePolling<ModelsListResponse>(
    () => getJSON("/api/models?limit=100"),
    60000,
    []
  );
  const { data: overview } = usePolling<Overview>(
    () => getJSON("/api/overview"),
    60000,
    []
  );

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  const models = data.models ?? [];
  const activeVersion = overview?.model.version ?? null;

  // 精度の推移: AUCが算出済みのものをバージョン昇順に並べる
  const history = models
    .filter((m) => m.auc != null)
    .map((m) => ({
      version: m.version,
      label: m.trained_at ? formatDate(m.trained_at) : m.version,
      auc: m.auc,
      logloss: m.logloss,
    }))
    .sort((a, b) => a.version.localeCompare(b.version));
  const activeLabel = history.find((h) => h.version === activeVersion)?.label;

  // 一覧: 新しい順
  const rows = [...models].sort((a, b) => b.version.localeCompare(a.version));
  const latest = rows[0] ?? null;

  const aucValues = models
    .map((m) => m.auc)
    .filter((v): v is number => v != null);
  const bestAuc = aucValues.length ? Math.max(...aucValues) : null;
  const bestModel =
    bestAuc != null ? models.find((m) => m.auc === bestAuc) ?? null : null;

  if (models.length === 0) {
    return (
      <div>
        <div className="page-header">
          <h2>学習モデル一覧</h2>
        </div>
        <p className="muted">学習済みモデルがまだありません。</p>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <h2>学習モデル一覧</h2>
      </div>
      <p className="muted">
        これまで学習したモデルの精度を比較・分析できます。バージョンをクリックすると詳細を新しいタブで開きます。
      </p>

      <div className="card-grid">
        <div className="card">
          <div className="card-title">モデル数</div>
          <div className="metric">{fmtInt(models.length)}</div>
        </div>
        <div className="card">
          <div className="card-title">最新モデルAUC</div>
          <div className="metric">{fmtNumber(latest?.auc)}</div>
          <div className="card-rows">
            <div>
              <span>検証logloss</span>
              <span>{fmtNumber(latest?.logloss)}</span>
            </div>
            <div>
              <span>学習日時</span>
              <span>{latest?.trained_at ? formatDate(latest.trained_at) : "-"}</span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-title">ベストAUC</div>
          <div className="metric">{fmtNumber(bestAuc)}</div>
          <div className="card-rows">
            <div>
              <span>該当バージョン</span>
              <span>{bestModel?.version ?? "-"}</span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-title">稼働中バージョン</div>
          <div className="metric model-version-metric">{activeVersion ?? "-"}</div>
        </div>
      </div>

      {history.length >= 2 && (
        <section className="overview-section">
          <h2>バージョン比較(精度の推移)</h2>
          <p className="muted">
            再学習ごとの検証AUC・loglossの推移です。AUCは高いほど、loglossは低いほど良好です。
          </p>
          <div className="chart-card">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={history} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
                <XAxis dataKey="label" stroke={AXIS} fontSize={11} />
                <YAxis
                  yAxisId="auc"
                  stroke={ACCENT}
                  fontSize={12}
                  domain={["auto", "auto"]}
                  width={48}
                />
                <YAxis
                  yAxisId="logloss"
                  orientation="right"
                  stroke={ACCENT2}
                  fontSize={12}
                  domain={["auto", "auto"]}
                  width={48}
                />
                <Tooltip
                  formatter={(v: number, name: string) => [v.toFixed(4), name]}
                  contentStyle={TOOLTIP_STYLE}
                />
                <Legend />
                {activeLabel && (
                  <ReferenceLine
                    x={activeLabel}
                    yAxisId="auc"
                    stroke={AXIS}
                    strokeDasharray="4 4"
                    label={{ value: "稼働中", fill: AXIS, fontSize: 11, position: "top" }}
                  />
                )}
                <Line
                  yAxisId="auc"
                  type="monotone"
                  dataKey="auc"
                  name="AUC"
                  stroke={ACCENT}
                  dot={{ r: 2 }}
                />
                <Line
                  yAxisId="logloss"
                  type="monotone"
                  dataKey="logloss"
                  name="logloss"
                  stroke={ACCENT2}
                  dot={{ r: 2 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      <h2>モデル一覧</h2>
      <table className="table">
        <thead>
          <tr>
            <th>バージョン</th>
            <th>学習日時</th>
            <th>検証AUC</th>
            <th>検証logloss</th>
            <th>学習レース数</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((m) => (
            <tr key={m.version}>
              <td>
                <a
                  className="link-button"
                  href={`/models/${encodeURIComponent(m.version)}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {m.version}
                </a>
                {m.version === activeVersion && (
                  <span className="summary-pill model-active-pill">稼働中</span>
                )}
                {bestModel && m.version === bestModel.version && (
                  <span className="summary-pill model-best-pill">ベスト</span>
                )}
              </td>
              <td>{formatDateTime(m.trained_at)}</td>
              <td>{fmtNumber(m.auc)}</td>
              <td>{fmtNumber(m.logloss)}</td>
              <td>{fmtInt(m.race_count)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
