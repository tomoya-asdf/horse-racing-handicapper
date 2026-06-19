"""WebUIから編集できる動的設定の値オブジェクト(dataclass)。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class BettingConfig:
    mode: str
    amount: float
    score_threshold: float
    min_expected_value: float


@dataclass(frozen=True)
class ModelConfig:
    """WebUIから編集できる学習設定(特徴量の選択 + LightGBMのハイパーパラメータ)。"""

    learning_rate: float
    num_leaves: int
    max_depth: int
    min_child_samples: int
    reg_alpha: float
    reg_lambda: float
    feature_fraction: float
    bagging_fraction: float
    max_boost_rounds: int
    early_stopping_rounds: int
    valid_fraction: float
    min_races: int
    enabled_features: tuple[str, ...]
    # 学習に使うレースの期間("YYYY-MM-DD" or None=全期間)
    train_start: str | None = None
    train_end: str | None = None


@dataclass(frozen=True)
class ScheduledJobConfig:
    job_name: str
    enabled: bool
    interval_minutes: int | None = None
    before_start_minutes: int | None = None
    after_start_minutes: int | None = None
    exact_time: str | None = None
    weekdays: frozenset[int] = frozenset(range(7))
