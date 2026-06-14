"""回収率ベースのバックテスト。

指定期間 [start, end] の確定済みレースに対して、
1. start より前のレースだけで学習したモデルを使い(未来を見ないため)、
2. 各レースを予測 → 現在の賭け戦略(decide_bets)で賭け対象を決定 → 実際の着順と
   最終オッズで決済し、
3. 的中率・回収率・購入レース数を集計する。

AUCが高くても回収率が低い(控除率に負ける)ことはよくあるため、賭け閾値
(score_threshold / min_expected_value)を実際の回収率で調整するための土台。

単勝の払戻は「最終オッズ × 賭け金」で計算する(entries.odds はバックフィルで
最終オッズが入っている前提)。賭け判断にも同じ最終オッズを使うため、購入時と
決済時のオッズが一致した理想条件での評価になる点に注意。

実行方法(CLI):
    docker compose run --rm predictor python -m src.predictor.backtest 20250101 20251231
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, replace
from datetime import date, datetime

from src.collector import scraper
from src.common.db import get_session, init_db
from src.common.dynamic_config import BettingConfig, load_betting_config
from src.common.models import Entry, Race
from src.predictor import model as model_module
from src.predictor.betting import decide_bets
from src.predictor.features import build_features
from src.predictor.history import (
    build_entries_frame,
    load_horse_history,
    load_jockey_history,
    load_sire_map,
    load_trainer_history,
)
from src.predictor.train import _load_training_frames, build_model_bundle

logger = logging.getLogger(__name__)

# 期待値下限(min_expected_value)のスイープ候補。回収率の閾値依存を見るため
EV_SWEEP = (1.0, 1.1, 1.2, 1.3, 1.5)


@dataclass
class _Pred:
    """decide_bets に渡す予測の最小表現(Prediction ORM の代わり)。"""

    entry_id: int
    score: float


def _winning_quinella(race: Race) -> str | None:
    """レースの1-2着の馬連買い目を返す(着順が揃わなければNone)。"""
    first = second = None
    for entry in race.entries:
        if entry.finish_position == 1:
            first = entry.horse_number
        elif entry.finish_position == 2:
            second = entry.horse_number
    if first is None or second is None:
        return None
    return scraper.normalize_combination([first, second])


def _bet_won(bet, race: Race, win_combo: str | None) -> bool:
    """その賭けが的中したか(単勝=1着、馬連=1-2着の組み合わせ一致)。"""
    positions = {
        e.horse_number: e.finish_position
        for e in race.entries
        if e.finish_position is not None
    }
    if bet.bet_type == "馬連":
        return bet.combination is not None and bet.combination == win_combo
    if bet.bet_type == "ワイド":
        if bet.combination is None:
            return False
        numbers = [int(n) for n in bet.combination.split("-")]
        return all(positions.get(n, 99) <= 3 for n in numbers)
    entry = next((e for e in race.entries if e.id == bet.entry_id), None)
    if entry is None:
        return False
    if bet.bet_type == "複勝":
        return entry.finish_position is not None and entry.finish_position <= 3
    return entry.finish_position == 1


def _evaluate(
    race_preds: list[tuple[Race, list[_Pred], dict]], config: BettingConfig
) -> dict:
    """同じ予測スコアに対し、ある賭け設定での的中率・回収率を集計する。"""
    invested = 0.0
    payout = 0.0
    bets = 0
    hits = 0
    by_type: dict[str, dict] = {}
    for race, preds, odds_by_type in race_preds:
        win_combo = _winning_quinella(race)
        for bet in decide_bets(race, preds, config, odds_by_type=odds_by_type):
            bets += 1
            invested += bet.amount
            type_stats = by_type.setdefault(
                bet.bet_type,
                {"bets": 0, "hits": 0, "invested": 0.0, "payout": 0.0},
            )
            type_stats["bets"] += 1
            type_stats["invested"] += bet.amount
            if _bet_won(bet, race, win_combo):
                # 払戻 = 賭け金 × 確定オッズ(単勝は単勝オッズ、馬連は馬連オッズ)
                bet_payout = bet.amount * (bet.odds_at_bet or 0.0)
                payout += bet_payout
                hits += 1
                type_stats["hits"] += 1
                type_stats["payout"] += bet_payout
    for stats in by_type.values():
        stats["hit_rate"] = (stats["hits"] / stats["bets"] * 100) if stats["bets"] else None
        stats["recovery_rate"] = (
            stats["payout"] / stats["invested"] * 100
        ) if stats["invested"] else None
    return {
        "bets": bets,
        "hits": hits,
        "invested": invested,
        "payout": payout,
        "hit_rate": (hits / bets * 100) if bets else None,
        "recovery_rate": (payout / invested * 100) if invested else None,
        "by_type": by_type,
    }


def run_backtest(start: date, end: date, config: BettingConfig | None = None) -> dict:
    """期間 [start, end] のバックテストを実行し、集計結果のdictを返す。"""
    config = config or load_betting_config()
    session = get_session()
    try:
        train_frames, train_races = _load_training_frames(before=start)
        if train_races == 0:
            return {"error": f"{start} より前の学習データがありません"}

        bundle, metrics = build_model_bundle(train_frames)
        if bundle is None:
            return {"error": "学習データに1着の記録が無く、モデルを作成できませんでした"}

        history = load_horse_history(session)
        sire_map = load_sire_map(session)
        jockey_history = load_jockey_history(session)
        trainer_history = load_trainer_history(session)
        races = (
            session.query(Race)
            .filter(
                Race.race_date >= start,
                Race.race_date <= end,
                Race.entries.any(Entry.finish_position.isnot(None)),
            )
            .order_by(Race.race_date, Race.id)
            .all()
        )

        race_preds: list[tuple[Race, list[_Pred], dict]] = []
        for race in races:
            entries = [e for e in race.entries if e.finish_position is not None]
            if len(entries) < 2:
                continue
            scores = model_module.predict(
                bundle,
                build_features(
                    build_entries_frame(
                        entries,
                        race,
                        history,
                        sire_map,
                        jockey_history,
                        trainer_history,
                    )
                ),
            )
            preds = [_Pred(entry_id=int(eid), score=float(score)) for eid, score in scores.items()]
            # 評価用に確定済みレースの最終オッズを取得する
            odds_by_type = scraper.fetch_supported_odds(race.race_key)
            race_preds.append((race, preds, odds_by_type))

        result = _evaluate(race_preds, config)
        sweep = [
            {"min_expected_value": ev, **_evaluate(race_preds, replace(config, min_expected_value=ev))}
            for ev in EV_SWEEP
        ]

        return {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "train_races": train_races,
            "test_races": len(race_preds),
            "model": {"version": bundle["version"], "auc": metrics["auc"], "logloss": metrics["logloss"]},
            "config": {
                "score_threshold": config.score_threshold,
                "min_expected_value": config.min_expected_value,
                "amount": config.amount,
            },
            "result": result,
            "sweep": sweep,
        }
    finally:
        session.close()


def format_summary(report: dict) -> str:
    """run_backtest の結果を、ジョブ履歴の detail 欄向けの文字列に整形する。"""
    if "error" in report:
        return report["error"]

    r = report["result"]
    lines = [
        f"バックテスト {report['start']}〜{report['end']}: "
        f"学習{report['train_races']}件 / 検証{report['test_races']}レース",
    ]
    auc = report["model"]["auc"]
    if auc is not None:
        lines.append(f"検証AUC={auc:.4f}")
    cfg = report["config"]
    lines.append(
        f"現設定(score>={cfg['score_threshold']}, 期待値>={cfg['min_expected_value']}): "
        f"購入{r['bets']}件 的中{r['hits']}件 "
        f"的中率{_pct(r['hit_rate'])} 回収率{_pct(r['recovery_rate'])} "
        f"(投資{r['invested']:.0f}円→払戻{r['payout']:.0f}円)"
    )
    lines.append("期待値下限スイープ:")
    for s in report["sweep"]:
        lines.append(
            f"  期待値>={s['min_expected_value']}: 購入{s['bets']}件 "
            f"的中率{_pct(s['hit_rate'])} 回収率{_pct(s['recovery_rate'])}"
        )
    if r.get("by_type"):
        lines.append("券種別:")
        for bet_type, stats in sorted(r["by_type"].items()):
            lines.append(
                f"  {bet_type}: 購入{stats['bets']}件 "
                f"的中率{_pct(stats['hit_rate'])} 回収率{_pct(stats['recovery_rate'])}"
            )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return f"{value:.1f}%" if value is not None else "-"


def main() -> None:
    if len(sys.argv) != 3:
        print("usage: python -m src.predictor.backtest <開始日YYYYMMDD> <終了日YYYYMMDD>")
        sys.exit(1)
    try:
        start = datetime.strptime(sys.argv[1], "%Y%m%d").date()
        end = datetime.strptime(sys.argv[2], "%Y%m%d").date()
    except ValueError:
        print("日付はYYYYMMDD形式で指定してください")
        sys.exit(1)
    if start > end:
        print("開始日は終了日以前を指定してください")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)
    init_db()
    logger.info("\n%s", format_summary(run_backtest(start, end)))


if __name__ == "__main__":
    main()
