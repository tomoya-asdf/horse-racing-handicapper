export interface BetStats {
  invested: number;
  payout: number;
  recovery_rate: number | null;
  settled_count: number;
  unsettled_count: number;
  pending_count: number;
  dry_run_count: number;
  failed_count: number;
  by_type: Record<string, BetTypeStats>;
}

export interface BetTypeStats {
  invested: number;
  payout: number;
  recovery_rate: number | null;
  settled_count: number;
  unsettled_count: number;
  pending_count: number;
  dry_run_count: number;
  failed_count: number;
}

export interface JobRun {
  id: number;
  job_name: string;
  label: string;
  trigger: string;
  status: "queued" | "running" | "success" | "failed";
  detail: string | null;
  params: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface JobReservation {
  id: number;
  job_name: string;
  label: string;
  run_at: string | null;
  params: string | null;
  status: "pending" | "queued" | "cancelled" | string;
  queued_run_id: number | null;
  created_at: string | null;
  queued_at: string | null;
  cancelled_at: string | null;
}

export interface JobsResponse {
  jobs: JobRun[];
  latest_jobs: JobRun[];
  scheduled_jobs: ScheduledJobSetting[];
  reservations: JobReservation[];
}

export interface AuthStatus {
  configured: boolean;
  authenticated: boolean;
}

export interface SettingsView {
  editable: {
    betting_mode: string;
    bet_amount: number;
    bet_score_threshold: number;
    bet_min_expected_value: number;
    schedule_collect_enabled: boolean;
    schedule_predict_enabled: boolean;
    schedule_bet_decide_enabled: boolean;
    schedule_settle_enabled: boolean;
    schedule_collect_horses_enabled: boolean;
    schedule_train_enabled: boolean;
    schedule_collect_interval_minutes: number | null;
    schedule_predict_interval_minutes: number | null;
    schedule_collect_horses_interval_minutes: number | null;
    schedule_train_interval_minutes: number | null;
    schedule_bet_decide_before_start_minutes: number | null;
    schedule_settle_after_start_minutes: number | null;
    schedule_collect_time: string | null;
    schedule_predict_time: string | null;
    schedule_collect_horses_time: string | null;
    schedule_train_time: string | null;
    schedule_bet_decide_time: string | null;
    schedule_settle_time: string | null;
    schedule_collect_days: string;
    schedule_predict_days: string;
    schedule_collect_horses_days: string;
    schedule_train_days: string;
    schedule_bet_decide_days: string;
    schedule_settle_days: string;
    model_learning_rate: number;
    model_num_leaves: number;
    model_max_depth: number;
    model_min_child_samples: number;
    model_reg_alpha: number;
    model_reg_lambda: number;
    model_feature_fraction: number;
    model_bagging_fraction: number;
    model_max_boost_rounds: number;
    model_early_stopping_rounds: number;
    model_valid_fraction: number;
    model_min_races: number;
    model_enabled_features: string;
    model_train_start_date: string;
    model_train_end_date: string;
  };
  readonly: {
    scraper_request_interval_seconds: number;
    ipat_dry_run: boolean;
    ipat_credentials_configured: boolean;
  };
  model_features: ModelFeatureGroup[];
  scheduled_jobs: ScheduledJobSetting[];
  env_settings: EnvSetting[];
}

export interface ModelFeatureItem {
  name: string;
  label: string;
  enabled: boolean;
  categorical: boolean;
  missing_rate?: number | null;
}

export interface ModelFeatureGroup {
  group: string;
  features: ModelFeatureItem[];
}

// モデル学習パラメータ(数値)の編集キー。Settings画面の入力欄定義に使う。
export type ModelParamKey =
  | "model_learning_rate"
  | "model_num_leaves"
  | "model_max_depth"
  | "model_min_child_samples"
  | "model_reg_alpha"
  | "model_reg_lambda"
  | "model_feature_fraction"
  | "model_bagging_fraction"
  | "model_max_boost_rounds"
  | "model_early_stopping_rounds"
  | "model_valid_fraction"
  | "model_min_races";

export interface ScheduledJobSetting {
  job_name: string;
  enabled_key: keyof SettingsView["editable"];
  interval_key: keyof SettingsView["editable"] | null;
  before_start_key: keyof SettingsView["editable"] | null;
  after_start_key: keyof SettingsView["editable"] | null;
  time_key: keyof SettingsView["editable"] | null;
  days_key: keyof SettingsView["editable"];
  label: string;
  description: string;
  enabled: boolean;
  interval_minutes: number | null;
  before_start_minutes: number | null;
  after_start_minutes: number | null;
  exact_time: string | null;
  days: number[];
  next_run_at: string | null;
}

export interface EnvSetting {
  key: string;
  label: string;
  value: string | number | boolean;
  secret?: boolean;
}

export interface SystemVersion {
  available: boolean;
  current_sha: string | null;
  current_ref: string | null;
  remote_sha: string | null;
  update_available: boolean;
  last_checked_at: string | null;
  state: string | null;
  last_deploy_at: string | null;
  last_deploy_result: string | null;
  message: string | null;
  agent_seen_at: string | null;
}

export interface Overview {
  model: { trained: boolean; version: string | null; trained_at: string | null };
  data: {
    race_count: number;
    finished_race_count: number;
    horse_result_horse_count: number;
    horse_target_count: number;
    horse_uncollected_count: number;
    horse_collected_race_count: number;
    horse_target_race_count: number;
    upcoming_race_count: number;
    predicted_upcoming_race_count: number;
    last_collected_at: string | null;
  };
  modes: Partial<Record<"sim" | "prod", BetStats>>;
  latest_jobs: JobRun[];
  settings: SettingsView;
}

export interface ModelFeatureImportance {
  name: string;
  importance: number;
  missing_rate?: number | null;
}

export interface ModelVersionDetail {
  version: string;
  trained_at: string | null;
  race_count: number | null;
  row_count: number | null;
  valid_race_count: number | null;
  auc: number | null;
  logloss: number | null;
  n_estimators: number | null;
  calibrated: boolean;
  feature_columns: string[];
  categorical_features: string[];
  feature_importances: ModelFeatureImportance[];
  metrics: Record<string, unknown>;
  training_params: Record<string, unknown>;
  model_path: string | null;
}

// モデル一覧(/api/models)の1件。バージョン比較グラフで使う指標のみ参照する。
export interface ModelVersionSummary {
  version: string;
  trained_at: string | null;
  auc: number | null;
  logloss: number | null;
  race_count: number | null;
}

export interface ModelsListResponse {
  models: ModelVersionSummary[];
}

export interface ModelCalibrationBin {
  mean_predicted: number;
  actual_rate: number;
  count: number;
  score_min: number;
  score_max: number;
}

export interface ModelCalibration {
  version: string;
  sample_count: number;
  win_count: number;
  base_rate: number | null;
  bins: ModelCalibrationBin[];
}

export interface TopPrediction {
  horse_number: number | null;
  horse_id: string | null;
  horse_name: string | null;
  score: number;
  model_version: string;
}

export interface RaceSummary {
  id: number;
  race_key: string;
  race_date: string | null;
  venue: string;
  race_number: number;
  race_name: string | null;
  start_time: string | null;
  distance: number | null;
  track_type: string | null;
  going: string | null;
  race_class: string | null;
  entry_count: number;
  finished: boolean;
  top_prediction: TopPrediction | null;
  bet_count: number;
}

export interface RacesResponse {
  races: RaceSummary[];
  total: number;
  limit: number;
  offset: number;
  venues: string[];
}

export interface RaceEntry {
  id: number;
  horse_number: number;
  horse_id: string | null;
  horse_name: string;
  sex: string | null;
  age: number | null;
  jockey: string | null;
  jockey_id: string | null;
  trainer: string | null;
  trainer_id: string | null;
  weight: number | null;
  horse_weight: number | null;
  horse_weight_diff: number | null;
  odds: number | null;
  pre_race_odds: number | null;
  final_odds: number | null;
  popularity: number | null;
  finish_position: number | null;
  score: number | null;
  ai_rank: number | null;
  odds_rank: number | null;
  expected_value: number | null;
  value_label: string | null;
  ai_vs_odds: string | null;
  has_bet: boolean;
}

export interface RaceAiPick {
  entry_id: number;
  horse_number: number;
  horse_id: string | null;
  horse_name: string;
  score: number;
  ai_rank: number | null;
  odds: number | null;
  odds_rank: number | null;
  expected_value: number | null;
}

export interface RaceDetail {
  id: number;
  race_key: string;
  race_date: string | null;
  venue: string;
  race_number: number;
  race_name: string | null;
  start_time: string | null;
  distance: number | null;
  track_type: string | null;
  direction: string | null;
  going: string | null;
  weather: string | null;
  race_class: string | null;
  model_version: string | null;
  analysis: {
    top_ai: RaceAiPick[];
    score_gap: number | null;
    race_shape: string | null;
    odds_status: RaceOddsStatus[];
  };
  entries: RaceEntry[];
  bet_candidates: RaceBetCandidate[];
  bets: RaceBet[];
  collection_status: RaceCollectionStatus;
}

export interface RaceCollectionStatus {
  horse_results: boolean;
}

export interface RaceBetCandidate {
  bet_type: string;
  entry_id: number;
  horse_number: number | null;
  horse_name: string | null;
  combination: string;
  probability: number;
  odds: number;
  expected_value: number;
}

export interface RaceOddsStatus {
  bet_type: string;
  available: number;
  total: number;
}

export interface RaceBet {
  id: number;
  mode: string;
  status: string;
  bet_type: string;
  horse_number: number | null;
  combination: string | null;
  amount: number;
  odds_at_bet: number | null;
  model_version: string | null;
  payout: number | null;
  is_settled: boolean;
  placed_at: string | null;
}

export interface HorseResult {
  race_key: string | null;
  race_date: string | null;
  venue: string | null;
  race_name: string | null;
  field_size: number | null;
  horse_number: number | null;
  odds: number | null;
  popularity: number | null;
  finish_position: number | null;
  jockey: string | null;
  jockey_id: string | null;
  weight: number | null;
  distance: number | null;
  track_type: string | null;
  going: string | null;
  time_seconds: number | null;
  last_3f: number | null;
  horse_weight: number | null;
}

export interface PedigreeAncestor {
  generation: number;
  position: number;
  ancestor_horse_id: string | null;
  ancestor_name: string | null;
}

export interface HorseDetail {
  horse_id: string;
  name: string | null;
  sire_id: string | null;
  sire_name: string | null;
  results_fetched_at: string | null;
  pedigree: PedigreeAncestor[];
  results: HorseResult[];
}

export interface PersonResult {
  race_key: string | null;
  race_date: string | null;
  venue: string | null;
  race_name: string | null;
  field_size: number | null;
  horse_id: string | null;
  horse_name: string | null;
  horse_number: number | null;
  jockey?: string | null;
  jockey_id?: string | null;
  trainer?: string | null;
  trainer_id?: string | null;
  weight: number | null;
  odds: number | null;
  popularity: number | null;
  finish_position: number | null;
  distance: number | null;
  track_type: string | null;
  going: string | null;
}

export interface JockeyDetail {
  jockey_id: string;
  name: string | null;
  results_fetched_at: string | null;
  years: number[];
  selected_year: number | null;
  results: PersonResult[];
}

export interface TrainerDetail {
  trainer_id: string;
  name: string | null;
  results_fetched_at: string | null;
  years: number[];
  selected_year: number | null;
  results: PersonResult[];
}

export interface BetItem {
  id: number;
  race_id: number;
  race_date: string | null;
  venue: string | null;
  race_number: number | null;
  race_name: string | null;
  horse_number: number | null;
  horse_name: string | null;
  combination: string | null;
  bet_type: string;
  status: string;
  amount: number;
  odds_at_bet: number | null;
  model_version: string | null;
  payout: number | null;
  is_settled: boolean;
  placed_at: string | null;
}

export interface CumulativePoint {
  placed_at: string | null;
  invested: number;
  payout: number;
  recovery_rate: number | null;
}

export interface BetsResponse {
  stats: BetStats;
  bets: BetItem[];
  cumulative: CumulativePoint[];
}
