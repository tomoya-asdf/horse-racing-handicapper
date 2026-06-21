"""発走直前(賭け対象決定の処理時間帯)に重いジョブを待避させる判定。

bet_decide(賭け対象決定)は「発走の指定分前までに必ず終わらせたい」唯一の
時間制約ジョブ。発走が近い間は collect / predict / train などの非時間制約ジョブを
スキップさせ、netkeiba へのリクエストと CPU を bet_decide(と settle)に空ける。
collector / predictor の両プロセスから参照する。
"""

from datetime import timedelta

from src.common import jobs
from src.common.config import settings
from src.common.db import session_scope
from src.common.dynamic_config import load_scheduled_job_config
from src.common.models import Entry, Race
from src.common.timeutils import now_jst

# 賭け対象決定リードに上乗せする猶予(分)。bet_decide のオッズ取得・発注が
# 終わるまでの余白として確保し、ぎりぎりで他ジョブと競合しないようにする。
BETTING_PRIORITY_BUFFER_MINUTES = 3


def betting_priority_active() -> bool:
    """発走が近く、bet_decide に処理を集中させるべき時間帯なら True を返す。

    未確定レースの発走が「賭け対象決定リード + 猶予」以内に迫っている間だけ
    True になる。bet_decide が exact_time 運用などでリードを持たない場合は
    .env の BET_DECISION_LEAD_MINUTES を既定リードとして使う。bet_decide が
    無効なら優先する必要が無いため常に False を返す。
    """
    config = load_scheduled_job_config(jobs.BET_DECIDE)
    if config is None or not config.enabled:
        return False
    lead_minutes = (
        config.before_start_minutes
        if config.before_start_minutes is not None
        else settings.BET_DECISION_LEAD_MINUTES
    )
    window = lead_minutes + BETTING_PRIORITY_BUFFER_MINUTES
    now = now_jst()
    with session_scope() as session:
        race = (
            session.query(Race.id)
            .filter(
                Race.start_time.isnot(None),
                Race.start_time > now,
                Race.start_time <= now + timedelta(minutes=window),
                Race.entries.any(),
                ~Race.entries.any(Entry.finish_position.isnot(None)),
            )
            .first()
        )
        return race is not None
