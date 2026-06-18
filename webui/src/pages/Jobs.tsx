import { useState } from "react";
import { getJSON, postJSON } from "../api";
import { ErrorNote, usePolling } from "../components";
import type { JobsResponse } from "../types";
import {
  JOB_BUTTONS,
  JOB_OPTIONS,
  RANGE_JOB_NAMES,
  addMinutesToLocalDateTime,
  buildLongBackfillChunks,
  isoDaysAgo,
  isoMonthsAgo,
  localDateTimeIn,
} from "./jobs/helpers";
import { JobHistoryTable, LatestJobsTable, ReservationsTable } from "./jobs/JobTables";

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
  const [longBackfillStartMonth, setLongBackfillStartMonth] = useState(isoMonthsAgo(24));
  const [longBackfillEndMonth, setLongBackfillEndMonth] = useState(isoMonthsAgo(0));
  const [longBackfillRunAt, setLongBackfillRunAt] = useState(localDateTimeIn(10));
  const [longBackfillGapMinutes, setLongBackfillGapMinutes] = useState("15");
  const [longBackfillBusy, setLongBackfillBusy] = useState(false);

  const selectedReservationJob = JOB_OPTIONS.find((job) => job.name === reservationJob);
  const reservationNeedsRange = RANGE_JOB_NAMES.has(reservationJob);
  const longBackfillChunks = buildLongBackfillChunks(
    longBackfillStartMonth,
    longBackfillEndMonth
  );
  const longBackfillGap = Math.max(1, Number(longBackfillGapMinutes) || 1);
  const longBackfillLastRunAt =
    longBackfillRunAt.includes("T") && longBackfillChunks.length > 0
      ? addMinutesToLocalDateTime(longBackfillRunAt, (longBackfillChunks.length - 1) * longBackfillGap)
      : "-";
  const reservations = (data?.reservations ?? []).filter(
    (reservation) => reservation.status !== "cancelled"
  );
  const historyJobs = data?.jobs ?? [];

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
    const ok = window.confirm("この予約を本当にキャンセルしますか?");
    if (!ok) return;
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
        1回31日以内の過去データ取得に自動分割して予約します。複数年分をまとめて準備したいときに使います。
      </p>
      <div className="backfill-form reservation-form long-backfill-form">
        <label>
          <span>開始年月</span>
          <input
            type="month"
            value={longBackfillStartMonth}
            onChange={(e) => setLongBackfillStartMonth(e.target.value)}
          />
        </label>
        <label>
          <span>終了年月</span>
          <input
            type="month"
            value={longBackfillEndMonth}
            onChange={(e) => setLongBackfillEndMonth(e.target.value)}
          />
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
          {longBackfillChunks.length === 0 ? (
            <span>取得期間が正しくありません(開始年月 ≦ 終了年月)</span>
          ) : (
            <>
              <span>{longBackfillChunks.length.toLocaleString()}件に分割</span>
              <span>
                {longBackfillChunks[0]?.start_date} -{" "}
                {longBackfillChunks[longBackfillChunks.length - 1]?.end_date}
              </span>
              <span>最終予約: {longBackfillLastRunAt}</span>
            </>
          )}
        </div>
        <button
          className="primary"
          disabled={longBackfillBusy || longBackfillChunks.length === 0}
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

      <ReservationsTable
        reservations={reservations}
        page={reservationPage}
        onPageChange={setReservationPage}
        onCancel={(id) => void cancelReservation(id)}
      />

      <LatestJobsTable jobs={data?.latest_jobs ?? []} />

      <JobHistoryTable
        jobs={historyJobs}
        page={historyPage}
        openJobId={openJobId}
        error={error}
        onToggle={(id) => setOpenJobId((current) => (current === id ? null : id))}
        onStop={(id) => void stopJob(id)}
        onPageChange={(nextPage) => {
          setOpenJobId(null);
          setHistoryPage(nextPage);
        }}
      />
    </div>
  );
}
