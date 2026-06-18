export const JOB_OPTIONS = [
  { name: "collect", label: "データ収集", description: "レース、出馬表、オッズ、結果を取得" },
  {
    name: "collect_horses",
    label: "馬過去戦績収集",
    description: "出走馬の過去戦績と統計を補完",
  },
  { name: "predict", label: "AI予想", description: "未確定レースへ予測スコアを作成" },
  {
    name: "bet_decide",
    label: "賭け対象決定",
    description: "予測と最新オッズから買い目を判定",
  },
  { name: "settle", label: "決済", description: "確定済みレースの払戻を反映" },
  { name: "train", label: "モデル学習", description: "蓄積データからモデルを再学習" },
  {
    name: "backfill",
    label: "過去データ取得",
    description: "指定期間の過去レースをまとめて取得",
  },
  {
    name: "backtest",
    label: "回収率バックテスト",
    description: "指定期間で予想、賭け、決済をシミュレート",
  },
];

export const JOB_BUTTONS = JOB_OPTIONS.filter(
  (job) => job.name !== "backfill" && job.name !== "backtest"
);
export const RANGE_JOB_NAMES = new Set(["backfill", "backtest"]);
export const BACKFILL_MAX_DAYS = 31;
export const RESERVATION_PAGE_SIZE = 5;
export const HISTORY_PAGE_SIZE = 15;

export function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export function isoMonthsAgo(months: number): string {
  const d = new Date();
  d.setDate(1);
  d.setMonth(d.getMonth() - months);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  return `${yyyy}-${mm}`;
}

export function localDateTimeIn(minutes: number): string {
  const d = new Date(Date.now() + minutes * 60 * 1000);
  return formatLocalDateTime(d);
}

export function formatLocalDate(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

export function formatLocalDateTime(d: Date): string {
  d.setSeconds(0, 0);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
}

export function addDays(d: Date, days: number): Date {
  const next = new Date(d);
  next.setDate(next.getDate() + days);
  return next;
}

export function addMinutesToLocalDateTime(value: string, minutes: number): string {
  const [datePart, timePart] = value.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  const d = new Date(year, month - 1, day, hour, minute + minutes);
  return formatLocalDateTime(d);
}

export function buildLongBackfillChunks(
  startMonth: string,
  endMonth: string
): { start_date: string; end_date: string }[] {
  const [sy, sm] = startMonth.split("-").map(Number);
  const [ey, em] = endMonth.split("-").map(Number);
  if (!sy || !sm || !ey || !em) return [];

  const start = new Date(sy, sm - 1, 1);
  start.setHours(0, 0, 0, 0);
  // 終了月の末日(翌月0日 = 当月末日)
  let end = new Date(ey, em, 0);
  end.setHours(0, 0, 0, 0);
  // 未来日を取得しないよう前日までに丸める
  const yesterday = new Date();
  yesterday.setHours(0, 0, 0, 0);
  yesterday.setDate(yesterday.getDate() - 1);
  if (end > yesterday) end = yesterday;
  if (start > end) return [];

  const chunks: { start_date: string; end_date: string }[] = [];
  let current = start;
  while (current <= end) {
    const chunkEnd = addDays(current, BACKFILL_MAX_DAYS - 1);
    const safeEnd = chunkEnd > end ? end : chunkEnd;
    chunks.push({ start_date: formatLocalDate(current), end_date: formatLocalDate(safeEnd) });
    current = addDays(safeEnd, 1);
  }
  return chunks;
}

export function triggerLabel(trigger: string): string {
  if (trigger === "manual") return "手動";
  if (trigger === "reserved") return "予約";
  return "スケジュール";
}

export function reservationStatusLabel(status: string): string {
  if (status === "pending") return "予約中";
  if (status === "queued") return "投入済み";
  if (status === "cancelled") return "キャンセル";
  return status;
}

export function formatParams(raw: string | null): string {
  if (!raw) return "-";
  try {
    const params = JSON.parse(raw);
    if (params && typeof params === "object") {
      const start = "start_date" in params ? String(params.start_date) : null;
      const end = "end_date" in params ? String(params.end_date) : null;
      if (start && end) return `${start} - ${end}`;
    }
  } catch {
    /* raw text fallback */
  }
  return raw;
}
