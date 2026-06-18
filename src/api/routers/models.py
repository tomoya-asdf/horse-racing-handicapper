"""学習モデルのメタデータ・確率較正の API。"""

from fastapi import APIRouter, HTTPException

from src.api.serializers import (
    _current_model_bundle_dict,
    _minimal_model_version_dict,
    _model_version_to_dict,
)
from src.common.db import get_session
from src.common.models import Entry, ModelVersion, Prediction

router = APIRouter()


@router.get("/api/models")
def list_models(limit: int = 30) -> dict:
    session = get_session()
    try:
        rows = (
            session.query(ModelVersion)
            .order_by(ModelVersion.trained_at.desc(), ModelVersion.version.desc())
            .limit(min(max(limit, 1), 100))
            .all()
        )
        models = [_model_version_to_dict(row) for row in rows]
        current = _current_model_bundle_dict()
        if current and not any(row["version"] == current["version"] for row in models):
            models.insert(0, current)
        known_versions = {row["version"] for row in models}
        prediction_versions = (
            session.query(Prediction.model_version)
            .filter(Prediction.model_version.isnot(None))
            .distinct()
            .order_by(Prediction.model_version.desc())
            .limit(100)
            .all()
        )
        for (version,) in prediction_versions:
            if version not in known_versions:
                models.append(_minimal_model_version_dict(version))
                known_versions.add(version)
        return {"models": models[: min(max(limit, 1), 100)]}
    finally:
        session.close()


@router.get("/api/models/{version}")
def model_detail(version: str) -> dict:
    session = get_session()
    try:
        row = session.get(ModelVersion, version)
        if row is None:
            current = _current_model_bundle_dict()
            if current and current["version"] == version:
                return current
            exists = (
                session.query(Prediction.id)
                .filter(Prediction.model_version == version)
                .first()
            )
            if exists is not None:
                return _minimal_model_version_dict(version)
            raise HTTPException(status_code=404, detail="model version not found")
        return _model_version_to_dict(row)
    finally:
        session.close()


@router.get("/api/models/{version}/calibration")
def model_calibration(version: str, bins: int = 10) -> dict:
    """本番予測の確率較正(キャリブレーション)を集計して返す。

    このバージョンが予測した確定済みレースについて、予測スコア(=1着になる確率)を
    分位で ``bins`` 個のビンに分け、各ビンの「平均予測確率」と「実際の1着率」を返す。
    予測確率が正確なら両者は一致する(プロット上で対角線に乗る)。
    スコア分布は1着率が低い側に偏るため、等幅でなく分位でビン分割する。
    """
    bins = min(max(bins, 2), 20)
    session = get_session()
    try:
        rows = (
            session.query(Prediction.score, Entry.finish_position)
            .join(Entry, Prediction.entry_id == Entry.id)
            .filter(
                Prediction.model_version == version,
                Prediction.score.isnot(None),
                Entry.finish_position.isnot(None),
            )
            .all()
        )
        pairs = sorted(
            ((float(score), 1 if finish == 1 else 0) for score, finish in rows),
            key=lambda p: p[0],
        )
        total = len(pairs)
        wins_total = sum(won for _, won in pairs)
        result_bins = []
        bin_count = max(1, min(bins, total))
        for i in range(bin_count):
            start = i * total // bin_count
            end = (i + 1) * total // bin_count
            group = pairs[start:end]
            if not group:
                continue
            scores = [score for score, _ in group]
            wins = sum(won for _, won in group)
            result_bins.append(
                {
                    "mean_predicted": sum(scores) / len(group),
                    "actual_rate": wins / len(group),
                    "count": len(group),
                    "score_min": scores[0],
                    "score_max": scores[-1],
                }
            )
        return {
            "version": version,
            "sample_count": total,
            "win_count": wins_total,
            "base_rate": (wins_total / total) if total else None,
            "bins": result_bins,
        }
    finally:
        session.close()
