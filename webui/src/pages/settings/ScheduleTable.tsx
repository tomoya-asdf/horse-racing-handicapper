import { formatDateTime } from "../../api";
import type { ScheduledJobSetting } from "../../types";
import { WEEKDAYS, parseDays, type ScheduleForm } from "./helpers";

export function ScheduleTable({
  jobs,
  scheduleForm,
  onField,
  onTime,
  onRelative,
  onToggleDay,
}: {
  jobs: ScheduledJobSetting[];
  scheduleForm: ScheduleForm;
  onField: (key: string, value: string | boolean) => void;
  onTime: (job: ScheduledJobSetting, value: string) => void;
  onRelative: (job: ScheduledJobSetting, key: string, value: string) => void;
  onToggleDay: (key: string, day: number) => void;
}) {
  return (
    <div className="table-scroll">
      <table className="table settings-schedule-table">
        <thead>
          <tr>
            <th>ジョブ</th>
            <th>有効</th>
            <th>指定時分</th>
            <th>確認間隔(分)</th>
            <th>発走前(分)</th>
            <th>発走後(分)</th>
            <th>実行曜日</th>
            <th>次回予定</th>
            <th>内容</th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => (
            <tr key={job.job_name}>
              <td>{job.label}</td>
              <td>
                <label className="toggle-row">
                  <input
                    type="checkbox"
                    checked={Boolean(scheduleForm[job.enabled_key])}
                    onChange={(e) => onField(job.enabled_key, e.target.checked)}
                  />
                  <span>{scheduleForm[job.enabled_key] ? "ON" : "OFF"}</span>
                </label>
              </td>
              <td>
                {job.time_key ? (
                  <input
                    className="schedule-time-input"
                    type="time"
                    value={String(scheduleForm[job.time_key] ?? "")}
                    onChange={(e) => onTime(job, e.target.value)}
                  />
                ) : (
                  "-"
                )}
              </td>
              <td>
                {job.interval_key ? (
                  <input
                    className="schedule-number-input"
                    type="number"
                    min={1}
                    step={1}
                    value={String(scheduleForm[job.interval_key] ?? "")}
                    onChange={(e) => onRelative(job, job.interval_key!, e.target.value)}
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
                    value={String(scheduleForm[job.before_start_key] ?? "")}
                    onChange={(e) => onRelative(job, job.before_start_key!, e.target.value)}
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
                    value={String(scheduleForm[job.after_start_key] ?? "")}
                    onChange={(e) => onRelative(job, job.after_start_key!, e.target.value)}
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
                        onClick={() => onToggleDay(job.days_key, day.value)}
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
    </div>
  );
}
