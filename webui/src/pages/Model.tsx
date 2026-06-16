import { useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { formatDateTime, getJSON } from "../api";
import { ErrorNote, usePolling } from "../components";
import type { ModelCalibration, ModelVersionDetail } from "../types";

// グラフ配色(Bets.tsx と揃える)
const AXIS = "#8b98a9";
const GRID = "#2a3441";
const ACCENT = "#4f9cf9";
const TOOLTIP_STYLE = { background: "#1a212b", border: "1px solid #2a3441" };

const PARAM_LABELS: Record<string, string> = {
  objective: "目的関数",
  learning_rate: "学習率",
  num_leaves: "葉の数",
  max_depth: "最大深さ",
  min_child_samples: "葉の最小データ数",
  reg_alpha: "L1正則化",
  reg_lambda: "L2正則化",
  feature_fraction: "特徴量サンプリング率",
  bagging_fraction: "データサンプリング率",
  valid_fraction: "検証データ割合",
  early_stopping_rounds: "早期終了ラウンド",
  max_boost_rounds: "最大ブースト回数",
  n_estimators: "木の本数(採用)",
  default_boost_rounds: "既定ブースト回数",
  random_state: "乱数シード",
};

const METRIC_LABELS: Record<string, string> = {
  rows: "学習行数",
  auc: "検証AUC",
  logloss: "検証logloss",
  n_estimators: "木の本数",
  valid_races: "検証レース数",
  calibrated: "確率較正",
  feature_count: "特徴量数",
  positives: "1着サンプル数",
  positive_rate: "1着率",
};

function fmtNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return "-";
  return value.toFixed(digits);
}

function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString();
}

function fmtValue(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "boolean") return value ? "有効" : "なし";
  if (typeof value === "number") {
    return Number.isInteger(value) ? value.toLocaleString() : value.toFixed(4);
  }
  return String(value);
}

function fmtPercent(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "-";
  return `${(value * 100).toFixed(digits)}%`;
}

function DetailTable({
  record,
  labels,
}: {
  record: Record<string, unknown>;
  labels: Record<string, string>;
}) {
  const entries = Object.entries(record ?? {});
  if (entries.length === 0) return <p className="muted">データがありません</p>;
  return (
    <table className="table compact-stats-table">
      <tbody>
        {entries.map(([key, value]) => (
          <tr key={key}>
            <td>{labels[key] ?? key}</td>
            <td>{fmtValue(value)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function ModelPage({ version }: { version: string }) {
  const [showAllImportances, setShowAllImportances] = useState(false);
  const { data, error } = usePolling<ModelVersionDetail>(
    () => getJSON(`/api/models/${encodeURIComponent(version)}`),
    60000,
    [version]
  );
  const { data: calibration } = usePolling<ModelCalibration>(
    () => getJSON(`/api/models/${encodeURIComponent(version)}/calibration`),
    60000,
    [version]
  );

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  const avgFieldSize =
    data.row_count != null && data.race_count
      ? data.row_count / data.race_count
      : null;
  const validRatio =
    data.valid_race_count != null && data.race_count
      ? data.valid_race_count / data.race_count
      : null;

  const sortedImportances = [...data.feature_importances].sort(
    (a, b) => b.importance - a.importance
  );
  const visibleImportances = showAllImportances
    ? sortedImportances
    : sortedImportances.slice(0, 15);

  const calibData = (calibration?.bins ?? []).map((b) => ({
    ...b,
    ideal: b.mean_predicted,
  }));
  const hasCalibration = (calibration?.sample_count ?? 0) > 0 && calibData.length > 1;

  return (
    <div className="model-page">
      <div className="page-header">
        <div>
          <h2>予測モデル分析</h2>
          <p className="muted model-version-text">{data.version}</p>
        </div>
      </div>

      <div className="card-grid">
        <div className="card">
          <div className="card-title">検証AUC</div>
          <div className="metric">{fmtNumber(data.auc)}</div>
          <div className="card-rows">
            <div>
              <span>検証logloss</span>
              <span>{fmtNumber(data.logloss)}</span>
            </div>
            <div>
              <span>確率較正</span>
              <span>{data.calibrated ? "有効" : "なし"}</span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-title">学習レース</div>
          <div className="metric">{fmtInt(data.race_count)}</div>
          <div className="card-rows">
            <div>
              <span>学習行数</span>
              <span>{fmtInt(data.row_count)}</span>
            </div>
            <div>
              <span>検証レース</span>
              <span>{fmtInt(data.valid_race_count)}</span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-title">データ規模</div>
          <div className="metric">{avgFieldSize != null ? avgFieldSize.toFixed(1) : "-"}</div>
          <div className="card-rows">
            <div>
              <span>平均出走頭数</span>
              <span>{avgFieldSize != null ? `${avgFieldSize.toFixed(1)} 頭` : "-"}</span>
            </div>
            <div>
              <span>検証レース割合</span>
              <span>{fmtPercent(validRatio)}</span>
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-title">特徴量</div>
          <div className="metric">{fmtInt(data.feature_columns.length)}</div>
          <div className="card-rows">
            <div>
              <span>カテゴリ特徴量</span>
              <span>{fmtInt(data.categorical_features.length)}</span>
            </div>
            <div>
              <span>木の本数</span>
              <span>{fmtInt(data.n_estimators)}</span>
            </div>
          </div>
        </div>
      </div>

      <section className="overview-section">
        <div className="section-header-row">
          <h2>
            特徴量重要度(gain)
            {showAllImportances
              ? `(全${sortedImportances.length})`
              : `(上位${visibleImportances.length})`}
          </h2>
          {sortedImportances.length > 15 && (
            <button
              className="secondary"
              onClick={() => setShowAllImportances((v) => !v)}
            >
              {showAllImportances ? "上位15のみ表示" : "全て表示"}
            </button>
          )}
        </div>
        <p className="muted">
          重要度は gain(利得)です。分岐回数(split)と違い、高カードナリティなID(騎手・調教師等)が
          不当に高く出にくく、寄与の大きさを反映します。「欠損率」は学習データで値が欠けていた割合です。
        </p>
        {visibleImportances.length === 0 ? (
          <p className="muted">特徴量重要度が保存されていません</p>
        ) : (
          <>
            <div className="chart-card">
              <ResponsiveContainer width="100%" height={Math.max(240, visibleImportances.length * 30)}>
                <BarChart
                  layout="vertical"
                  data={visibleImportances}
                  margin={{ top: 8, right: 24, bottom: 8, left: 12 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke={GRID} horizontal={false} />
                  <XAxis type="number" stroke={AXIS} fontSize={12} />
                  <YAxis
                    type="category"
                    dataKey="name"
                    width={150}
                    stroke={AXIS}
                    fontSize={11}
                    interval={0}
                  />
                  <Tooltip
                    formatter={(v: number) => [v.toLocaleString(), "重要度(gain)"]}
                    contentStyle={TOOLTIP_STYLE}
                  />
                  <Bar dataKey="importance" fill={ACCENT} radius={[0, 3, 3, 0]} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <table className="table table-narrow feature-importance-table">
              <thead>
                <tr>
                  <th>特徴量</th>
                  <th>重要度(gain)</th>
                  <th>欠損率</th>
                </tr>
              </thead>
              <tbody>
                {visibleImportances.map((item) => (
                  <tr key={item.name}>
                    <td>{item.name}</td>
                    <td>{item.importance.toLocaleString()}</td>
                    <td>{item.missing_rate == null ? "-" : fmtPercent(item.missing_rate)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section className="overview-section">
        <h2>確率較正(キャリブレーション)</h2>
        <p className="muted">
          本番予測した確定レースで、予測確率を分位で区切り「平均予測確率」と「実際の1着率」を比較します。
          点が対角線(理想)に近いほど、出力した確率が実態に即しています。
          {calibration && calibration.sample_count > 0 && (
            <>
              {" "}
              対象 {calibration.sample_count.toLocaleString()} 件 / 全体1着率{" "}
              {fmtPercent(calibration.base_rate)}。
            </>
          )}
        </p>
        {hasCalibration ? (
          <div className="chart-card">
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={calibData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke={GRID} />
                <XAxis
                  type="number"
                  dataKey="mean_predicted"
                  stroke={AXIS}
                  fontSize={12}
                  domain={[0, "auto"]}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                  name="平均予測確率"
                />
                <YAxis
                  stroke={AXIS}
                  fontSize={12}
                  domain={[0, "auto"]}
                  tickFormatter={(v: number) => `${(v * 100).toFixed(0)}%`}
                />
                <Tooltip
                  formatter={(v: number, name: string) => [fmtPercent(v, 1), name]}
                  labelFormatter={(v: number) => `予測 ${(v * 100).toFixed(1)}%`}
                  contentStyle={TOOLTIP_STYLE}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="ideal"
                  name="理想(対角線)"
                  stroke={AXIS}
                  strokeDasharray="5 5"
                  dot={false}
                />
                <Line
                  type="monotone"
                  dataKey="actual_rate"
                  name="実際の1着率"
                  stroke={ACCENT}
                  dot={{ r: 3 }}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        ) : (
          <p className="muted">
            このバージョンで確定済みレースの予測がまだ十分に蓄積されていません。
          </p>
        )}
      </section>

      <section className="overview-section">
        <h2>使用特徴量</h2>
        <div className="feature-list">
          {data.feature_columns.map((feature) => (
            <span key={feature} className="feature-chip">
              {feature}
            </span>
          ))}
        </div>
        {data.categorical_features.length > 0 && (
          <>
            <h2>カテゴリ特徴量</h2>
            <div className="feature-list">
              {data.categorical_features.map((feature) => (
                <span key={feature} className="feature-chip feature-chip-accent">
                  {feature}
                </span>
              ))}
            </div>
          </>
        )}
      </section>

      <section className="overview-section">
        <h2>詳細</h2>
        <div className="card-grid">
          <div className="card">
            <div className="card-title">学習パラメータ</div>
            <DetailTable record={data.training_params} labels={PARAM_LABELS} />
          </div>
          <div className="card">
            <div className="card-title">学習指標</div>
            <DetailTable record={data.metrics} labels={METRIC_LABELS} />
          </div>
        </div>
        <p className="muted model-version-text">学習日時: {formatDateTime(data.trained_at)}</p>
      </section>
    </div>
  );
}
