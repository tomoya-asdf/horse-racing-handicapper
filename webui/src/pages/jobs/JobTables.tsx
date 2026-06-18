import { Fragment } from "react";
import { formatDateTime } from "../../api";
import { ErrorNote, StatusBadge } from "../../components";
import type { JobReservation, JobRun } from "../../types";
import {
  HISTORY_PAGE_SIZE,
  RESERVATION_PAGE_SIZE,
  formatParams,
  reservationStatusLabel,
  triggerLabel,
} from "./helpers";

export function TablePager({
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

export function ReservationsTable({
  reservations,
  page,
  onPageChange,
  onCancel,
}: {
  reservations: JobReservation[];
  page: number;
  onPageChange: (page: number) => void;
  onCancel: (id: number) => void;
}) {
  const pageCount = Math.max(1, Math.ceil(reservations.length / RESERVATION_PAGE_SIZE));
  const visible = reservations.slice(
    page * RESERVATION_PAGE_SIZE,
    (page + 1) * RESERVATION_PAGE_SIZE
  );
  return (
    <>
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
          {visible.map((reservation) => (
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
                    onClick={() => onCancel(reservation.id)}
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
        page={page}
        pageCount={pageCount}
        pageSize={RESERVATION_PAGE_SIZE}
        total={reservations.length}
        onPageChange={onPageChange}
      />
    </>
  );
}

export function LatestJobsTable({ jobs }: { jobs: JobRun[] }) {
  return (
    <>
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
          {jobs.length === 0 && (
            <tr>
              <td colSpan={5} className="muted">
                まだ実行履歴がありません
              </td>
            </tr>
          )}
          {jobs.map((job) => (
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
    </>
  );
}

export function JobHistoryTable({
  jobs,
  total,
  page,
  openJobId,
  error,
  onToggle,
  onStop,
  onPageChange,
}: {
  jobs: JobRun[];
  total: number;
  page: number;
  openJobId: number | null;
  error: string | null;
  onToggle: (id: number) => void;
  onStop: (id: number) => void;
  onPageChange: (page: number) => void;
}) {
  // jobs はサーバー側で1ページ分(offset/limit)取得済み。総数 total からページ数を出す。
  const pageCount = Math.max(1, Math.ceil(total / HISTORY_PAGE_SIZE));
  const visible = jobs;
  return (
    <>
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
          {visible.map((job) => (
            <Fragment key={job.id}>
              <tr className="row-clickable" onClick={() => onToggle(job.id)}>
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
                              onStop(job.id);
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
        page={page}
        pageCount={pageCount}
        pageSize={HISTORY_PAGE_SIZE}
        total={total}
        onPageChange={onPageChange}
      />
    </>
  );
}
