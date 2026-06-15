import { Fragment, useState } from "react";
import { formatDateTime, getJSON, postJSON } from "../api";
import { ErrorNote, StatusBadge, usePolling } from "../components";
import type { JobsResponse } from "../types";

const JOB_OPTIONS = [
  { name: "collect", label: "データ収集", description: "レース、出馬表、オッズ、結果を取得" },
  {
    name: "collect_horses",
    label: "馬過去戦績収集",
    description: "出走馬の過去戦績と統計を補完",
  },
  {
    name: "collect_jockeys",
    label: "騎手過去戦績収集",
    description: "出走騎手の過去戦績を補完",
  },
  {
    name: "collect_trainers",
    label: "調教師過去戦績収集",
    description: "出走馬の調教師の過去戦績を補完",
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

const JOB_BUTTONS = JOB_OPTIONS.filter((job) => job.name !== "backfill" && job.name !== "backtest");
const RANGE_JOB_NAMES = new Set(["backfill", "backtest"]);
const BACKFILL_MAX_DAYS = 31;
const RESERVATION_PAGE_SIZE = 5;
const HISTORY_PAGE_SIZE = 15;

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

function localDateTimeIn(minutes: number): string {
  const d = new Date(Date.now() + minutes * 60 * 1000);
  return formatLocalDateTime(d);
}

function formatLocalDate(d: Date): string {
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}`;
}

function formatLocalDateTime(d: Date): string {
  d.setSeconds(0, 0);
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth() + 1).padStart(2, "0");
  const dd = String(d.getDate()).padStart(2, "0");
  const hh = String(d.getHours()).padStart(2, "0");
  const mi = String(d.getMinutes()).padStart(2, "0");
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
}

function addDays(d: Date, days: number): Date {
  const next = new Date(d);
  next.setDate(next.getDate() + days);
  return next;
}

function addMinutesToLocalDateTime(value: string, minutes: number): string {
  const [datePart, timePart] = value.split("T");
  const [year, month, day] = datePart.split("-").map(Number);
  const [hour, minute] = timePart.split(":").map(Number);
  const d = new Date(year, month - 1, day, hour, minute + minutes);
  return formatLocalDateTime(d);
}

function buildLongBackfillChunks(years: number): { start_date: string; end_date: string }[] {
  const end = new Date();
  end.setHours(0, 0, 0, 0);
  end.setDate(end.getDate() - 1);

  const start = new Date(end);
  start.setFullYear(start.getFullYear() - years);
  start.setDate(start.getDate() + 1);

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

function triggerLabel(trigger: string): string {
  if (trigger === "manual") return "手動";
  if (trigger === "reserved") return "予約";
  return "スケジュール";
}

function reservationStatusLabel(status: string): string {
  if (status === "pending") return "予約中";
  if (status === "queued") return "投入済み";
  if (status === "cancelled") return "キャンセル";
  return status;
}

function formatParams(raw: string | null): string {
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

export default function JobsPage() {
  const { data, error } = usePolling<JobsResponse>(() => getJSON("/api/jobs?limit=50"), 5000);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [openJobId, setOpenJobId] = useState<number | null>(null);
  const [backfillStart, setBackfillStart] = useState(isoDaysAgo(14));
  const [backfillEnd, setBackfillEnd] = useState(isoDaysAgo(1));
  const [backtestStart, setBacktestStart] = useState(isoDaysAgo(365));
  const [backtestEnd, setBacktestEnd] = useState(isoDaysAgo(1));
  const [reservationJob, setReservationJob] = useState("collect");
  const [reservationRunAt, setReservationRunAt] = useState(localDateTimeIn(10));
  const [reservationStart, setReservationStart] = useState(isoDaysAgo(14));
  const [reservationEnd, setReservationEnd] = useState(isoDaysAgo(1));
  const [reservationPage, setReservationPage] = useState(0);
  const [historyPage, setHistoryPage] = useState(0);
  const [longBackfillYears, setLongBackfillYears] = useState("2");
  const [longBackfillRunAt, setLongBackfillRunAt] = useState(localDateTimeIn(10));
  const [longBackfillGapMinutes, setLongBackfillGapMinutes] = useState("15");
  const [longBackfillBusy, setLongBackfillBusy] = useState(false);

  const selectedReservationJob = JOB_OPTIONS.find((job) => job.name === reservationJob);
  const reservationNeedsRange = RANGE_JOB_NAMES.has(reservationJob);
  const longBackfillChunks = buildLongBackfillChunks(Number(longBackfillYears));
  const longBackfillGap = Math.max(1, Number(longBackfillGapMinutes) || 1);
  const longBackfillLastRunAt =
    longBackfillRunAt.includes("T") && longBackfillChunks.length > 0
      ? addMinutesToLocalDateTime(longBackfillRunAt, (longBackfillChunks.length - 1) * longBackfillGap)
      : "-";
  const reservations = (data?.reservations ?? []).filter(
    (reservation) => reservation.status !== "cancelled"
  );
  const historyJobs = data?.jobs ?? [];
  const reservationPageCount = Math.max(1, Math.ceil(reservations.length / RESERVATION_PAGE_SIZE));
  const historyPageCount = Math.max(1, Math.ceil(historyJobs.length / HISTORY_PAGE_SIZE));
  const visibleReservations = reservations.slice(
    reservationPage * RESERVATION_PAGE_SIZE,
    (reservationPage + 1) * RESERVATION_PAGE_SIZE
  );
  const visibleHistoryJobs = historyJobs.slice(
    historyPage * HISTORY_PAGE_SIZE,
    (historyPage + 1) * HISTORY_PAGE_SIZE
  );

  const runJob = async (name: string, label: string, body?: unknown) => {
    setMessage(null);
    setActionError(null);
    try {
      const result = await postJSON<{ queued: boolean }>(`/api/jobs/${name}/run`, body);
      setMessage(
        result.queued
          ? `「${label}」の実行を依頼しました。担当サービスが数秒以内に開始します。`
          : `「${label}」はすでに実行待ち、または実行中です。`
      );
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  const confirmAndRunJob = (name: string, label: string, body?: unknown) => {
    const ok = window.confirm(`${label}を実行しますか?\n必要な場合だけ実行してください。`);
    if (!ok) return;
    void runJob(name, label, body);
  };

  const reserveJob = async () => {
    setMessage(null);
    setActionError(null);
    const params = reservationNeedsRange
      ? { start_date: reservationStart, end_date: reservationEnd }
      : undefined;
    try {
      await postJSON("/api/job-reservations", {
        job_name: reservationJob,
        run_at: reservationRunAt,
        params,
      });
      setReservationPage(0);
      setMessage(`「${selectedReservationJob?.label ?? reservationJob}」を予約しました。`);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  const reserveLongBackfill = async () => {
    setMessage(null);
    setActionError(null);
    const firstChunk = longBackfillChunks[0];
    const lastChunk = longBackfillChunks[longBackfillChunks.length - 1];
    if (!firstChunk || !lastChunk) {
      setActionError("予約対象の期間を作成できませんでした。");
      return;
    }
    if (!longBackfillRunAt.includes("T")) {
      setActionError("予約開始日時を入力してください。");
      return;
    }

    const ok = window.confirm(
      `過去データ取得を${longBackfillChunks.length}件に分割して予約しますか?\n` +
        `${firstChunk.start_date} - ${lastChunk.end_date}\n` +
        `${longBackfillRunAt} から ${longBackfillGap}分間隔で投入します。`
    );
    if (!ok) return;

    setLongBackfillBusy(true);
    try {
      for (const [index, chunk] of longBackfillChunks.entries()) {
        await postJSON("/api/job-reservations", {
          job_name: "backfill",
          run_at: addMinutesToLocalDateTime(longBackfillRunAt, index * longBackfillGap),
          params: chunk,
        });
      }
      setReservationPage(0);
      setMessage(`過去データ取得を${longBackfillChunks.length}件に分割して予約しました。`);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    } finally {
      setLongBackfillBusy(false);
    }
  };

  const cancelReservation = async (id: number) => {
    setMessage(null);
    setActionError(null);
    try {
      await postJSON(`/api/job-reservations/${id}/cancel`);
      setReservationPage(0);
      setMessage("ジョブ予約をキャンセルしました。");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  const stopJob = async (id: number) => {
    setMessage(null);
    setActionError(null);
    try {
      await postJSON(`/api/jobs/${id}/stop`);
      setMessage("実行待ちジョブを停止しました。");
    } catch (e) {
      setActionError(e instanceof Error ? e.message : String(e));
    }
  };

  return (
    <div>
      <h2>手動実行</h2>
      <p className="muted">
        実行依頼はキューに登録され、collector / predictor サービスが短い間隔で取得して実行します。
      </p>
      <div className="job-buttons">
        {JOB_BUTTONS.map((job) => (
          <button
            key={job.name}
            className="job-button"
            onClick={() => confirmAndRunJob(job.name, job.label)}
          >
            <span className="job-button-label">{job.label}</span>
            <span className="job-button-desc">{job.description}</span>
          </button>
        ))}
      </div>

      <h2>過去データ取得(バックフィル)</h2>
      <p className="muted">
        初回セットアップ時など、過去の開催日のレース、最終オッズ、確定結果をまとめて取得します。
        モデル学習には確定済みレースが一定件数必要です。
      </p>
      <div className="backfill-form">
        <label>
          <span>開始日</span>
          <input
            type="date"
            value={backfillStart}
            onChange={(e) => setBackfillStart(e.target.value)}
          />
        </label>
        <label>
          <span>終了日</span>
          <input type="date" value={backfillEnd} onChange={(e) => setBackfillEnd(e.target.value)} />
        </label>
        <button
          className="primary"
          onClick={() =>
            confirmAndRunJob("backfill", "過去データ取得", {
              start_date: backfillStart,
              end_date: backfillEnd,
            })
          }
        >
          取得を開始
        </button>
      </div>

      <h2>長期バックフィル予約</h2>
      <p className="muted">
        1回31日以内の過去データ取得に自動分割して予約します。2〜3年分をまとめて準備したいときに使います。
      </p>
      <div className="backfill-form reservation-form long-backfill-form">
        <label>
          <span>取得期間</span>
          <select value={longBackfillYears} onChange={(e) => setLongBackfillYears(e.target.value)}>
            <option value="1">直近1年</option>
            <option value="2">直近2年</option>
            <option value="3">直近3年</option>
          </select>
        </label>
        <label>
          <span>予約開始日時</span>
          <input
            type="datetime-local"
            value={longBackfillRunAt}
            onChange={(e) => setLongBackfillRunAt(e.target.value)}
          />
        </label>
        <label>
          <span>予約間隔(分)</span>
          <input
            type="number"
            min="1"
            step="1"
            value={longBackfillGapMinutes}
            onChange={(e) => setLongBackfillGapMinutes(e.target.value)}
          />
        </label>
        <div className="long-backfill-preview">
          <span>{longBackfillChunks.length.toLocaleString()}件に分割</span>
          <span>
            {longBackfillChunks[0]?.start_date} -{" "}
            {longBackfillChunks[longBackfillChunks.length - 1]?.end_date}
          </span>
          <span>最終予約: {longBackfillLastRunAt}</span>
        </div>
        <button
          className="primary"
          disabled={longBackfillBusy}
          onClick={() => void reserveLongBackfill()}
        >
          長期取得を予約
        </button>
      </div>

      <h2>回収率バックテスト</h2>
      <p className="muted">
        指定期間の確定レースで、予測、賭け、決済をシミュレートします。結果は実行履歴に表示されます。
      </p>
      <div className="backfill-form">
        <label>
          <span>開始日</span>
          <input
            type="date"
            value={backtestStart}
            onChange={(e) => setBacktestStart(e.target.value)}
          />
        </label>
        <label>
          <span>終了日</span>
          <input type="date" value={backtestEnd} onChange={(e) => setBacktestEnd(e.target.value)} />
        </label>
        <button
          className="primary"
          onClick={() =>
            confirmAndRunJob("backtest", "回収率バックテスト", {
              start_date: backtestStart,
              end_date: backtestEnd,
            })
          }
        >
          バックテストを実行
        </button>
      </div>

      <h2>ジョブ予約</h2>
      <p className="muted">
        実行日時を指定して、1回だけジョブを自動投入します。過去データ取得と回収率バックテストは期間指定が必要です。
      </p>
      <div className="backfill-form reservation-form">
        <label>
          <span>実行日時</span>
          <input
            type="datetime-local"
            value={reservationRunAt}
            onChange={(e) => setReservationRunAt(e.target.value)}
          />
        </label>
        <label>
          <span>ジョブ</span>
          <select value={reservationJob} onChange={(e) => setReservationJob(e.target.value)}>
            {JOB_OPTIONS.map((job) => (
              <option key={job.name} value={job.name}>
                {job.label}
              </option>
            ))}
          </select>
        </label>
        {reservationNeedsRange && (
          <>
            <label>
              <span>開始日</span>
              <input
                type="date"
                value={reservationStart}
                onChange={(e) => setReservationStart(e.target.value)}
              />
            </label>
            <label>
              <span>終了日</span>
              <input
                type="date"
                value={reservationEnd}
                onChange={(e) => setReservationEnd(e.target.value)}
              />
            </label>
          </>
        )}
        <button className="primary" onClick={() => void reserveJob()}>
          予約する
        </button>
      </div>

      {message && <div className="info-note">{message}</div>}
      <ErrorNote message={actionError} />

      <h2>予約一覧</h2>
      <table className="table latest-jobs-table">
        <thead>
          <tr>
            <th>実行予定</th>
            <th>ジョブ</th>
            <th>状態</th>
            <th>パラメータ</th>
            <th>登録</th>
            <th>投入ID</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          {reservations.length === 0 && (
            <tr>
              <td colSpan={7} className="muted">
                予約はありません
              </td>
            </tr>
          )}
          {visibleReservations.map((reservation) => (
            <tr key={reservation.id}>
              <td>{formatDateTime(reservation.run_at)}</td>
              <td>{reservation.label}</td>
              <td>
                <span className={`badge badge-${reservation.status}`}>
                  {reservationStatusLabel(reservation.status)}
                </span>
              </td>
              <td className="detail-cell">{formatParams(reservation.params)}</td>
              <td>{formatDateTime(reservation.created_at)}</td>
              <td>{reservation.queued_run_id ?? "-"}</td>
              <td>
                {reservation.status === "pending" ? (
                  <button
                    className="secondary danger-outline"
                    onClick={() => void cancelReservation(reservation.id)}
                  >
                    キャンセル
                  </button>
                ) : (
                  "-"
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <TablePager
        page={reservationPage}
        pageCount={reservationPageCount}
        pageSize={RESERVATION_PAGE_SIZE}
        total={reservations.length}
        onPageChange={setReservationPage}
      />

      <h2>ジョブの最終実行</h2>
      <table className="table latest-jobs-table">
        <thead>
          <tr>
            <th>ジョブ</th>
            <th>状態</th>
            <th>実行種別</th>
            <th>開始</th>
            <th>結果</th>
          </tr>
        </thead>
        <tbody>
          {(data?.latest_jobs ?? []).length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                まだ実行履歴がありません
              </td>
            </tr>
          )}
          {(data?.latest_jobs ?? []).map((job) => (
            <tr key={job.id}>
              <td>{job.label}</td>
              <td>
                <StatusBadge status={job.status} />
              </td>
              <td>{triggerLabel(job.trigger)}</td>
              <td>{formatDateTime(job.started_at ?? job.created_at)}</td>
              <td className="detail-cell">{job.detail ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>実行履歴</h2>
      <ErrorNote message={error} />
      <table className="table">
        <thead>
          <tr>
            <th>ジョブ</th>
            <th>状態</th>
            <th>実行種別</th>
            <th>依頼</th>
            <th>開始</th>
            <th>終了</th>
            <th>結果</th>
          </tr>
        </thead>
        <tbody>
          {visibleHistoryJobs.map((job) => (
            <Fragment key={job.id}>
              <tr
                className="row-clickable"
                onClick={() => setOpenJobId((current) => (current === job.id ? null : job.id))}
              >
                <td>{job.label}</td>
                <td>
                  <StatusBadge status={job.status} />
                </td>
                <td>{triggerLabel(job.trigger)}</td>
                <td>{formatDateTime(job.created_at)}</td>
                <td>{formatDateTime(job.started_at)}</td>
                <td>{formatDateTime(job.finished_at)}</td>
                <td className="detail-cell">{job.detail ? job.detail.split("\n")[0] : "-"}</td>
              </tr>
              {openJobId === job.id && (
                <tr>
                  <td colSpan={7}>
                    <div className="job-detail-panel">
                      <div className="job-detail-actions">
                        <strong>実行状況ログ</strong>
                        {job.status === "queued" && (
                          <button
                            className="secondary danger-outline"
                            onClick={(e) => {
                              e.stopPropagation();
                              void stopJob(job.id);
                            }}
                          >
                            停止
                          </button>
                        )}
                      </div>
                      <div className="job-detail-grid">
                        <span>ID</span>
                        <span>{job.id}</span>
                        <span>状態</span>
                        <span>{job.status}</span>
                        <span>パラメータ</span>
                        <span className="detail-cell">{job.params ?? "-"}</span>
                        <span>ログ</span>
                        <span className="detail-cell">
                          {job.detail ??
                            (job.status === "running"
                              ? "実行中です。完了後に結果ログが反映されます。"
                              : "-")}
                        </span>
                      </div>
                    </div>
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
        </tbody>
      </table>
      <TablePager
        page={historyPage}
        pageCount={historyPageCount}
        pageSize={HISTORY_PAGE_SIZE}
        total={historyJobs.length}
        onPageChange={(nextPage) => {
          setOpenJobId(null);
          setHistoryPage(nextPage);
        }}
      />

    </div>
  );
}

function TablePager({
  page,
  pageCount,
  pageSize,
  total,
  onPageChange,
}: {
  page: number;
  pageCount: number;
  pageSize: number;
  total: number;
  onPageChange: (page: number) => void;
}) {
  if (total <= pageSize) return null;
  return (
    <div className="pagination-bar table-pagination">
      <span className="muted">
        {total.toLocaleString()}件中{" "}
        {(page * pageSize + 1).toLocaleString()}-
        {Math.min((page + 1) * pageSize, total).toLocaleString()}件を表示
      </span>
      <div className="pagination-actions">
        <button disabled={page === 0} onClick={() => onPageChange(page - 1)}>
          前のページ
        </button>
        <span>
          {page + 1} / {pageCount}ページ
        </span>
        <button disabled={page + 1 >= pageCount} onClick={() => onPageChange(page + 1)}>
          次のページ
        </button>
      </div>
    </div>
  );
}
