import { useEffect, useState } from "react";
import { getJSON, postJSON, putJSON } from "../api";
import { ErrorNote, Toast, usePolling } from "../components";
import type { ScheduledJobSetting, SettingsView, SystemVersion } from "../types";
import { AdminSection } from "./settings/AdminSection";
import { FeatureSelect } from "./settings/FeatureSelect";
import { ScheduleTable } from "./settings/ScheduleTable";
import {
  MODEL_PARAMS,
  daysToString,
  featuresToForm,
  modelToForm,
  parseDays,
  scheduleToForm,
  type FeatureForm,
  type ModelForm,
  type ScheduleForm,
} from "./settings/helpers";

export default function SettingsPage() {
  const [view, setView] = useState<SettingsView | null>(null);
  const [form, setForm] = useState({
    betting_mode: "sim",
    bet_amount: "100",
    bet_score_threshold: "0.15",
    bet_min_expected_value: "1.0",
  });
  const [scheduleForm, setScheduleForm] = useState<ScheduleForm>({});
  const [modelForm, setModelForm] = useState<ModelForm>({});
  const [featureForm, setFeatureForm] = useState<FeatureForm>({});
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [reloading, setReloading] = useState(false);

  const applyView = (v: SettingsView) => {
    setView(v);
    setForm({
      betting_mode: v.editable.betting_mode,
      bet_amount: String(v.editable.bet_amount),
      bet_score_threshold: String(v.editable.bet_score_threshold),
      bet_min_expected_value: String(v.editable.bet_min_expected_value),
    });
    setScheduleForm(scheduleToForm(v.scheduled_jobs));
    setModelForm(modelToForm(v.editable));
    setFeatureForm(featuresToForm(v));
  };

  const toggleFeature = (name: string) => {
    setFeatureForm((prev) => ({ ...prev, [name]: !prev[name] }));
  };

  const setGroupFeatures = (names: string[], value: boolean) => {
    setFeatureForm((prev) => {
      const next = { ...prev };
      for (const name of names) next[name] = value;
      return next;
    });
  };

  useEffect(() => {
    getJSON<SettingsView>("/api/settings")
      .then(applyView)
      .catch((e) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  const updateScheduleField = (key: string, value: string | boolean) => {
    setScheduleForm((prev) => ({ ...prev, [key]: value }));
  };

  const updateScheduleTime = (job: ScheduledJobSetting, value: string) => {
    setScheduleForm((prev) => {
      const next = { ...prev };
      if (job.time_key) next[job.time_key] = value;
      if (value) {
        if (job.interval_key) next[job.interval_key] = "";
        if (job.before_start_key) next[job.before_start_key] = "";
        if (job.after_start_key) next[job.after_start_key] = "";
      }
      return next;
    });
  };

  const updateScheduleRelative = (job: ScheduledJobSetting, key: string, value: string) => {
    setScheduleForm((prev) => {
      const next = { ...prev, [key]: value };
      if (value && job.time_key) next[job.time_key] = "";
      return next;
    });
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
      const payload: Record<string, string | boolean | number | null> = {
        betting_mode: form.betting_mode,
        bet_amount: Number(form.bet_amount),
        bet_score_threshold: Number(form.bet_score_threshold),
        bet_min_expected_value: Number(form.bet_min_expected_value),
      };

      for (const job of view?.scheduled_jobs ?? []) {
        payload[job.enabled_key] = Boolean(scheduleForm[job.enabled_key]);
        if (job.time_key) payload[job.time_key] = String(scheduleForm[job.time_key] ?? "");
        if (job.interval_key) {
          const value = String(scheduleForm[job.interval_key] ?? "").trim();
          payload[job.interval_key] = value ? Number(value) : null;
        }
        if (job.before_start_key) {
          const value = String(scheduleForm[job.before_start_key] ?? "").trim();
          payload[job.before_start_key] = value ? Number(value) : null;
        }
        if (job.after_start_key) {
          const value = String(scheduleForm[job.after_start_key] ?? "").trim();
          payload[job.after_start_key] = value ? Number(value) : null;
        }
        payload[job.days_key] = String(scheduleForm[job.days_key] ?? "");
      }

      for (const p of MODEL_PARAMS) {
        payload[p.key] = Number(modelForm[p.key]);
      }
      payload.model_train_start_date = modelForm.model_train_start_date ?? "";
      payload.model_train_end_date = modelForm.model_train_end_date ?? "";
      const enabledFeatures = Object.entries(featureForm)
        .filter(([, on]) => on)
        .map(([name]) => name);
      payload.model_enabled_features = enabledFeatures.join(",");

      const updated = await putJSON<SettingsView>("/api/settings", payload);
      applyView(updated);
      setMessage("設定を保存しました。次回のジョブ確認から反映されます。");
      setToast("設定を保存しました。次回のジョブ確認から反映されます。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  };

  // 編集途中の内容を破棄し、DBの保存済み設定を読み込み直す
  const discard = async () => {
    setError(null);
    setMessage(null);
    setReloading(true);
    try {
      const fresh = await getJSON<SettingsView>("/api/settings");
      applyView(fresh);
      setToast("編集を破棄しました。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setReloading(false);
    }
  };

  const restartSystem = async () => {
    setError(null);
    setMessage(null);
    const ok = window.confirm(
      "コンテナ(collector / predictor / webui)の再起動をホストのデプロイエージェントに依頼します。" +
        "一時的に画面へ接続できなくなることがあります。実行しますか?"
    );
    if (!ok) return;
    try {
      await postJSON("/api/system/restart");
      setMessage("再起動を依頼しました。数十秒後に画面を再読み込みしてください。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  // バージョン/更新状態。デプロイ進捗を追うため短い間隔でポーリングする。
  const { data: version } = usePolling<SystemVersion>(() => getJSON("/api/system/version"), 5000);

  const deploySystem = async () => {
    setError(null);
    setMessage(null);
    const ok = window.confirm(
      "最新版を取得してビルド・再起動します(ホストのデプロイエージェントが実行)。" +
        "数分かかり、途中で一時的に画面へ接続できなくなることがあります。実行しますか?"
    );
    if (!ok) return;
    try {
      await postJSON("/api/system/deploy");
      setMessage("デプロイを依頼しました。進捗はこの画面に表示されます。");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  if (!view && !error) return <div className="loading">読み込み中...</div>;

  return (
    <div className="settings-page">
      <Toast message={toast} onClose={() => setToast(null)} />
      <h2>賭け設定</h2>
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
      <ScheduleTable
        jobs={view?.scheduled_jobs ?? []}
        scheduleForm={scheduleForm}
        onField={updateScheduleField}
        onTime={updateScheduleTime}
        onRelative={updateScheduleRelative}
        onToggleDay={toggleDay}
      />

      <h2>モデルの学習期間</h2>
      <p className="muted">
        学習に使う確定レースの期間です。空欄なら全期間。変更は次回のモデル学習(再学習)から反映されます。
      </p>
      <div className="backfill-form">
        <label>
          <span>開始日</span>
          <input
            type="date"
            value={modelForm.model_train_start_date ?? ""}
            onChange={(e) =>
              setModelForm({ ...modelForm, model_train_start_date: e.target.value })
            }
          />
        </label>
        <label>
          <span>終了日</span>
          <input
            type="date"
            value={modelForm.model_train_end_date ?? ""}
            onChange={(e) =>
              setModelForm({ ...modelForm, model_train_end_date: e.target.value })
            }
          />
        </label>
      </div>

      <h2>モ学習パラメータ</h2>
      <p className="muted">
        LightGBMのハイパーパラメータです。変更は次回のモデル学習(再学習)から反映されます。
      </p>
      <div className="form model-params-form">
        {MODEL_PARAMS.map((p) => (
          <label key={p.key}>
            <span>{p.label}</span>
            <input
              type="number"
              step={p.step}
              min={p.min}
              value={modelForm[p.key] ?? ""}
              onChange={(e) => setModelForm({ ...modelForm, [p.key]: e.target.value })}
            />
          </label>
        ))}
      </div>

      <h2>使用特徴量</h2>
      <p className="muted">
        学習に使う特徴量を選びます。チェックを外した特徴量は次回のモデル学習から除外されます
        (すべて外した場合は安全のため全特徴量で学習します)。「欠損n%」は最新学習時点で値が欠けていた割合です。
      </p>
      <FeatureSelect
        groups={view?.model_features ?? []}
        featureForm={featureForm}
        onToggle={toggleFeature}
        onGroup={setGroupFeatures}
      />

      <div className="schedule-actions">
        <button className="primary" onClick={save} disabled={saving || reloading}>
          {saving ? "保存中..." : "保存"}
        </button>
        <button
          className="secondary"
          onClick={() => void discard()}
          disabled={saving || reloading}
          title="編集途中の内容を破棄して、保存済みの設定を読み込み直します"
        >
          {reloading ? "読み込み中..." : "中止(変更を破棄)"}
        </button>
        <span className="muted">変更は保存を押すまで反映されません。</span>
      </div>

      {view && (
        <details className="collapsible-panel">
          <summary>環境設定(.env / 変更にはコンテナの再作成が必要)</summary>
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
        </details>
      )}

      <AdminSection
        version={version ?? null}
        onDeploy={() => void deploySystem()}
        onRestart={() => void restartSystem()}
      />
    </div>
  );
}
