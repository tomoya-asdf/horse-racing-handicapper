"""Runtime-editable settings stored in app_settings.

.env values are defaults. Values saved from the WebUI override those defaults
without requiring a container restart.

実装はサブモジュールに分割している(従来の単一ファイルからの分割):

- configs   : 値オブジェクト(BettingConfig / ModelConfig / ScheduledJobConfig)
- defaults  : 設定キーの定義と .env 起点の既定値
- parsing   : WebUI入力 / app_settings 文字列の検証・正規化
- store     : app_settings と既定値のマージ・読み書き
- schedule  : 定期実行ジョブの次回実行時刻の算出と一覧ビュー
- views     : 設定ページ用のビュー生成

外部からは従来どおり ``from src.common.dynamic_config import X`` で参照できる。
"""

from .configs import BettingConfig, ModelConfig, ScheduledJobConfig
from .schedule import scheduled_jobs_view
from .store import (
    load_betting_config,
    load_model_config,
    load_scheduled_job_config,
    save_settings,
)
from .views import get_settings_view

__all__ = [
    "BettingConfig",
    "ModelConfig",
    "ScheduledJobConfig",
    "scheduled_jobs_view",
    "load_betting_config",
    "load_model_config",
    "load_scheduled_job_config",
    "save_settings",
    "get_settings_view",
]
