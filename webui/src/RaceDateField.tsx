import { useEffect, useMemo, useRef, useState } from "react";

import { getJSON } from "./api";

const WEEKDAYS = ["日", "月", "火", "水", "木", "金", "土"];

function parseISO(iso: string): { y: number; m: number } {
  const [y, m] = iso.split("-").map(Number);
  return { y, m: m - 1 };
}

function toISO(y: number, m: number, d: number): string {
  return `${y}-${String(m + 1).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** その年月のカレンダーグリッド(日曜始まり)。月初までの空白は null。 */
function buildGrid(year: number, month: number): (number | null)[] {
  const firstDow = new Date(year, month, 1).getDay();
  const daysInMonth = new Date(year, month + 1, 0).getDate();
  const cells: (number | null)[] = Array(firstDow).fill(null);
  for (let d = 1; d <= daysInMonth; d += 1) cells.push(d);
  return cells;
}

/**
 * レース一覧の日付フィルタ用カレンダー。
 *
 * ネイティブの <input type="date"> は個別日の色付けができないため、自前の
 * カレンダーに置き換え、DBにレースデータが存在する日(/api/race-dates)を
 * 強調表示する。
 */
export function RaceDateField({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [collected, setCollected] = useState<Set<string>>(new Set());
  const [scheduled, setScheduled] = useState<Set<string>>(new Set());
  const [view, setView] = useState<{ y: number; m: number }>(() => {
    if (value) return parseISO(value);
    const now = new Date();
    return { y: now.getFullYear(), m: now.getMonth() };
  });
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getJSON<{ collected: string[]; scheduled: string[] }>("/api/race-dates")
      .then((d) => {
        setCollected(new Set(d.collected));
        setScheduled(new Set(d.scheduled));
      })
      .catch(() => {
        setCollected(new Set());
        setScheduled(new Set());
      });
  }, []);

  // 外部から日付が設定されたら、その月を表示する
  useEffect(() => {
    if (value) setView(parseISO(value));
  }, [value]);

  // ポップオーバーの外側クリックで閉じる
  useEffect(() => {
    if (!open) return undefined;
    const onDocMouseDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  const grid = useMemo(() => buildGrid(view.y, view.m), [view]);

  const shiftMonth = (delta: number) =>
    setView((v) => {
      const total = v.y * 12 + v.m + delta;
      return { y: Math.floor(total / 12), m: ((total % 12) + 12) % 12 };
    });

  return (
    <div className="race-date-field" ref={ref}>
      <div className="race-date-control">
        <button
          type="button"
          className="race-date-input"
          onClick={() => setOpen((o) => !o)}
        >
          {value || "日付を選択"}
        </button>
        {value && (
          <button
            type="button"
            className="race-date-clear"
            onClick={() => onChange("")}
            aria-label="日付をクリア"
          >
            ×
          </button>
        )}
      </div>
      {open && (
        <div className="race-date-popover">
          <div className="race-date-nav">
            <button type="button" onClick={() => shiftMonth(-1)} aria-label="前の月">
              ‹
            </button>
            <span>
              {view.y}年{view.m + 1}月
            </span>
            <button type="button" onClick={() => shiftMonth(1)} aria-label="次の月">
              ›
            </button>
          </div>
          <div className="race-date-weekdays">
            {WEEKDAYS.map((w) => (
              <span key={w}>{w}</span>
            ))}
          </div>
          <div className="race-date-grid">
            {grid.map((day, index) => {
              if (day === null) {
                // eslint-disable-next-line react/no-array-index-key
                return <span key={`empty-${index}`} className="race-date-cell empty" />;
              }
              const iso = toISO(view.y, view.m, day);
              const isCollected = collected.has(iso);
              // 開催予定だが未収集(netkeibaカレンダー上は開催日だがDBにデータ無し)
              const scheduledOnly = !isCollected && scheduled.has(iso);
              const selected = iso === value;
              const cellClass = [
                "race-date-cell",
                isCollected ? "has-data" : "",
                scheduledOnly ? "scheduled-only" : "",
                selected ? "selected" : "",
              ]
                .filter(Boolean)
                .join(" ");
              return (
                <button
                  key={iso}
                  type="button"
                  className={cellClass}
                  onClick={() => {
                    onChange(iso);
                    setOpen(false);
                  }}
                >
                  {day}
                </button>
              );
            })}
          </div>
          <div className="race-date-legend">
            <span className="race-date-cell has-data legend-swatch" />
            収集済み
            <span className="race-date-cell scheduled-only legend-swatch" />
            開催予定(未収集)
          </div>
        </div>
      )}
    </div>
  );
}
