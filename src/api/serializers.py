"""DBモデル → API応答(dict)へのシリアライズ補助。

FastAPI の app やルーターには依存しない純粋関数群。ルーターから import して使う。
"""

import json
import logging
from datetime import datetime

from src.api.deps import JOB_LABELS
from src.common.models import Bet, BetStatus, JobRun, ModelVersion, Prediction
from src.common.paths import MODEL_PATH

logger = logging.getLogger(__name__)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json_value(raw: str | None, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def bet_stats(bets: list[Bet]) -> dict:
    placed = [b for b in bets if b.status == BetStatus.PLACED.value]
    settled = [b for b in placed if b.is_settled]
    invested = sum(b.amount for b in settled)
    payout = sum(b.payout or 0 for b in settled)
    by_type = {}
    for bet_type in sorted({b.bet_type for b in bets}):
        type_bets = [b for b in bets if b.bet_type == bet_type]
        type_placed = [b for b in type_bets if b.status == BetStatus.PLACED.value]
        type_settled = [b for b in type_placed if b.is_settled]
        type_invested = sum(b.amount for b in type_settled)
        type_payout = sum(b.payout or 0 for b in type_settled)
        by_type[bet_type] = {
            "invested": type_invested,
            "payout": type_payout,
            "recovery_rate": (type_payout / type_invested * 100) if type_invested else None,
            "settled_count": len(type_settled),
            "unsettled_count": len(type_placed) - len(type_settled),
            "pending_count": sum(1 for b in type_bets if b.status == BetStatus.PENDING.value),
            "dry_run_count": sum(1 for b in type_bets if b.status == BetStatus.DRY_RUN.value),
            "failed_count": sum(1 for b in type_bets if b.status == BetStatus.FAILED.value),
        }
    return {
        "invested": invested,
        "payout": payout,
        "recovery_rate": (payout / invested * 100) if invested else None,
        "settled_count": len(settled),
        "unsettled_count": len(placed) - len(settled),
        "pending_count": sum(1 for b in bets if b.status == BetStatus.PENDING.value),
        "dry_run_count": sum(1 for b in bets if b.status == BetStatus.DRY_RUN.value),
        "failed_count": sum(1 for b in bets if b.status == BetStatus.FAILED.value),
        "by_type": by_type,
    }


def job_to_dict(run: JobRun) -> dict:
    return {
        "id": run.id,
        "job_name": run.job_name,
        "label": JOB_LABELS.get(run.job_name, run.job_name),
        "trigger": run.trigger,
        "status": run.status,
        "detail": run.detail,
        "params": run.params,
        "created_at": iso(run.created_at),
        "started_at": iso(run.started_at),
        "finished_at": iso(run.finished_at),
    }


def reservation_to_dict(reservation: dict) -> dict:
    return {
        **reservation,
        "label": JOB_LABELS.get(reservation["job_name"], reservation["job_name"]),
    }


def _latest_prediction_model_version(session) -> str | None:
    row = (
        session.query(Prediction.model_version)
        .filter(Prediction.model_version.isnot(None))
        .order_by(Prediction.created_at.desc(), Prediction.id.desc())
        .first()
    )
    return row[0] if row else None


def model_info(session=None) -> dict:
    latest_model = None
    if session is not None:
        latest_model = session.query(ModelVersion).order_by(ModelVersion.trained_at.desc()).first()
    if not MODEL_PATH.exists():
        version = latest_model.version if latest_model else (
            _latest_prediction_model_version(session) if session is not None else None
        )
        return {
            "trained": bool(version),
            "version": version,
            "trained_at": iso(latest_model.trained_at) if latest_model else None,
        }
    info = {
        "trained": True,
        "version": latest_model.version if latest_model else (
            _latest_prediction_model_version(session) if session is not None else None
        ),
        "trained_at": iso(latest_model.trained_at) if latest_model else None,
    }
    try:
        info["trained_at"] = datetime.fromtimestamp(MODEL_PATH.stat().st_mtime).isoformat()
    except OSError:
        pass
    if info["version"] is None:
        try:
            import joblib

            bundle = joblib.load(MODEL_PATH)
            info["version"] = bundle.get("version")
        except ModuleNotFoundError as exc:
            logger.info("model bundle metadata unavailable in api image: %s", exc)
        except Exception as exc:
            logger.warning("failed to read model bundle metadata: %s", exc)
    return info


def model_version_to_dict(model_version: ModelVersion) -> dict:
    return {
        "version": model_version.version,
        "trained_at": iso(model_version.trained_at),
        "race_count": model_version.race_count,
        "row_count": model_version.row_count,
        "valid_race_count": model_version.valid_race_count,
        "auc": model_version.auc,
        "logloss": model_version.logloss,
        "n_estimators": model_version.n_estimators,
        "calibrated": bool(model_version.calibrated),
        "feature_columns": _json_value(model_version.feature_columns, []),
        "categorical_features": _json_value(model_version.categorical_features, []),
        "feature_importances": _json_value(model_version.feature_importances, []),
        "metrics": _json_value(model_version.metrics, {}),
        "training_params": _json_value(model_version.training_params, {}),
        "model_path": model_version.model_path,
    }


def _model_bundle_to_dict(bundle: dict, trained_at: datetime | None = None) -> dict:
    return {
        "version": bundle.get("version"),
        "trained_at": iso(trained_at),
        "race_count": None,
        "row_count": None,
        "valid_race_count": None,
        "auc": None,
        "logloss": None,
        "n_estimators": None,
        "calibrated": bool(bundle.get("calibrator")),
        "feature_columns": bundle.get("feature_columns", []),
        "categorical_features": bundle.get("categorical_features", []),
        "feature_importances": [],
        "metrics": {},
        "training_params": {},
        "model_path": str(MODEL_PATH),
    }


def minimal_model_version_dict(version: str) -> dict:
    return {
        "version": version,
        "trained_at": None,
        "race_count": None,
        "row_count": None,
        "valid_race_count": None,
        "auc": None,
        "logloss": None,
        "n_estimators": None,
        "calibrated": False,
        "feature_columns": [],
        "categorical_features": [],
        "feature_importances": [],
        "metrics": {},
        "training_params": {},
        "model_path": None,
    }


def current_model_bundle_dict() -> dict | None:
    if not MODEL_PATH.exists():
        return None
    try:
        import joblib

        trained_at = datetime.fromtimestamp(MODEL_PATH.stat().st_mtime)
        return _model_bundle_to_dict(joblib.load(MODEL_PATH), trained_at)
    except ModuleNotFoundError as exc:
        logger.info("model bundle metadata unavailable in api image: %s", exc)
        return None
    except Exception as exc:
        logger.warning("failed to read model bundle metadata: %s", exc)
        return None


def rank_entries(values: dict[int, float], reverse: bool = False) -> dict[int, int]:
    ranked: dict[int, int] = {}
    previous_value = None
    previous_rank = 0
    for index, (entry_id, value) in enumerate(
        sorted(values.items(), key=lambda item: item[1], reverse=reverse),
        start=1,
    ):
        if previous_value is None or value != previous_value:
            previous_rank = index
            previous_value = value
        ranked[entry_id] = previous_rank
    return ranked
