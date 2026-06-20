import { useState } from "react";
import { getJSON } from "../api";
import { ErrorNote, usePolling } from "../components";
import { RaceDateField } from "../RaceDateField";
import type { RacesResponse } from "../types";
import { RaceRow } from "./races/RaceRow";

const PAGE_SIZE = 50;

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
    trainer: "",
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
      trainer: "",
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
          <div className="race-filter-field">
            <span>日付</span>
            <RaceDateField
              value={filters.race_date}
              onChange={(value) => updateFilter("race_date", value)}
            />
          </div>
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
            <span>状態</span>
            <select value={filters.status} onChange={(e) => updateFilter("status", e.target.value)}>
              <option value="">すべて</option>
              <option value="upcoming">発走前</option>
              <option value="finished">確定</option>
              <option value="unfinished">未確定</option>
            </select>
          </label>
          <label>
            <span>レース名</span>
            <input
              value={filters.race_name}
              onChange={(e) => updateFilter("race_name", e.target.value)}
              placeholder="レース名で検索"
            />
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
            <span>厩舎</span>
            <input
              value={filters.trainer}
              onChange={(e) => updateFilter("trainer", e.target.value)}
              placeholder="厩舎名で絞り込み"
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
