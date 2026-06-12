"""時刻まわりの共通ユーティリティ。

レースの発走時刻は日本時間(JST)であり、DBにはnaive datetimeとして保存する。
コンテナのシステムタイムゾーン(既定UTC)に依存しないよう、現在時刻の取得は
必ず ``now_jst()`` を使い、DB内のdatetimeはすべて「naiveなJST」で統一する。
"""

from datetime import datetime
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")


def now_jst() -> datetime:
    """日本時間の現在時刻をnaive datetimeで返す。"""
    return datetime.now(JST).replace(tzinfo=None)
