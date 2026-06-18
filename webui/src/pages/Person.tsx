import { useEffect, useState } from "react";

import { getJSON, formatDate, formatFullDateTime } from "../api";
import { ErrorNote, usePolling } from "../components";
import type { JockeyDetail, PersonResult, TrainerDetail } from "../types";

type PersonKind = "jockey" | "trainer";

function formatCourse(track: string | null, distance: number | null): string {
  const course = `${track ?? ""}${distance ? `${distance}m` : ""}`;
  return course || "-";
}

function otherPersonLabel(kind: PersonKind): string {
  return kind === "jockey" ? "調教師" : "騎手";
}

function otherPersonName(kind: PersonKind, result: PersonResult): string | null | undefined {
  return kind === "jockey" ? result.trainer : result.jockey;
}

function otherPersonId(kind: PersonKind, result: PersonResult): string | null | undefined {
  return kind === "jockey" ? result.trainer_id : result.jockey_id;
}

export default function PersonPage({ kind, personId }: { kind: PersonKind; personId: string }) {
  const endpoint = kind === "jockey" ? "jockeys" : "trainers";
  const label = kind === "jockey" ? "騎手" : "調教師";
  const [year, setYear] = useState<number | null>(null);

  // 別の人物を開いたら年度選択をリセットし、最新年度から表示する
  useEffect(() => setYear(null), [kind, personId]);

  const { data, error } = usePolling<JockeyDetail | TrainerDetail>(
    () => getJSON(`/api/${endpoint}/${personId}${year != null ? `?year=${year}` : ""}`),
    60000,
    [kind, personId, year]
  );

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  return (
    <div className="horse-page">
      <div className="page-header">
        <div>
          <h2>{data.name ?? personId}</h2>
          <div className="horse-detail-header">
            <span className="muted">
              {label}ID: {personId}
            </span>
            <span className="muted">取得: {formatFullDateTime(data.results_fetched_at)}</span>
          </div>
        </div>
        {data.years.length > 0 && (
          <label className="person-year-filter">
            <span>年度</span>
            <select
              value={data.selected_year ?? ""}
              onChange={(e) => setYear(Number(e.target.value))}
            >
              {data.years.map((y) => (
                <option key={y} value={y}>
                  {y}年
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
      {data.results.length === 0 ? (
        <p className="muted">
          {data.selected_year != null
            ? `${data.selected_year}年の戦績はありません。`
            : `過去戦績はまだ収集されていません。`}
        </p>
      ) : (
        <table className="table horse-results-table">
          <thead>
            <tr>
              <th>日付</th>
              <th>開催</th>
              <th>レース</th>
              <th>馬</th>
              <th>{otherPersonLabel(kind)}</th>
              <th>距離</th>
              <th>着順</th>
              <th>人気</th>
              <th>オッズ</th>
              <th>斤量</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((result, index) => {
              const otherName = otherPersonName(kind, result);
              const otherId = otherPersonId(kind, result);
              const otherHref =
                kind === "jockey"
                  ? otherId
                    ? `/trainers/${encodeURIComponent(otherId)}`
                    : null
                  : otherId
                    ? `/jockeys/${encodeURIComponent(otherId)}`
                    : null;
              return (
                <tr key={`${result.race_key ?? "race"}-${result.horse_id ?? index}`}>
                  <td>{formatDate(result.race_date)}</td>
                  <td>{result.venue ?? "-"}</td>
                  <td>{result.race_name ?? "-"}</td>
                  <td>
                    {result.horse_id ? (
                      <a
                        className="link-button"
                        href={`/horses/${encodeURIComponent(result.horse_id)}`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        {result.horse_name ?? result.horse_id}
                      </a>
                    ) : (
                      result.horse_name ?? "-"
                    )}
                  </td>
                  <td>
                    {otherHref ? (
                      <a className="link-button" href={otherHref} target="_blank" rel="noreferrer">
                        {otherName ?? otherId}
                      </a>
                    ) : (
                      otherName ?? "-"
                    )}
                  </td>
                  <td>{formatCourse(result.track_type, result.distance)}</td>
                  <td>{result.finish_position ?? "-"}</td>
                  <td>{result.popularity ? `${result.popularity}人気` : "-"}</td>
                  <td>{result.odds ?? "-"}</td>
                  <td>{result.weight ?? "-"}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}
