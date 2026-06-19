"""動的設定値のパース・検証(WebUI入力 / app_settings 文字列 → 正規化値)。"""

from datetime import date

from src.common.feature_catalog import normalize_enabled_features

# 検証付きの整数キー / 小数キー(model_enabled_features は別扱い)
_MODEL_INT_KEYS = {
    "model_num_leaves",
    "model_max_depth",
    "model_min_child_samples",
    "model_max_boost_rounds",
    "model_early_stopping_rounds",
    "model_min_races",
}
_MODEL_FLOAT_KEYS = {
    "model_learning_rate",
    "model_reg_alpha",
    "model_reg_lambda",
    "model_feature_fraction",
    "model_bagging_fraction",
    "model_valid_fraction",
}


def _parse_bool(key: str, value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"{key} は true/false を指定してください: {value!r}")


def _parse_number(key: str, value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{key} は数値を指定してください: {value!r}")


def _parse_weekdays(key: str, value: object) -> str:
    """曜日指定を ``"0,1,5"`` 形式の正規化文字列にする。

    WebUIからは配列、app_settingsからはカンマ区切り文字列で渡る。曜日番号は
    月=0〜日=6。空(どの曜日も選ばない)も許可し、その場合ジョブは実行されない。
    """
    if isinstance(value, (list, tuple)):
        raw = list(value)
    else:
        raw = [part for part in str(value).split(",") if part.strip() != ""]
    days: set[int] = set()
    for part in raw:
        try:
            day = int(str(part).strip())
        except (TypeError, ValueError):
            raise ValueError(f"{key} は0〜6の曜日番号で指定してください: {value!r}")
        if not 0 <= day <= 6:
            raise ValueError(f"{key} は0〜6の曜日番号で指定してください: {value!r}")
        days.add(day)
    return ",".join(str(day) for day in sorted(days))


def parse_exact_time(key: str, value: object) -> str | None:
    text = str(value or "").strip()
    if text == "":
        return None
    try:
        hour_text, minute_text = text.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
    except (TypeError, ValueError):
        raise ValueError(f"{key} は HH:MM 形式で指定してください: {value!r}")
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError(f"{key} は HH:MM 形式で指定してください: {value!r}")
    return f"{hour:02d}:{minute:02d}"


def weekdays_from_str(value: object) -> frozenset[int]:
    return frozenset(int(part) for part in str(value).split(",") if part.strip() != "")


def _parse_enabled_features(key: str, value: object) -> str:
    """特徴量の選択を ``"horse_number,age,..."`` 形式の正規化文字列にする。

    WebUIからは配列、app_settingsからはカンマ区切り文字列で渡る。既知の特徴量名のみを
    FEATURE_COLUMNS の並び順で残す。空(全て無効)も許可し、その場合 load_model_config 側で
    全特徴量にフォールバックする。
    """
    if isinstance(value, (list, tuple)):
        raw = [str(part).strip() for part in value]
    else:
        raw = [part.strip() for part in str(value).split(",") if part.strip() != ""]
    return ",".join(normalize_enabled_features(raw))


def _parse_date_setting(key: str, value: object) -> str:
    """学習期間の日付を "YYYY-MM-DD" に正規化する。空(=期間指定なし)も許可。"""
    text = str(value or "").strip()
    if text == "":
        return ""
    try:
        date.fromisoformat(text)
    except (TypeError, ValueError):
        raise ValueError(f"{key} は YYYY-MM-DD 形式で指定してください: {value!r}")
    return text


def _parse_model_setting(key: str, value: object):
    if key == "model_enabled_features":
        return _parse_enabled_features(key, value)
    if key in ("model_train_start_date", "model_train_end_date"):
        return _parse_date_setting(key, value)

    number = _parse_number(key, value)
    if key in _MODEL_INT_KEYS:
        ivalue = int(number)
        if key == "model_max_depth":
            if ivalue != -1 and ivalue < 1:
                raise ValueError(f"{key} は -1(無制限)または1以上で指定してください: {value!r}")
        elif key == "model_num_leaves":
            if ivalue < 2:
                raise ValueError(f"{key} は2以上で指定してください: {value!r}")
        elif ivalue < 1:
            raise ValueError(f"{key} は1以上で指定してください: {value!r}")
        return ivalue

    # 小数キー
    if key == "model_learning_rate":
        if not 0.0 < number <= 1.0:
            raise ValueError(f"{key} は0より大きく1以下で指定してください: {value!r}")
    elif key in ("model_feature_fraction", "model_bagging_fraction"):
        if not 0.0 < number <= 1.0:
            raise ValueError(f"{key} は0より大きく1以下で指定してください: {value!r}")
    elif key == "model_valid_fraction":
        if not 0.0 < number < 1.0:
            raise ValueError(f"{key} は0より大きく1未満で指定してください: {value!r}")
    elif key in ("model_reg_alpha", "model_reg_lambda"):
        if number < 0:
            raise ValueError(f"{key} は0以上で指定してください: {value!r}")
    return float(number)


def parse_setting(key: str, value: object):
    """1つの設定キーを検証・正規化する。不正値は ValueError を送出する。"""
    if key == "betting_mode":
        if value not in ("prod", "sim"):
            raise ValueError(f"betting_mode は 'prod' か 'sim' を指定してください: {value!r}")
        return value

    if key.startswith("model_"):
        return _parse_model_setting(key, value)

    if key.startswith("schedule_") and key.endswith("_enabled"):
        return _parse_bool(key, value)

    if key.startswith("schedule_") and key.endswith("_days"):
        return _parse_weekdays(key, value)

    if key.startswith("schedule_") and key.endswith("_time"):
        return parse_exact_time(key, value)

    if key.startswith("schedule_") and (
        key.endswith("_interval_minutes")
        or key.endswith("_before_start_minutes")
        or key.endswith("_after_start_minutes")
    ):
        if str(value or "").strip() == "":
            return None
        number = int(_parse_number(key, value))
        minimum = 1 if key.endswith("_interval_minutes") or key.endswith("_before_start_minutes") else 0
        if number < minimum:
            raise ValueError(f"{key} は {minimum} 以上で指定してください: {value!r}")
        return number

    number = _parse_number(key, value)
    if key == "bet_amount":
        if number < 100 or number % 100 != 0:
            raise ValueError(f"bet_amount は100円以上・100円単位で指定してください: {value!r}")
    elif key == "bet_score_threshold":
        if not 0.0 <= number <= 1.0:
            raise ValueError(f"bet_score_threshold は0〜1で指定してください: {value!r}")
    elif key == "bet_min_expected_value":
        if number < 0:
            raise ValueError(f"bet_min_expected_value は0以上で指定してください: {value!r}")
    else:
        raise ValueError(f"未対応の設定キーです: {key}")
    return number
