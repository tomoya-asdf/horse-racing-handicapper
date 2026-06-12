import { Fragment, useState } from "react";
import { getJSON, formatDateTime } from "../api";
import { ErrorNote, ModeBadge, StatusBadge, usePolling } from "../components";
import type { RaceDetail, RaceSummary } from "../types";

const PAGE_SIZE = 50;

function formatPercent(value: number | null): string {
  if (value === null) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function formatExpectedValue(value: number | null): string {
  if (value === null) return "-";
  return value.toFixed(2);
}

function ScoreBar({ score }: { score: number | null }) {
  if (score === null) return <span className="muted">-</span>;
  return (
    <div className="score-bar">
      <div className="score-bar-fill" style={{ width: `${Math.min(score * 100, 100)}%` }} />
      <span className="score-bar-text">{formatPercent(score)}</span>
    </div>
  );
}

function ValueBadge({ label }: { label: string | null }) {
  if (!label) return <span className="muted">-</span>;
  const className = label === "妙味あり" ? "value-badge value-good" : "value-badge value-muted";
  return <span className={className}>{label}</span>;
}

function RaceDetailView({ raceId }: { raceId: number }) {
  const { data, error } = usePolling<RaceDetail>(
    () => getJSON(`/api/races/${raceId}`),
    30000,
    [raceId]
  );

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  return (
    <div className="race-detail">
      {data.model_version && <p className="muted">予測モデル: {data.model_version}</p>}
      {data.analysis.top_ai.length > 0 && (
        <div className="race-ai-summary">
          <div className="race-ai-status">
            <span>スコア差: {formatExpectedValue(data.analysis.score_gap)}</span>
            {data.analysis.race_shape && <strong>{data.analysis.race_shape}</strong>}
          </div>
          <div className="race-ai-picks">
            {data.analysis.top_ai.map((pick) => (
              <div key={pick.entry_id} className="race-ai-pick">
                <span className="race-ai-rank">{pick.ai_rank}位</span>
                <span>
                  {pick.horse_number}番 {pick.horse_name}
                </span>
                <span>{formatPercent(pick.score)}</span>
                <span>期待値 {formatExpectedValue(pick.expected_value)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
      <table className="table">
        <thead>
          <tr>
            <th>AI順位</th>
            <th>馬番</th>
            <th>馬名</th>
            <th>騎手</th>
            <th>斤量</th>
            <th>オッズ</th>
            <th>人気</th>
            <th>AI勝率</th>
            <th>期待値</th>
            <th>判定</th>
            <th>評価</th>
            <th>着順</th>
            <th>賭け</th>
          </tr>
        </thead>
        <tbody>
          {data.entries.map((e) => (
            <tr key={e.id} className={e.has_bet ? "row-bet" : ""}>
              <td>{e.ai_rank ? `${e.ai_rank}位` : "-"}</td>
              <td>{e.horse_number}</td>
              <td>{e.horse_name}</td>
              <td>{e.jockey || "-"}</td>
              <td>{e.weight ?? "-"}</td>
              <td>{e.odds ?? "-"}</td>
              <td>{e.odds_rank ? `${e.odds_rank}人気` : "-"}</td>
              <td>
                <ScoreBar score={e.score} />
              </td>
              <td>{formatExpectedValue(e.expected_value)}</td>
              <td>
                <ValueBadge label={e.value_label} />
              </td>
              <td>{e.ai_vs_odds ?? "-"}</td>
              <td>{e.finish_position ?? "-"}</td>
              <td>{e.has_bet ? "有" : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {data.bets.length > 0 && (
        <div className="race-bets">
          {data.bets.map((b) => (
            <div key={b.id} className="race-bet-row">
              <ModeBadge mode={b.mode} />
              <StatusBadge status={b.status} />
              <span>
                {b.bet_type} {b.horse_number}番 / {b.amount.toLocaleString()}円
                {b.is_settled && ` → 払戻 ${Math.round(b.payout ?? 0).toLocaleString()}円`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function RacesPage() {
  const [page, setPage] = useState(0);
  const offset = page * PAGE_SIZE;
  const { data, error } = usePolling<{
    races: RaceSummary[];
    total: number;
    limit: number;
    offset: number;
  }>(() => getJSON(`/api/races?limit=${PAGE_SIZE}&offset=${offset}`), 30000, [offset]);
  const [openId, setOpenId] = useState<number | null>(null);
  const total = data?.total ?? 0;
  const rowCount = data?.races.length ?? 0;
  const start = !data || total === 0 ? 0 : data.offset + 1;
  const end = data ? Math.min(data.offset + rowCount, total) : 0;
  const canGoPrev = page > 0;
  const canGoNext = data ? data.offset + rowCount < total : false;
  const changePage = (nextPage: number) => {
    setOpenId(null);
    setPage(nextPage);
  };

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  return (
    <div>
      <div className="pagination-bar">
        <span className="muted">
          {total.toLocaleString()}件中 {start.toLocaleString()}-{end.toLocaleString()}件を表示
        </span>
        <div className="pagination-actions">
          <button disabled={!canGoPrev} onClick={() => changePage(page - 1)}>
            前のページ
          </button>
          <span>{page + 1}ページ目</span>
          <button disabled={!canGoNext} onClick={() => changePage(page + 1)}>
            次のページ
          </button>
        </div>
      </div>
      <h2>レース一覧(50件ずつ表示)</h2>
      <table className="table">
        <thead>
          <tr>
            <th>日付</th>
            <th>競馬場</th>
            <th>R</th>
            <th>レース名</th>
            <th>発走</th>
            <th>頭数</th>
            <th>状態</th>
            <th>予測1位</th>
            <th>賭け</th>
          </tr>
        </thead>
        <tbody>
          {data.races.map((race) => (
            <Fragment key={race.id}>
              <tr
                className="row-clickable"
                onClick={() => setOpenId(openId === race.id ? null : race.id)}
              >
                <td>{race.race_date?.slice(5) ?? "-"}</td>
                <td>{race.venue}</td>
                <td>{race.race_number}</td>
                <td>{race.race_name || "-"}</td>
                <td>{formatDateTime(race.start_time)}</td>
                <td>{race.entry_count}</td>
                <td>{race.finished ? "確定" : "未確定"}</td>
                <td>
                  {race.top_prediction
                    ? `${race.top_prediction.horse_number}番 ${race.top_prediction.horse_name ?? ""} (${formatPercent(race.top_prediction.score)})`
                    : "-"}
                </td>
                <td>{race.bet_count > 0 ? `${race.bet_count}件` : ""}</td>
              </tr>
              {openId === race.id && (
                <tr>
                  <td colSpan={9}>
                    <RaceDetailView raceId={race.id} />
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
}
