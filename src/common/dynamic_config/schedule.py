"""定期実行ジョブの次回実行時刻の算出と一覧ビュー。"""

from datetime import datetime, timedelta

from src.common.db import session_scope
from src.common.models import Entry, JobRun, JobTrigger, Race
from src.common.timeutils import now_jst

from .defaults import SCHEDULED_JOB_DEFS
from .parsing import parse_exact_time, weekdays_from_str
from .store import merged_settings

# 発走後・未確定レースの結果反映を起動する期間。collector の RESULT_FETCH_DAYS に合わせる
# (common は collector に依存させないため定数を再掲する)。
_SETTLE_RESULT_WINDOW_DAYS = 7


def _latest_scheduled_started_at(session, job_name: str) -> datetime | None:
    row = (
        session.query(JobRun)
        .filter(JobRun.job_name == job_name, JobRun.trigger == JobTrigger.SCHEDULED.value)
        .order_by(JobRun.started_at.desc().nullslast(), JobRun.created_at.desc())
        .first()
    )
    return row.started_at if row is not None else None


def _next_interval_run_at(session, job_name: str, interval_minutes: int) -> datetime:
    latest = _latest_scheduled_started_at(session, job_name)
    if latest is None:
        return now_jst()
    return latest + timedelta(minutes=interval_minutes)


def _next_bet_decide_run_at(session, before_start_minutes: int) -> datetime | None:
    now = now_jst()
    target = (
        session.query(Race.start_time)
        .filter(Race.start_time.isnot(None), Race.start_time > now)
        .order_by(Race.start_time)
        .first()
    )
    if target is None or target[0] is None:
        return None
    return max(now, target[0] - timedelta(minutes=before_start_minutes))


def _next_settle_run_at(session, after_start_minutes: int) -> datetime | None:
    now = now_jst()
    row = (
        session.query(Race.start_time)
        .filter(
            Race.start_time.isnot(None),
            Race.start_time < now,
            Race.race_date >= (now - timedelta(days=_SETTLE_RESULT_WINDOW_DAYS)).date(),
            Race.entries.any(),
            ~Race.entries.any(Entry.finish_position.isnot(None)),
        )
        .order_by(Race.start_time)
        .first()
    )
    if row is None or row[0] is None:
        return None
    return max(now, row[0] + timedelta(minutes=after_start_minutes))


def _restrict_to_weekdays(dt: datetime | None, weekdays: frozenset[int]) -> datetime | None:
    """``dt`` を実行可能な曜日に丸める。当日が対象外なら次の対象曜日の0時へ繰り上げる。"""
    if dt is None or not weekdays:
        return None
    if dt.weekday() in weekdays:
        return dt
    for offset in range(1, 8):
        candidate = dt + timedelta(days=offset)
        if candidate.weekday() in weekdays:
            return candidate.replace(hour=0, minute=0, second=0, microsecond=0)
    return None


def _next_exact_time_run_at(exact_time: str | None, weekdays: frozenset[int]) -> datetime | None:
    if not exact_time or not weekdays:
        return None
    hour, minute = (int(part) for part in exact_time.split(":", 1))
    now = now_jst()
    for offset in range(0, 8):
        candidate_date = (now + timedelta(days=offset)).date()
        if candidate_date.weekday() not in weekdays:
            continue
        candidate = datetime.combine(candidate_date, datetime.min.time()).replace(
            hour=hour,
            minute=minute,
            tzinfo=now.tzinfo,
        )
        if candidate >= now:
            return candidate
    return None


def scheduled_jobs_view() -> list[dict]:
    merged = merged_settings()
    with session_scope() as session:
        items = []
        for item in SCHEDULED_JOB_DEFS:
            job_name = str(item["job_name"])
            enabled = bool(merged[item["enabled_key"]])
            interval = (
                int(merged[item["interval_key"]])
                if item.get("interval_key") and merged.get(item["interval_key"]) is not None
                else None
            )
            before_start = (
                int(merged[item["before_key"]])
                if item.get("before_key") and merged.get(item["before_key"]) is not None
                else None
            )
            after_start = (
                int(merged[item["after_key"]])
                if item.get("after_key") and merged.get(item["after_key"]) is not None
                else None
            )
            exact_time = parse_exact_time(str(item["time_key"]), merged.get(item["time_key"]))
            weekdays = weekdays_from_str(merged[item["days_key"]])

            if exact_time:
                interval = None
                before_start = None
                after_start = None
                next_run_at = _next_exact_time_run_at(exact_time, weekdays)
            elif interval is not None:
                next_run_at = _next_interval_run_at(session, job_name, interval)
            elif before_start is not None:
                next_run_at = _next_bet_decide_run_at(session, before_start)
            elif after_start is not None:
                next_run_at = _next_settle_run_at(session, after_start)
            else:
                next_run_at = None

            if not exact_time:
                next_run_at = _restrict_to_weekdays(next_run_at, weekdays)

            items.append(
                {
                    "job_name": job_name,
                    "enabled_key": item["enabled_key"],
                    "interval_key": item.get("interval_key"),
                    "before_start_key": item.get("before_key"),
                    "after_start_key": item.get("after_key"),
                    "time_key": item.get("time_key"),
                    "days_key": item["days_key"],
                    "label": item["label"],
                    "description": item["description"],
                    "enabled": enabled,
                    "interval_minutes": interval,
                    "before_start_minutes": before_start,
                    "after_start_minutes": after_start,
                    "exact_time": exact_time,
                    "days": sorted(weekdays),
                    "next_run_at": (
                        next_run_at.isoformat() if enabled and next_run_at is not None else None
                    ),
                }
            )
        return items
