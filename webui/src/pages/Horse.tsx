import { getJSON, formatDate, formatFullDateTime } from "../api";
import { ErrorNote, usePolling } from "../components";
import type { HorseDetail, PedigreeAncestor } from "../types";

// 5代血統表。各先祖は世代gごとに 2^(maxGen-g) 行を rowspan で占める(netkeiba風)。
function PedigreeTable({ pedigree }: { pedigree: PedigreeAncestor[] }) {
  if (pedigree.length === 0) {
    return (
      <p className="muted">
        血統はまだ収集されていません。ジョブの「馬過去成績収集」を実行すると表示されます。
      </p>
    );
  }
  const maxGen = pedigree.reduce((m, a) => Math.max(m, a.generation), 0);
  const totalRows = 2 ** maxGen;
  const byPos = new Map<string, PedigreeAncestor>();
  for (const a of pedigree) byPos.set(`${a.generation}:${a.position}`, a);

  const rows = [];
  for (let r = 0; r < totalRows; r++) {
    const cells = [];
    for (let g = 1; g <= maxGen; g++) {
      const span = 2 ** (maxGen - g);
      if (r % span !== 0) continue; // このセルは上のrowspanに含まれる
      const pos = r / span;
      const anc = byPos.get(`${g}:${pos}`);
      cells.push(
        <td key={g} rowSpan={span} className={`ped-cell ped-gen-${g}`}>
          {anc?.ancestor_horse_id ? (
            <a
              className="link-button"
              href={`/horses/${encodeURIComponent(anc.ancestor_horse_id)}`}
              target="_blank"
              rel="noreferrer"
            >
              {anc.ancestor_name ?? anc.ancestor_horse_id}
            </a>
          ) : (
            anc?.ancestor_name ?? "-"
          )}
        </td>
      );
    }
    rows.push(<tr key={r}>{cells}</tr>);
  }

  return (
    <table className="table pedigree-table">
      <tbody>{rows}</tbody>
    </table>
  );
}

function formatCourse(track: string | null, distance: number | null): string {
  const course = `${track ?? ""}${distance ? `${distance}m` : ""}`;
  return course || "-";
}

function formatTimeSeconds(value: number | null): string {
  if (value === null) return "-";
  const minutes = Math.floor(value / 60);
  const seconds = value - minutes * 60;
  return minutes > 0 ? `${minutes}:${seconds.toFixed(1).padStart(4, "0")}` : seconds.toFixed(1);
}

export default function HorsePage({ horseId }: { horseId: string }) {
  const { data, error } = usePolling<HorseDetail>(
    () => getJSON(`/api/horses/${horseId}`),
    60000,
    [horseId]
  );

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  return (
    <div className="horse-page">
      <div className="page-header">
        <div>
          <h2>{data.name ?? horseId}</h2>
          <div className="horse-detail-header">
            <span className="muted">馬ID: {data.horse_id}</span>
            <span>父: {data.sire_name ?? "-"}</span>
            <span className="muted">取得: {formatFullDateTime(data.results_fetched_at)}</span>
          </div>
        </div>
      </div>
      <details className="pedigree-section" open>
        <summary>5代血統表</summary>
        <PedigreeTable pedigree={data.pedigree} />
      </details>
      {data.results.length === 0 ? (
        <p className="muted">
          過去戦績はまだ収集されていません。ジョブの「馬過去成績収集」を実行すると表示されます。
        </p>
      ) : (
        <table className="table horse-results-table">
          <thead>
            <tr>
              <th>日付</th>
              <th>開催</th>
              <th>レース</th>
              <th>距離</th>
              <th>着順</th>
              <th>人気</th>
              <th>オッズ</th>
              <th>騎手</th>
              <th>斤量</th>
              <th>タイム</th>
              <th>上り</th>
              <th>馬体重</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((result, index) => (
              <tr key={`${result.race_key ?? "race"}-${index}`}>
                <td>{formatDate(result.race_date)}</td>
                <td>{result.venue ?? "-"}</td>
                <td>{result.race_name ?? "-"}</td>
                <td>{formatCourse(result.track_type, result.distance)}</td>
                <td>{result.finish_position ?? "-"}</td>
                <td>{result.popularity ? `${result.popularity}人気` : "-"}</td>
                <td>{result.odds ?? "-"}</td>
                <td>
                  {result.jockey_id ? (
                    <a
                      className="link-button"
                      href={`/jockeys/${encodeURIComponent(result.jockey_id)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {result.jockey ?? result.jockey_id}
                    </a>
                  ) : (
                    result.jockey ?? "-"
                  )}
                </td>
                <td>{result.weight ?? "-"}</td>
                <td>{formatTimeSeconds(result.time_seconds)}</td>
                <td>{result.last_3f ?? "-"}</td>
                <td>{result.horse_weight ?? "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
