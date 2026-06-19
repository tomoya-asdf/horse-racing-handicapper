"""特徴量カタログ(pandas/lightgbm 非依存)のテスト。"""

from src.common.feature_catalog import (
    DEFAULT_ENABLED_FEATURES,
    FEATURE_COLUMNS,
    normalize_enabled_features,
    resolve_features,
)


def test_defaults_are_known_features():
    assert set(DEFAULT_ENABLED_FEATURES).issubset(set(FEATURE_COLUMNS))


def test_normalize_keeps_only_known_and_orders_by_columns():
    raw = ["age", "totally_unknown", "horse_number"]
    normalized = normalize_enabled_features(raw)
    assert "totally_unknown" not in normalized
    # FEATURE_COLUMNS の並び順を保つ
    assert normalized == [c for c in FEATURE_COLUMNS if c in set(normalized)]


def test_resolve_features_empty_falls_back_to_all():
    # 空指定(すべて無効)は安全のため全特徴量にフォールバックする
    selected, _ = resolve_features([])
    assert set(selected) == set(FEATURE_COLUMNS)


def test_resolve_features_subset():
    selected, categorical = resolve_features(["age", "horse_number"])
    assert set(selected) == {"age", "horse_number"}
    assert all(c in selected for c in categorical)
