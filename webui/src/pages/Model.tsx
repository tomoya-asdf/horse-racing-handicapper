import { formatDateTime, getJSON } from "../api";
import { ErrorNote, usePolling } from "../components";
import type { ModelVersionDetail } from "../types";

function fmtNumber(value: number | null | undefined, digits = 4): string {
  if (value === null || value === undefined) return "-";
  return value.toFixed(digits);
}

function fmtInt(value: number | null | undefined): string {
  if (value === null || value === undefined) return "-";
  return value.toLocaleString();
}

function JsonBlock({ value }: { value: unknown }) {
  return <pre className="json-block">{JSON.stringify(value, null, 2)}</pre>;
}

export default function ModelPage({ version }: { version: string }) {
  const { data, error } = usePolling<ModelVersionDetail>(
    () => getJSON(`/api/models/${encodeURIComponent(version)}`),
    60000,
    [version]
  );

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  return (
    <div className="model-page">
      <div className="page-header">
        <div>
          <h2>予測モデル</h2>
          <p className="muted model-version-text">{data.version}</p>
        </div>
      </div>

      <div className="card-grid">
        <div className="card">
          <div className="card-title">検証AUC</div>
          <div className="metric">{fmtNumber(data.auc)}</div>
        </div>
        <div className="card">
          <div className="card-title">検証logloss</div>
          <div className="metric">{fmtNumber(data.logloss)}</div>
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
          <div className="card-title">学習情報</div>
          <div className="card-rows">
            <div>
              <span>学習日時</span>
              <span>{formatDateTime(data.trained_at)}</span>
            </div>
            <div>
              <span>木の本数</span>
              <span>{fmtInt(data.n_estimators)}</span>
            </div>
            <div>
              <span>確率較正</span>
              <span>{data.calibrated ? "有効" : "なし"}</span>
            </div>
          </div>
        </div>
      </div>

      <section className="overview-section">
        <h2>特徴量重要度</h2>
        <table className="table compact-stats-table">
          <thead>
            <tr>
              <th>特徴量</th>
              <th>重要度</th>
            </tr>
          </thead>
          <tbody>
            {data.feature_importances.length === 0 && (
              <tr>
                <td colSpan={2} className="muted">
                  特徴量重要度が保存されていません
                </td>
              </tr>
            )}
            {data.feature_importances.map((item) => (
              <tr key={item.name}>
                <td>{item.name}</td>
                <td>{item.importance.toLocaleString()}</td>
              </tr>
            ))}
          </tbody>
        </table>
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
            <JsonBlock value={data.training_params} />
          </div>
          <div className="card">
            <div className="card-title">指標JSON</div>
            <JsonBlock value={data.metrics} />
          </div>
        </div>
      </section>
    </div>
  );
}
