"""特徴量の「名前・ラベル・グループ」を一元管理する純Pythonモジュール。

ここには pandas / numpy / lightgbm を一切持ち込まない。理由は、APIイメージ
([requirements-api.txt](docker/requirements-api.txt))がML系ライブラリを含まないため。
特徴量の定義(列名一覧)を pandas に依存する [features.py](src/predictor/features.py) や
[history.py](src/predictor/history.py) に置くと、それを参照する設定モジュール
([dynamic_config.py](src/common/dynamic_config.py))経由でAPIの起動が落ちる。

そのため「特徴量の集合」と「UI向けメタ情報」だけをここに切り出し、
predictor側(features/history)とAPI側(dynamic_config)の双方から参照する。
"""

from __future__ import annotations

# 基礎特徴量(レース当日の出馬表から分かるもの。オッズ・人気は使わない)
# age=馬齢, horse_weight=馬体重, horse_weight_diff=前走比増減, season_sin/cos=季節(周期)
BASE_NUMERIC_FEATURES = [
    "horse_number",
    "age",
    "weight",
    "horse_weight",
    "horse_weight_diff",
    "field_size",
    "distance",
    "season_sin",
    "season_cos",
]

# 枠順・相対値(レース内の出走馬から算出する相対特徴量)
# draw_ratio=馬番/頭数(内外の相対位置), weight_rel/horse_weight_rel=斤量/馬体重のレース平均差
RELATIVE_FEATURES = [
    "draw_ratio",
    "weight_rel",
    "horse_weight_rel",
]

# レース条件の数値特徴量(レース単位で同じ値)
RACE_NUMERIC_FEATURES = [
    "race_number",
]

# レース条件のカテゴリ特徴量(当日条件。track_type以外は当日まで確定しないことがある)
RACE_CATEGORICAL_FEATURES = [
    "track_type",
    "going",
    "weather",
    "direction",
    "race_class",
    "venue",
]

# カテゴリ特徴量: 性別・騎手ID・調教師ID(いずれも同姓同名対策にID)・父ID(血統=距離/芝ダ適性の遺伝)
# + レース条件カテゴリ。生ID(jockey_id/trainer_id/sire_id)は高カードナリティで過学習しやすい。
CATEGORICAL_FEATURES = ["sex", "jockey_id", "trainer_id", "sire_id"] + RACE_CATEGORICAL_FEATURES

# 馬の過去成績(horse_results)ベースの履歴特徴量(全て数値、未該当はNaN=欠損としてLightGBMに渡す)
HISTORY_FEATURES = [
    "career_starts",  # 出走回数(完走数)
    "win_rate",  # 勝率
    "place_rate",  # 複勝率(3着内率)
    "avg_finish_recent3",  # 直近3走の平均着順
    "avg_finish_recent5",  # 直近5走の平均着順
    "best_last3f_recent5",  # 直近5走の上がり3F最速
    "avg_last3f_recent5",  # 直近5走の上がり3F平均
    "days_since_last",  # 前走からの間隔(日)
    "distance_change",  # 今回距離 - 前走距離
    "same_dist_starts",  # 同距離帯の出走数
    "same_dist_avg_finish",  # 同距離帯の平均着順
    "same_surface_starts",  # 同馬場種別(芝/ダ)の出走数
    "same_surface_avg_finish",  # 同馬場種別の平均着順
    "win_rate_recent10",  # 直近10走の勝率
    "place_rate_recent10",  # 直近10走の複勝率
    "same_going_starts",  # 同馬場状態(良/重等)の出走数
    "same_going_place_rate",  # 同馬場状態の複勝率
]

JOCKEY_HISTORY_FEATURES = [
    "jockey_starts",
    "jockey_win_rate",
    "jockey_place_rate",
    "jockey_avg_finish_recent10",
    "jockey_same_dist_starts",
    "jockey_same_dist_win_rate",
    "jockey_same_surface_starts",
    "jockey_same_surface_win_rate",
    "jockey_same_going_starts",
    "jockey_same_going_win_rate",
]

TRAINER_HISTORY_FEATURES = [
    "trainer_starts",
    "trainer_win_rate",
    "trainer_place_rate",
    "trainer_avg_finish_recent10",
    "trainer_same_dist_starts",
    "trainer_same_dist_win_rate",
    "trainer_same_surface_starts",
    "trainer_same_surface_win_rate",
    "trainer_same_going_starts",
    "trainer_same_going_win_rate",
]

# モデルに渡す全特徴量。並び順は固定(build_features が返す列順と一致させる)。
FEATURE_COLUMNS = (
    BASE_NUMERIC_FEATURES
    + RELATIVE_FEATURES
    + RACE_NUMERIC_FEATURES
    + CATEGORICAL_FEATURES
    + HISTORY_FEATURES
    + JOCKEY_HISTORY_FEATURES
    + TRAINER_HISTORY_FEATURES
)

# WebUIで特徴量のON/OFFを選ばせるための日本語ラベル。`build_features` が作る列名と一致させる。
FEATURE_LABELS: dict[str, str] = {
    # 基礎
    "horse_number": "馬番",
    "age": "馬齢",
    "weight": "斤量",
    "horse_weight": "馬体重",
    "horse_weight_diff": "馬体重増減",
    "field_size": "頭数",
    "distance": "距離",
    "season_sin": "季節(sin)",
    "season_cos": "季節(cos)",
    # 枠順・相対値
    "draw_ratio": "枠位置(馬番/頭数)",
    "weight_rel": "斤量(レース平均差)",
    "horse_weight_rel": "馬体重(レース平均差)",
    # レース条件
    "race_number": "レース番号",
    "track_type": "コース(芝/ダート)",
    "going": "馬場状態",
    "weather": "天候",
    "direction": "回り(右/左)",
    "race_class": "クラス・格",
    "venue": "競馬場",
    # カテゴリ(ID等)
    "sex": "性別",
    "jockey_id": "騎手ID",
    "trainer_id": "調教師ID",
    "sire_id": "父ID(血統)",
    # 馬の履歴
    "career_starts": "出走回数",
    "win_rate": "勝率",
    "place_rate": "複勝率(3着内率)",
    "avg_finish_recent3": "直近3走平均着順",
    "avg_finish_recent5": "直近5走平均着順",
    "best_last3f_recent5": "直近5走上がり3F最速",
    "avg_last3f_recent5": "直近5走上がり3F平均",
    "days_since_last": "前走からの間隔(日)",
    "distance_change": "前走比の距離変化",
    "same_dist_starts": "同距離帯の出走数",
    "same_dist_avg_finish": "同距離帯の平均着順",
    "same_surface_starts": "同馬場種別の出走数",
    "same_surface_avg_finish": "同馬場種別の平均着順",
    "win_rate_recent10": "直近10走勝率",
    "place_rate_recent10": "直近10走複勝率",
    "same_going_starts": "同馬場状態の出走数",
    "same_going_place_rate": "同馬場状態の複勝率",
    # 騎手の履歴
    "jockey_starts": "騎手 出走数",
    "jockey_win_rate": "騎手 勝率",
    "jockey_place_rate": "騎手 複勝率",
    "jockey_avg_finish_recent10": "騎手 直近10走平均着順",
    "jockey_same_dist_starts": "騎手 同距離帯出走数",
    "jockey_same_dist_win_rate": "騎手 同距離帯勝率",
    "jockey_same_surface_starts": "騎手 同馬場種別出走数",
    "jockey_same_surface_win_rate": "騎手 同馬場種別勝率",
    "jockey_same_going_starts": "騎手 同馬場状態出走数",
    "jockey_same_going_win_rate": "騎手 同馬場状態勝率",
    # 調教師の履歴
    "trainer_starts": "調教師 出走数",
    "trainer_win_rate": "調教師 勝率",
    "trainer_place_rate": "調教師 複勝率",
    "trainer_avg_finish_recent10": "調教師 直近10走平均着順",
    "trainer_same_dist_starts": "調教師 同距離帯出走数",
    "trainer_same_dist_win_rate": "調教師 同距離帯勝率",
    "trainer_same_surface_starts": "調教師 同馬場種別出走数",
    "trainer_same_surface_win_rate": "調教師 同馬場種別勝率",
    "trainer_same_going_starts": "調教師 同馬場状態出走数",
    "trainer_same_going_win_rate": "調教師 同馬場状態勝率",
}

# 設定画面でグループ見出し付きに表示するためのカタログ(列の並びは FEATURE_COLUMNS と一致)
FEATURE_GROUPS: list[tuple[str, list[str]]] = [
    ("基礎(出馬表)", BASE_NUMERIC_FEATURES),
    ("枠順・相対値", RELATIVE_FEATURES),
    ("レース条件", RACE_NUMERIC_FEATURES + RACE_CATEGORICAL_FEATURES),
    ("カテゴリ(ID等)", ["sex", "jockey_id", "trainer_id", "sire_id"]),
    ("馬の過去成績", HISTORY_FEATURES),
    ("騎手の過去成績", JOCKEY_HISTORY_FEATURES),
    ("調教師の過去成績", TRAINER_HISTORY_FEATURES),
]

# 既定でOFFにする特徴量(選択肢としては提示するが、初期状態では学習に使わない)。
#  - 生ID: 高カードナリティで memorization の温床(率特徴量へ寄せる)
#  - 弱い/冗長: 単体で弱い、または既存の率特徴量と重複
#  - 弱いレース条件 / 今回追加した拡張特徴量(同馬場状態・直近10走)はオプション扱い
DEFAULT_OFF_FEATURES = {
    # 生ID(memorization源)
    "jockey_id",
    "trainer_id",
    "sire_id",
    # 弱い/冗長
    "horse_number",  # draw_ratio を採用
    "season_sin",
    "season_cos",
    "horse_weight_rel",
    # 弱いレース条件
    "weather",
    "direction",
    "venue",
    "race_number",
    # 今回追加した馬履歴拡張
    "win_rate_recent10",
    "place_rate_recent10",
    "same_going_starts",
    "same_going_place_rate",
    # 今回追加した騎手/調教師拡張
    "jockey_same_going_starts",
    "jockey_same_going_win_rate",
    "trainer_same_going_starts",
    "trainer_same_going_win_rate",
}

# 既定でONにする最適セット(汎化しやすく欠損の少ない素性)。未設定時の初期値に使う。
DEFAULT_ENABLED_FEATURES = [name for name in FEATURE_COLUMNS if name not in DEFAULT_OFF_FEATURES]


def feature_catalog(
    enabled: list[str], missing_rates: dict[str, float] | None = None
) -> list[dict]:
    """設定画面向けに、グループ単位の特徴量メタ情報(ラベル・有効フラグ・カテゴリ判定・欠損率)を返す。

    ``missing_rates`` は特徴量名→欠損率(0〜1)のマップ(最新学習時点の値)。無ければ None を返す。
    """
    enabled_set = set(enabled)
    categorical_set = set(CATEGORICAL_FEATURES)
    rates = missing_rates or {}
    groups: list[dict] = []
    for group_label, names in FEATURE_GROUPS:
        groups.append(
            {
                "group": group_label,
                "features": [
                    {
                        "name": name,
                        "label": FEATURE_LABELS.get(name, name),
                        "enabled": name in enabled_set,
                        "categorical": name in categorical_set,
                        "missing_rate": rates.get(name),
                    }
                    for name in names
                ],
            }
        )
    return groups


def normalize_enabled_features(enabled: list[str] | None) -> list[str]:
    """既知の特徴量名のみを FEATURE_COLUMNS の並び順で正規化して返す。"""
    enabled_set = set(enabled or [])
    return [name for name in FEATURE_COLUMNS if name in enabled_set]


def resolve_features(enabled: list[str] | None) -> tuple[list[str], list[str]]:
    """有効特徴量リストから (学習に使う列, うちカテゴリ列) を返す。

    並び順は ``FEATURE_COLUMNS`` を保つ。``enabled`` が None/空、もしくは一つも
    既知の特徴量に該当しない場合は全特徴量にフォールバックする(モデルが
    特徴量ゼロで学習不能になる事故を防ぐ)。
    """
    selected = normalize_enabled_features(enabled)
    if not selected:
        selected = list(FEATURE_COLUMNS)
    categorical = [name for name in selected if name in CATEGORICAL_FEATURES]
    return selected, categorical
