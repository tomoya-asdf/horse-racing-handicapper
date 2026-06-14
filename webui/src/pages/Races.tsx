import { Fragment, useState } from "react";
import { getJSON, formatDate, formatFullDateTime } from "../api";
import { ErrorNote, ModeBadge, StatusBadge, usePolling } from "../components";
import type { RaceBetCandidate, RaceDetail, RaceEntry, RaceSummary, RacesResponse } from "../types";

const PAGE_SIZE = 50;

type SortKey =
  | "ai_rank"
  | "horse_number"
  | "horse_name"
  | "sex_age"
  | "jockey"
  | "trainer"
  | "weight"
  | "horse_weight"
  | "pre_race_odds"
  | "final_odds"
  | "popularity"
  | "score"
  | "expected_value"
  | "finish_position";

interface SortState {
  key: SortKey;
  dir: "asc" | "desc";
}

function entrySortValue(entry: RaceEntry, key: SortKey): string | number | null {
  if (key === "sex_age") {
    const sex = entry.sex ?? "";
    const age = entry.age ?? "";
    return sex || age !== "" ? `${sex}${age}` : null;
  }
  return entry[key];
}

function sortEntries(entries: RaceEntry[], sort: SortState): RaceEntry[] {
  return [...entries].sort((a, b) => {
    const av = entrySortValue(a, sort.key);
    const bv = entrySortValue(b, sort.key);
    const aEmpty = av === null || av === undefined;
    const bEmpty = bv === null || bv === undefined;
    if (aEmpty && bEmpty) return 0;
    if (aEmpty) return 1;
    if (bEmpty) return -1;

    const cmp =
      typeof av === "string" || typeof bv === "string"
        ? String(av).localeCompare(String(bv), "ja")
        : (av as number) - (bv as number);
    return sort.dir === "asc" ? cmp : -cmp;
  });
}

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

function formatCourse(track: string | null, distance: number | null): string {
  const course = `${track ?? ""}${distance ? `${distance}m` : ""}`;
  return course || "-";
}

function formatConditions(d: {
  track_type: string | null;
  distance: number | null;
  direction: string | null;
  going: string | null;
  weather: string | null;
  race_class: string | null;
}): string {
  const parts: string[] = [];
  const course = `${d.track_type ?? ""}${d.distance ? `${d.distance}m` : ""}`;
  if (course) parts.push(d.direction ? `${course} (${d.direction})` : course);
  if (d.race_class) parts.push(d.race_class);
  if (d.going) parts.push(`馬場:${d.going}`);
  if (d.weather) parts.push(`天候:${d.weather}`);
  return parts.join(" / ");
}

function formatPercent(value: number | null): string {
  if (value === null) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

function formatExpectedValue(value: number | null): string {
  if (value === null) return "-";
  return value.toFixed(2);
}

function formatRank(value: number | null): string {
  return value ? `${value}位` : "-";
}

function formatPopularity(value: number | null): string {
  return value ? `${value}人気` : "-";
}

function raceStatusLabel(finished: boolean): string {
  return finished ? "確定" : "未確定";
}

function formatSexAge(sex: string | null, age: number | null): string {
  const s = sex ?? "";
  const a = age != null ? String(age) : "";
  return s || a ? `${s}${a}` : "-";
}

function formatHorseWeight(weight: number | null, diff: number | null): string {
  if (weight == null) return "-";
  if (diff == null) return String(weight);
  const sign = diff > 0 ? `+${diff}` : String(diff);
  return `${weight}(${sign})`;
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

function candidateLabel(candidate: RaceBetCandidate): string {
  if (candidate.bet_type === "単勝" || candidate.bet_type === "複勝") {
    return `${candidate.horse_number ?? "-"}番 ${candidate.horse_name ?? ""}`;
  }
  return candidate.combination;
}

function probabilityLabel(betType: string): string {
  if (betType === "単勝") return "1着";
  if (betType === "複勝") return "3着内";
  if (betType === "馬連") return "1-2着";
  if (betType === "ワイド") return "双方3着内";
  return "的中";
}

function RaceDetailView({ raceId }: { raceId: number }) {
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

  return (
    <div className="race-detail">
      {formatConditions(data) && <p className="race-conditions">{formatConditions(data)}</p>}
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
      <details className="collapsible-panel bet-candidates">
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
      </details>
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
                {b.is_settled && ` → 払戻 ${Math.round(b.payout ?? 0).toLocaleString()}円`}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function RaceRow({
  race,
  open,
  onToggle,
}: {
  race: RaceSummary;
  open: boolean;
  onToggle: () => void;
}) {
  return (
    <Fragment>
      <tr className="row-clickable" onClick={onToggle}>
        <td>{formatDate(race.race_date)}</td>
        <td>{race.venue}</td>
        <td>{race.race_number}</td>
        <td>{formatCourse(race.track_type, race.distance)}</td>
        <td>{race.race_name || "-"}</td>
        <td>{formatFullDateTime(race.start_time)}</td>
        <td>{race.entry_count}</td>
        <td>
          <span className={`race-status ${race.finished ? "finished" : "unfinished"}`}>
            {raceStatusLabel(race.finished)}
          </span>
        </td>
        <td>
          {race.top_prediction
            ? `${race.top_prediction.horse_number}番 ${
                race.top_prediction.horse_name ?? ""
              } (${formatPercent(race.top_prediction.score)})`
            : "-"}
        </td>
        <td>{race.bet_count > 0 ? `${race.bet_count}件` : ""}</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={10}>
            <RaceDetailView raceId={race.id} />
          </td>
        </tr>
      )}
    </Fragment>
  );
}

export default function RacesPage() {
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState({
    race_name: "",
    race_date: "",
    race_number: "",
    venue: "",
    status: "",
    horse_name: "",
    jockey: "",
    prediction: "",
    bet: "",
  });
  const [openId, setOpenId] = useState<number | null>(null);
  const offset = page * PAGE_SIZE;
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    offset: String(offset),
  });
  Object.entries(filters).forEach(([key, value]) => {
    if (value) params.set(key, value);
  });
  const query = params.toString();
  const { data, error } = usePolling<RacesResponse>(
    () => getJSON(`/api/races?${query}`),
    30000,
    [query]
  );

  const total = data?.total ?? 0;
  const rowCount = data?.races.length ?? 0;
  const start = !data || total === 0 ? 0 : data.offset + 1;
  const end = data ? Math.min(data.offset + rowCount, total) : 0;
  const canGoPrev = page > 0;
  const canGoNext = data ? data.offset + rowCount < total : false;
  const lastPage = total > 0 ? Math.floor((total - 1) / PAGE_SIZE) : 0;

  const changePage = (nextPage: number) => {
    setOpenId(null);
    setPage(nextPage);
  };
  const updateFilter = (key: keyof typeof filters, value: string) => {
    setOpenId(null);
    setPage(0);
    setFilters((current) => ({ ...current, [key]: value }));
  };
  const clearFilters = () => {
    setOpenId(null);
    setPage(0);
    setFilters({
      race_name: "",
      race_date: "",
      race_number: "",
      venue: "",
      status: "",
      horse_name: "",
      jockey: "",
      prediction: "",
      bet: "",
    });
  };

  if (error) return <ErrorNote message={error} />;
  if (!data) return <div className="loading">読み込み中...</div>;

  return (
    <div className="races-page">
      <div className="pagination-bar">
        <span className="muted">
          {total.toLocaleString()}件中 {start.toLocaleString()}-{end.toLocaleString()}件を表示
        </span>
        <div className="pagination-actions">
          <button disabled={!canGoPrev} onClick={() => changePage(0)}>
            最初のページ
          </button>
          <button disabled={!canGoPrev} onClick={() => changePage(page - 1)}>
            前のページ
          </button>
          <span>
            {page + 1} / {lastPage + 1}ページ
          </span>
          <button disabled={!canGoNext} onClick={() => changePage(page + 1)}>
            次のページ
          </button>
          <button disabled={!canGoNext} onClick={() => changePage(lastPage)}>
            最後のページ
          </button>
        </div>
      </div>

      <h2>レース一覧</h2>
      <div className="race-filters">
          <label>
            <span>レース名</span>
            <input
              value={filters.race_name}
              onChange={(e) => updateFilter("race_name", e.target.value)}
              placeholder="レース名で検索"
            />
          </label>
          <label>
            <span>日付</span>
            <input
              type="date"
              value={filters.race_date}
              onChange={(e) => updateFilter("race_date", e.target.value)}
            />
          </label>
          <label>
            <span>R</span>
            <select
              value={filters.race_number}
              onChange={(e) => updateFilter("race_number", e.target.value)}
            >
              <option value="">すべて</option>
              {Array.from({ length: 12 }, (_, index) => index + 1).map((raceNumber) => (
                <option key={raceNumber} value={raceNumber}>
                  {raceNumber}R
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>競馬場</span>
            <select value={filters.venue} onChange={(e) => updateFilter("venue", e.target.value)}>
              <option value="">すべて</option>
              {(data?.venues ?? []).map((venue) => (
                <option key={venue} value={venue}>
                  {venue}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>状態</span>
            <select value={filters.status} onChange={(e) => updateFilter("status", e.target.value)}>
              <option value="">すべて</option>
              <option value="upcoming">発走前</option>
              <option value="finished">確定</option>
              <option value="unfinished">未確定</option>
            </select>
          </label>
          <label>
            <span>馬名</span>
            <input
              value={filters.horse_name}
              onChange={(e) => updateFilter("horse_name", e.target.value)}
              placeholder="馬名で絞り込み"
            />
          </label>
          <label>
            <span>騎手</span>
            <input
              value={filters.jockey}
              onChange={(e) => updateFilter("jockey", e.target.value)}
              placeholder="騎手名で絞り込み"
            />
          </label>
          <label>
            <span>予測</span>
            <select
              value={filters.prediction}
              onChange={(e) => updateFilter("prediction", e.target.value)}
            >
              <option value="">すべて</option>
              <option value="yes">予測あり</option>
              <option value="no">予測なし</option>
            </select>
          </label>
          <label>
            <span>買い</span>
            <select value={filters.bet} onChange={(e) => updateFilter("bet", e.target.value)}>
              <option value="">すべて</option>
              <option value="yes">買いあり</option>
              <option value="no">買いなし</option>
            </select>
          </label>
          <button className="secondary" onClick={clearFilters}>
            クリア
          </button>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>日付</th>
            <th>競馬場</th>
            <th>R</th>
            <th>コース</th>
            <th>レース名</th>
            <th>発走</th>
            <th>頭数</th>
            <th>状態</th>
            <th>予測1位</th>
            <th>買い</th>
          </tr>
        </thead>
        <tbody>
          {data.races.map((race) => (
            <RaceRow
              key={race.id}
              race={race}
              open={openId === race.id}
              onToggle={() => setOpenId(openId === race.id ? null : race.id)}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
