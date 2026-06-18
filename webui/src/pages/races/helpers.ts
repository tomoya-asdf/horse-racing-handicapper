import type { RaceBetCandidate, RaceEntry } from "../../types";

export type SortKey =
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

export interface SortState {
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

export function sortEntries(entries: RaceEntry[], sort: SortState): RaceEntry[] {
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

export function formatCourse(track: string | null, distance: number | null): string {
  const course = `${track ?? ""}${distance ? `${distance}m` : ""}`;
  return course || "-";
}

export function formatConditions(d: {
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

export function formatPercent(value: number | null): string {
  if (value === null) return "-";
  return `${(value * 100).toFixed(1)}%`;
}

export function formatExpectedValue(value: number | null): string {
  if (value === null) return "-";
  return value.toFixed(2);
}

export function formatRank(value: number | null): string {
  return value ? `${value}位` : "-";
}

export function formatPopularity(value: number | null): string {
  return value ? `${value}人気` : "-";
}

export function raceStatusLabel(finished: boolean): string {
  return finished ? "確定" : "未確定";
}

export function formatSexAge(sex: string | null, age: number | null): string {
  const s = sex ?? "";
  const a = age != null ? String(age) : "";
  return s || a ? `${s}${a}` : "-";
}

export function formatHorseWeight(weight: number | null, diff: number | null): string {
  if (weight == null) return "-";
  if (diff == null) return String(weight);
  const sign = diff > 0 ? `+${diff}` : String(diff);
  return `${weight}(${sign})`;
}

export function candidateLabel(candidate: RaceBetCandidate): string {
  if (candidate.bet_type === "単勝" || candidate.bet_type === "複勝") {
    return `${candidate.horse_number ?? "-"}番 ${candidate.horse_name ?? ""}`;
  }
  return candidate.combination;
}

export function probabilityLabel(betType: string): string {
  if (betType === "単勝") return "1着";
  if (betType === "複勝") return "3着内";
  if (betType === "馬連") return "1-2着";
  if (betType === "ワイド") return "双方3着内";
  return "的中";
}
