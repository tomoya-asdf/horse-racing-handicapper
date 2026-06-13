import { useEffect, useState } from "react";
import { formatDateTime, getJSON, putJSON } from "../api";
import { ErrorNote } from "../components";
import type { ScheduledJobSetting, SettingsView } from "../types";

type ScheduleForm = Record<string, string | boolean>;

// 曜日番号はPythonのdate.weekday()に合わせ、月=0〜日=6。表示は日曜始まり。
const WEEKDAYS: { label: string; value: number }[] = [
  { label: "日", value: 6 },
  { label: "月", value: 0 },
  { label: "火", value: 1 },
  { label: "水", value: 2 },
  { label: "木", value: 3 },
  { label: "金", value: 4 },
  { label: "土", value: 5 },
];

function parseDays(value: string | boolean | undefined): Set<number> {
  return new Set(
    String(value ?? "")
      .split(",")
      .filter((part) => part.trim() !== "")
      .map(Number)
  );
}

function daysToString(days: Set<number>): string {
  return [...days].sort((a, b) => a - b).join(",");
}

function scheduleToForm(jobs: ScheduledJobSetting[]): ScheduleForm {
  const form: ScheduleForm = {};
  for (const job of jobs) {
    form[job.enabled_key] = job.enabled;
    if (job.interval_key) form[job.interval_key] = String(job.interval_minutes ?? 1);
    if (job.before_start_key) {
      form[job.before_start_key] = String(job.before_start_minutes ?? 1);
    }
    if (job.after_start_key) {
      form[job.after_start_key] = String(job.after_start_minutes ?? 0);
    }
    form[job.days_key] = (job.days ?? []).join(",");
  }
  return form;
}

export default function SettingsPage() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [form, setForm] = useState({
    betting_mode: "sim",
    bet_amount: "100",
    bet_score_threshold: "0.15",
    bet_min_expected_value: "1.0",
  });
  const [scheduleForm, setScheduleForm] = useState<ScheduleForm>({});
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const applyView = (v: SettingsView) => {
    setView(v);
    setForm({
      betting_mode: v.editable.betting_mode,
      bet_amount: String(v.editable.bet_amount),
      bet_score_threshold: String(v.editable.bet_score_threshold),
      bet_min_expected_value: String(v.editable.bet_min_expected_value),
    });
    setScheduleForm(scheduleToForm(v.scheduled_jobs));
  };

  useEffect(() => {
    getJSON<SettingsView>("/api/settings")
      .then(applyView)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const updateScheduleField = (key: string, value: string | boolean) => {
    setScheduleForm((prev) => ({ ...prev, [key]: value }));
  };

  const toggleDay = (key: string, day: number) => {
    const days = parseDays(scheduleForm[key]);
    if (days.has(day)) {
      days.delete(day);
    } else {
      days.add(day);
    }
    updateScheduleField(key, daysToString(days));
  };

  const save = async () => {
    setError(null);
    setMessage(null);

    if (
      form.betting_mode === "prod" &&
      view?.editable.betting_mode !== "prod" &&
      !window.confirm(
        "本番モード(prod)に切り替えます。IPAT_DRY_RUN=false の場合、実際に購入が行われます。よろしいですか?"
      )
    ) {
      return;
    }

    setSaving(true);
    try {
      const payload: Record<string, string | boolean | number> = {
        betting_mode: form.betting_mode,
        bet_amount: Number(form.bet_amount),
        bet_score_threshold: Number(form.bet_score_threshold),
        bet_min_expected_value: Number(form.bet_min_expected_value),
      };

      for (const job of view?.scheduled_jobs ?? []) {
        payload[job.enabled_key] = Boolean(scheduleForm[job.enabled_key]);
        if (job.interval_key) payload[job.interval_key] = Number(scheduleForm[job.interval_key]);
        if (job.before_start_key) {
          payload[job.before_start_key] = Number(scheduleForm[job.before_start_key]);
        }
        if (job.after_start_key) {
          payload[job.after_start_key] = Number(scheduleForm[job.after_start_key]);
        }
        payload[job.days_key] = String(scheduleForm[job.days_key] ?? "");
      }

      const updated = await putJSON<SettingsView>("/api/settings", payload);
      applyView(updated);
      setMessage("設定を保存しました。次回のジョブ確認から反映されます。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  if (!view && !error) return <div className="loading">読み込み中...</div>;

  return (
    <div className="settings-page">
      <h2>賭け設定 / 再起動不要</h2>
      <ErrorNote message={error} />
      {message && <div className="info-note">{message}</div>}

      <div className="form">
        <label>
          <span>
            賭けモード
            {form.betting_mode === "prod" && <b className="danger-text"> 本番購入モード</b>}
          </span>
          <select
            value={form.betting_mode}
            onChange={(e) => setForm({ ...form, betting_mode: e.target.value })}
          >
            <option value="sim">sim(シミュレーション)</option>
            <option value="prod">prod(本番・実購入)</option>
          </select>
        </label>
        <label>
          <span>1件あたりの賭け金(円・100円単位)</span>
          <input
            type="number"
            min={100}
            step={100}
            value={form.bet_amount}
            onChange={(e) => setForm({ ...form, bet_amount: e.target.value })}
          />
        </label>
        <label>
          <span>賭けるAIスコアの下限(0-1)</span>
          <input
            type="number"
            min={0}
            max={1}
            step={0.01}
            value={form.bet_score_threshold}
            onChange={(e) => setForm({ ...form, bet_score_threshold: e.target.value })}
          />
        </label>
        <label>
          <span>賭ける期待値(score x odds)の下限</span>
          <input
            type="number"
            min={0}
            step={0.05}
            value={form.bet_min_expected_value}
            onChange={(e) => setForm({ ...form, bet_min_expected_value: e.target.value })}
          />
        </label>
      </div>

      <h2>定期実行設定</h2>
      <p className="muted">
        賭け対象決定と決済は固定の確認間隔ではなく、レースの発走時刻を基準に実行します。
      </p>
      <table className="table settings-schedule-table">
        <thead>
          <tr>
            <th>ジョブ</th>
            <th>有効</th>
            <th>確認間隔(分)</th>
            <th>発走前(分)</th>
            <th>発走後(分)</th>
            <th>実行曜日</th>
            <th>次回予定</th>
            <th>内容</th>
          </tr>
        </thead>
        <tbody>
          {(view?.scheduled_jobs ?? []).map((job) => (
            <tr key={job.job_name}>
              <td>{job.label}</td>
              <td>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={Boolean(scheduleForm[job.enabled_key])}
                    onChange={(e) => updateScheduleField(job.enabled_key, e.target.checked)}
                  />
                  <span>{scheduleForm[job.enabled_key] ? "ON" : "OFF"}</span>
                </label>
              </td>
              <td>
                {job.interval_key ? (
                  <input
                    className="schedule-number-input"
                    type="number"
                    min={1}
                    step={1}
                    value={String(scheduleForm[job.interval_key] ?? job.interval_minutes ?? 1)}
                    onChange={(e) => updateScheduleField(job.interval_key!, e.target.value)}
                  />
                ) : (
                  "-"
                )}
              </td>
              <td>
                {job.before_start_key ? (
                  <input
                    className="schedule-number-input"
                    type="number"
                    min={1}
                    step={1}
                    value={String(
                      scheduleForm[job.before_start_key] ?? job.before_start_minutes ?? 1
                    )}
                    onChange={(e) => updateScheduleField(job.before_start_key!, e.target.value)}
                  />
                ) : (
                  "-"
                )}
              </td>
              <td>
                {job.after_start_key ? (
                  <input
                    className="schedule-number-input"
                    type="number"
                    min={0}
                    step={1}
                    value={String(scheduleForm[job.after_start_key] ?? job.after_start_minutes ?? 0)}
                    onChange={(e) => updateScheduleField(job.after_start_key!, e.target.value)}
                  />
                ) : (
                  "-"
                )}
              </td>
              <td>
                <div className="weekday-toggles">
                  {WEEKDAYS.map((day) => {
                    const active = parseDays(scheduleForm[job.days_key]).has(day.value);
                    return (
                      <button
                        type="button"
                        key={day.value}
                        className={`weekday-toggle${active ? " active" : ""}`}
                        aria-pressed={active}
                        onClick={() => toggleDay(job.days_key, day.value)}
                      >
                        {day.label}
                      </button>
                    );
                  })}
                </div>
              </td>
              <td>{job.enabled ? formatDateTime(job.next_run_at) : "-"}</td>
              <td className="muted">{job.description}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="schedule-actions">
        <button className="primary" onClick={save} disabled={saving}>
          {saving ? "保存中..." : "保存"}
        </button>
        <span className="muted">変更は保存を押すまで反映されません。</span>
      </div>

      {view && (
        <>
          <h2>環境設定(.env / 変更にはコンテナの再作成が必要)</h2>
          <table className="table settings-env-table">
            <thead>
              <tr>
                <th>項目</th>
                <th>環境変数</th>
                <th>現在値</th>
              </tr>
            </thead>
            <tbody>
              {view.env_settings.map((item) => (
                <tr key={item.key}>
                  <td>{item.label}</td>
                  <td className="env-key">{item.key}</td>
                  <td>
                    {item.key === "IPAT_DRY_RUN" && item.value === false ? (
                      <b className="danger-text">false(実購入あり)</b>
                    ) : (
                      String(item.value)
                    )}
                    {item.secret && <span className="muted"> (値は非表示)</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}
