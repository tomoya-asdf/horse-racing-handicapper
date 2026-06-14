import { useState } from "react";
import { formatDateTime, getJSON, postJSON } from "../api";
import { ErrorNote, StatusBadge, usePolling } from "../components";
import type { JobsResponse } from "../types";

const JOB_BUTTONS = [
  { name: "collect", label: "データ収集", description: "レース、出馬表、オッズ、結果を取得" },
  {
    name: "collect_horses",
    label: "馬過去戦績収集",
    description: "出走馬の過去戦績と血統を補完",
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
];

function isoDaysAgo(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function JobsPage() {
  const { data, error } = usePolling<JobsResponse>(() => getJSON("/api/jobs?limit=50"), 5000);
  const [message, setMessage] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [backfillStart, setBackfillStart] = useState(isoDaysAgo(14));
  const [backfillEnd, setBackfillEnd] = useState(isoDaysAgo(1));
  const [backtestStart, setBacktestStart] = useState(isoDaysAgo(365));
  const [backtestEnd, setBacktestEnd] = useState(isoDaysAgo(1));

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
        モデル学習には確定済みレースが50件以上必要です。
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

      {message && <div className="info-note">{message}</div>}
      <ErrorNote message={actionError} />

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
          {(data?.jobs ?? []).map((job) => (
            <tr key={job.id}>
              <td>{job.label}</td>
              <td>
                <StatusBadge status={job.status} />
              </td>
              <td>{job.trigger === "manual" ? "手動" : "スケジュール"}</td>
              <td>{formatDateTime(job.created_at)}</td>
              <td>{formatDateTime(job.started_at)}</td>
              <td>{formatDateTime(job.finished_at)}</td>
              <td className="detail-cell">{job.detail ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
