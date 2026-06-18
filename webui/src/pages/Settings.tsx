import { useEffect, useState } from "react";
import { formatDateTime, getJSON, postJSON, putJSON } from "../api";
import { ErrorNote, Toast, usePolling } from "../components";
import type { ModelParamKey, ScheduledJobSetting, SettingsView, SystemVersion } from "../types";

type ScheduleForm = Record<string, string | boolean>;
type ModelForm = Record<string, string>;
type FeatureForm = Record<string, boolean>;

// モデル学習パラメータの入力欄定義(キー・ラベル・入力刻み・最小値)
const MODEL_PARAMS: { key: ModelParamKey; label: string; step: number; min: number }[] = [
  { key: "model_learning_rate", label: "学習率 (learning_rate)", step: 0.01, min: 0 },
  { key: "model_num_leaves", label: "葉の数 (num_leaves)", step: 1, min: 2 },
  { key: "model_max_depth", label: "木の最大深さ (max_depth, -1=無制限)", step: 1, min: -1 },
  { key: "model_min_child_samples", label: "葉の最小データ数 (min_child_samples)", step: 1, min: 1 },
  { key: "model_feature_fraction", label: "特徴量サンプリング率 (feature_fraction)", step: 0.05, min: 0 },
  { key: "model_bagging_fraction", label: "データサンプリング率 (bagging_fraction)", step: 0.05, min: 0 },
  { key: "model_reg_alpha", label: "L1正則化 (reg_alpha)", step: 0.1, min: 0 },
  { key: "model_reg_lambda", label: "L2正則化 (reg_lambda)", step: 0.1, min: 0 },
  { key: "model_max_boost_rounds", label: "最大ブースティング回数 (max_boost_rounds)", step: 50, min: 1 },
  { key: "model_early_stopping_rounds", label: "早期終了ラウンド (early_stopping_rounds)", step: 5, min: 1 },
  { key: "model_valid_fraction", label: "検証データの割合 (valid_fraction)", step: 0.05, min: 0 },
  { key: "model_min_races", label: "学習に必要な最小レース数 (min_races)", step: 5, min: 1 },
];

function modelToForm(editable: SettingsView["editable"]): ModelForm {
  const form: ModelForm = {};
  for (const p of MODEL_PARAMS) form[p.key] = String(editable[p.key]);
  form.model_train_start_date = String(editable.model_train_start_date ?? "");
  form.model_train_end_date = String(editable.model_train_end_date ?? "");
  return form;
}

function featuresToForm(view: SettingsView): FeatureForm {
  const form: FeatureForm = {};
  for (const group of view.model_features) {
    for (const feature of group.features) form[feature.name] = feature.enabled;
  }
  return form;
}

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
    if (job.time_key) form[job.time_key] = job.exact_time ?? "";
    if (job.interval_key) form[job.interval_key] = job.interval_minutes == null ? "" : String(job.interval_minutes);
    if (job.before_start_key) {
      form[job.before_start_key] = job.before_start_minutes == null ? "" : String(job.before_start_minutes);
    }
    if (job.after_start_key) {
      form[job.after_start_key] = job.after_start_minutes == null ? "" : String(job.after_start_minutes);
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
      "システム全体の再起動を試行します。Web UIコンテナからDockerを操作できない環境では、手動コマンドが表示されます。"
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
          {(view?.scheduled_jobs ?? []).map((job) => {
            return (
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
                {job.time_key ? (
                  <input
                    className="schedule-time-input"
                    type="time"
                    value={String(scheduleForm[job.time_key] ?? "")}
                    onChange={(e) => updateScheduleTime(job, e.target.value)}
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
                    onChange={(e) => updateScheduleRelative(job, job.interval_key!, e.target.value)}
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
                    onChange={(e) => updateScheduleRelative(job, job.before_start_key!, e.target.value)}
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
                    onChange={(e) => updateScheduleRelative(job, job.after_start_key!, e.target.value)}
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
            );
          })}
        </tbody>
      </table>
      </div>

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
      <div className="feature-select">
        {(view?.model_features ?? []).map((group) => {
          const names = group.features.map((f) => f.name);
          return (
            <div key={group.group} className="feature-select-group">
              <div className="feature-select-head">
                <h3>{group.group}</h3>
                <div className="feature-select-actions">
                  <button type="button" onClick={() => setGroupFeatures(names, true)}>
                    全選択
                  </button>
                  <button type="button" onClick={() => setGroupFeatures(names, false)}>
                    全解除
                  </button>
                </div>
              </div>
              <div className="feature-checkboxes">
                {group.features.map((feature) => (
                  <label key={feature.name} className="feature-checkbox">
                    <input
                      type="checkbox"
                      checked={Boolean(featureForm[feature.name])}
                      onChange={() => toggleFeature(feature.name)}
                    />
                    <span>
                      {feature.label}
                      {feature.categorical && <span className="muted"> (カテゴリ)</span>}
                      {feature.missing_rate != null && (
                        <span className="muted feature-missing">
                          {" "}
                          欠損{(feature.missing_rate * 100).toFixed(0)}%
                        </span>
                      )}
                    </span>
                  </label>
                ))}
              </div>
            </div>
          );
        })}
      </div>

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

      <section className="settings-deploy-section">
        <h2>
          ソフトウェア更新
          {version?.update_available && <span className="update-badge">更新あり</span>}
        </h2>
        {!version?.available ? (
          <p className="muted">
            デプロイエージェントが未検出です。ホストで{" "}
            <code>scripts/deploy_agent.sh</code>(Linux)または{" "}
            <code>scripts/deploy_agent.ps1</code>(Windows)を起動すると、
            現在のバージョンと更新有無が表示されます。
          </p>
        ) : (
          <>
            <table className="table deploy-status-table">
              <tbody>
                <tr>
                  <td>稼働中バージョン</td>
                  <td>
                    {version.current_sha ?? "-"}
                    {version.current_ref ? ` (${version.current_ref})` : ""}
                  </td>
                </tr>
                <tr>
                  <td>最新バージョン</td>
                  <td>
                    {version.remote_sha ?? "-"}
                    {version.update_available ? " — 更新あり" : " — 最新です"}
                  </td>
                </tr>
                <tr>
                  <td>更新確認</td>
                  <td>{formatDateTime(version.last_checked_at)}</td>
                </tr>
                <tr>
                  <td>デプロイ状態</td>
                  <td>
                    {version.state ?? "-"}
                    {version.last_deploy_at ? ` / 最終: ${formatDateTime(version.last_deploy_at)}` : ""}
                    {version.last_deploy_result ? ` (${version.last_deploy_result})` : ""}
                  </td>
                </tr>
              </tbody>
            </table>
            {version.message && <pre className="deploy-log">{version.message}</pre>}
            <button
              className="secondary danger-outline"
              disabled={version.state === "requested" || version.state === "running"}
              onClick={() => void deploySystem()}
            >
              {version.state === "running" ? "デプロイ中..." : "アップデートを実行"}
            </button>
          </>
        )}
      </section>

      <section className="danger-zone settings-restart-section">
        <div>
          <h2>管理操作</h2>
          <p className="muted">
            システム全体の再起動は通常の設定保存とは別操作です。必要な時だけ実行してください。
          </p>
        </div>
        <button className="secondary danger-outline" onClick={() => void restartSystem()}>
          システム全体を再起動
        </button>
      </section>
    </div>
  );
}
