import { useState } from "react";
import { getJSON } from "../../api";
import { ErrorNote, ModeBadge, StatusBadge, usePolling } from "../../components";
import type { RaceDetail } from "../../types";
import {
  candidateLabel,
  formatConditions,
  formatExpectedValue,
  formatHorseWeight,
  formatPercent,
  formatPopularity,
  formatRank,
  formatSexAge,
  probabilityLabel,
  sortEntries,
  type SortKey,
  type SortState,
} from "./helpers";

function SortHeader({
  label,
  sortKey,
  defaultDir,
  sort,
  onSort,
}: {
  label: string;
  sortKey: SortKey;
  defaultDir: "asc" | "desc";
  sort: SortState;
  onSort: (key: SortKey, defaultDir: "asc" | "desc") => void;
}) {
  const active = sort.key === sortKey;
  return (
    <th className={`sortable${active ? " sorted" : ""}`} onClick={() => onSort(sortKey, defaultDir)}>
      {label}
      <span className="sort-arrow">{active ? (sort.dir === "asc" ? "▲" : "▼") : "↕"}</span>
    </th>
  );
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

export function RaceDetailView({ raceId }: { raceId: number }) {
  const { data, error } = usePolling<RaceDetail>(
    () => getJSON(`/api/races/${raceId}`),
    30000,
    [raceId]
  );
  const [sort, setSort] = useState<SortState>({ key: "ai_rank", dir: "asc" });
  const handleSort = (key: SortKey, defaultDir: "asc" | "desc") => {
    setSort((current) =>
      current.key === key
        ? { key, dir: current.dir === "asc" ? "desc" : "asc" }
        : { key, dir: defaultDir }
    );
  };

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  const sortedEntries = sortEntries(data.entries, sort);
  const collectionFlags: { label: string; done: boolean }[] = [
    { label: "馬成績", done: data.collection_status.horse_results },
  ];

  return (
    <div className="race-detail">
      {formatConditions(data) && <p className="race-conditions">{formatConditions(data)}</p>}
      <div className="collection-status">
        <span className="muted">過去成績の収集状況:</span>
        {collectionFlags.map((flag) => (
          <span
            key={flag.label}
            className={`collection-badge ${flag.done ? "collected" : "pending"}`}
          >
            {flag.label}: {flag.done ? "収集済" : "未収集"}
          </span>
        ))}
      </div>
      {data.model_version && (
        <p className="muted">
          予測モデル:{" "}
          <a
            className="link-button"
            href={`/models/${encodeURIComponent(data.model_version)}`}
            target="_blank"
            rel="noopener noreferrer"
          >
            {data.model_version}
          </a>
        </p>
      )}
      {data.analysis.top_ai.length > 0 && (
        <div className="race-ai-summary">
          <div className="race-ai-status">
            <span>スコア差: {formatExpectedValue(data.analysis.score_gap)}</span>
            {data.analysis.race_shape && <strong>{data.analysis.race_shape}</strong>}
          </div>
          <div className="race-ai-picks">
            {data.analysis.top_ai.map((pick) => (
              <div key={pick.entry_id} className="race-ai-pick">
                <span className="race-ai-rank">{formatRank(pick.ai_rank)}</span>
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
      <div className="bet-candidates">
        <summary>買い目候補</summary>
        <div className="bet-candidates-header">
          <div>
            <p className="muted">単勝・複勝・馬連・ワイドを期待値順に比較</p>
          </div>
        </div>
        <div className="odds-status-row">
          {data.analysis.odds_status.map((item) => (
            <span key={item.bet_type} className="odds-status-pill">
              {item.bet_type}: {item.available}/{item.total}
            </span>
          ))}
        </div>
        {data.bet_candidates.length === 0 ? (
          <p className="muted">候補なし</p>
        ) : (
          <>
            <table className="table compact-table">
              <thead>
                <tr>
                  <th>券種</th>
                  <th>買い目</th>
                  <th>確率</th>
                  <th>オッズ</th>
                  <th>期待値</th>
                </tr>
              </thead>
              <tbody>
                {data.bet_candidates.slice(0, 8).map((candidate) => (
                  <tr key={`${candidate.bet_type}-${candidate.combination}`}>
                    <td>{candidate.bet_type}</td>
                    <td>{candidateLabel(candidate)}</td>
                    <td>
                      {formatPercent(candidate.probability)}
                      <span className="probability-kind"> {probabilityLabel(candidate.bet_type)}</span>
                    </td>
                    <td>{candidate.odds.toFixed(1)}</td>
                    <td>{formatExpectedValue(candidate.expected_value)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </>
        )}
      </div>
      <table className="table">
          <thead>
            <tr>
              <SortHeader label="AI順位" sortKey="ai_rank" defaultDir="asc" sort={sort} onSort={handleSort} />
              <SortHeader label="馬番" sortKey="horse_number" defaultDir="asc" sort={sort} onSort={handleSort} />
              <SortHeader label="馬名" sortKey="horse_name" defaultDir="asc" sort={sort} onSort={handleSort} />
              <SortHeader label="性齢" sortKey="sex_age" defaultDir="asc" sort={sort} onSort={handleSort} />
              <SortHeader label="騎手" sortKey="jockey" defaultDir="asc" sort={sort} onSort={handleSort} />
              <SortHeader label="厩舎" sortKey="trainer" defaultDir="asc" sort={sort} onSort={handleSort} />
              <SortHeader label="斤量" sortKey="weight" defaultDir="desc" sort={sort} onSort={handleSort} />
              <SortHeader label="馬体重" sortKey="horse_weight" defaultDir="desc" sort={sort} onSort={handleSort} />
              <SortHeader
                label="事前オッズ"
                sortKey="pre_race_odds"
                defaultDir="asc"
                sort={sort}
                onSort={handleSort}
              />
              <SortHeader
                label="確定オッズ"
                sortKey="final_odds"
                defaultDir="asc"
                sort={sort}
                onSort={handleSort}
              />
              <SortHeader label="人気" sortKey="popularity" defaultDir="asc" sort={sort} onSort={handleSort} />
              <SortHeader label="AI勝率" sortKey="score" defaultDir="desc" sort={sort} onSort={handleSort} />
              <SortHeader label="期待値" sortKey="expected_value" defaultDir="desc" sort={sort} onSort={handleSort} />
              <th>判定</th>
              <th>評価</th>
              <SortHeader label="着順" sortKey="finish_position" defaultDir="asc" sort={sort} onSort={handleSort} />
              <th>買い</th>
            </tr>
          </thead>
          <tbody>
            {sortedEntries.map((e) => (
              <tr key={e.id} className={e.has_bet ? "row-bet" : ""}>
                <td>{formatRank(e.ai_rank)}</td>
                <td>{e.horse_number}</td>
                <td>
                  {e.horse_id ? (
                    <a
                      className="link-button"
                      href={`/horses/${encodeURIComponent(e.horse_id)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {e.horse_name}
                    </a>
                  ) : (
                    e.horse_name
                  )}
                </td>
                <td>{formatSexAge(e.sex, e.age)}</td>
                <td>
                  {e.jockey_id ? (
                    <a
                      className="link-button"
                      href={`/jockeys/${encodeURIComponent(e.jockey_id)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {e.jockey || e.jockey_id}
                    </a>
                  ) : (
                    e.jockey || "-"
                  )}
                </td>
                <td>
                  {e.trainer_id ? (
                    <a
                      className="link-button"
                      href={`/trainers/${encodeURIComponent(e.trainer_id)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      {e.trainer || e.trainer_id}
                    </a>
                  ) : (
                    e.trainer || "-"
                  )}
                </td>
                <td>{e.weight ?? "-"}</td>
                <td>{formatHorseWeight(e.horse_weight, e.horse_weight_diff)}</td>
                <td>{e.pre_race_odds ?? "-"}</td>
                <td>{e.final_odds ?? "-"}</td>
                <td>{formatPopularity(e.popularity)}</td>
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
                {b.bet_type} {b.combination ? b.combination : `${b.horse_number}番`} /{" "}
                {b.amount.toLocaleString()}円
                {b.model_version && (
                  <>
                    {" "}
                    / モデル{" "}
                    <a
                      className="link-button"
                      href={`/models/${encodeURIComponent(b.model_version)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                    >
                      {b.model_version}
                    </a>
                  </>
                )}
                {b.is_settled && ` → 払戻 ${Math.round(b.payout ?? 0).toLocaleString()}円`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
