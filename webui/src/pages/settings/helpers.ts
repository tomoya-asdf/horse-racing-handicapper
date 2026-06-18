import type { ModelParamKey, ScheduledJobSetting, SettingsView } from "../../types";

export type ScheduleForm = Record<string, string | boolean>;
export type ModelForm = Record<string, string>;
export type FeatureForm = Record<string, boolean>;

// モデル学習パラメータの入力欄定義(キー・ラベル・入力刻み・最小値)
export const MODEL_PARAMS: { key: ModelParamKey; label: string; step: number; min: number }[] = [
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

// 曜日番号はPythonのdate.weekday()に合わせ、月=0〜日=6。表示は日曜始まり。
export const WEEKDAYS: { label: string; value: number }[] = [
  { label: "日", value: 6 },
  { label: "月", value: 0 },
  { label: "火", value: 1 },
  { label: "水", value: 2 },
  { label: "木", value: 3 },
  { label: "金", value: 4 },
  { label: "土", value: 5 },
];

export function modelToForm(editable: SettingsView["editable"]): ModelForm {
  const form: ModelForm = {};
  for (const p of MODEL_PARAMS) form[p.key] = String(editable[p.key]);
  form.model_train_start_date = String(editable.model_train_start_date ?? "");
  form.model_train_end_date = String(editable.model_train_end_date ?? "");
  return form;
}

export function featuresToForm(view: SettingsView): FeatureForm {
  const form: FeatureForm = {};
  for (const group of view.model_features) {
    for (const feature of group.features) form[feature.name] = feature.enabled;
  }
  return form;
}

export function parseDays(value: string | boolean | undefined): Set<number> {
  return new Set(
    String(value ?? "")
      .split(",")
      .filter((part) => part.trim() !== "")
      .map(Number)
  );
}

export function daysToString(days: Set<number>): string {
  return [...days].sort((a, b) => a - b).join(",");
}

export function scheduleToForm(jobs: ScheduledJobSetting[]): ScheduleForm {
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
