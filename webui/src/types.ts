export interface BetStats {
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
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
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
  };
  readonly: {
    collect_interval_minutes: number;
    predict_interval_minutes: number;
    bet_decision_window_minutes: number;
    scraper_request_interval_seconds: number;
    ipat_dry_run: boolean;
    ipat_credentials_configured: boolean;
  };
  env_settings: EnvSetting[];
}

export interface EnvSetting {
  key: string;
  label: string;
  value: string | number | boolean;
  secret?: boolean;
}

export interface Overview {
  model: { trained: boolean; version: string | null; trained_at: string | null };
  data: {
    race_count: number;
    finished_race_count: number;
    upcoming_race_count: number;
    last_collected_at: string | null;
  };
  modes: Record<string, BetStats>;
  latest_jobs: JobRun[];
  settings: SettingsView;
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
  jockey: string | null;
  weight: number | null;
  odds: number | null;
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
  };
  entries: RaceEntry[];
  bets: RaceBet[];
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
  weight: number | null;
  distance: number | null;
  track_type: string | null;
  going: string | null;
  time_seconds: number | null;
  last_3f: number | null;
  horse_weight: number | null;
}

export interface HorseDetail {
  horse_id: string;
  name: string | null;
  sire_id: string | null;
  sire_name: string | null;
  results_fetched_at: string | null;
  results: HorseResult[];
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
