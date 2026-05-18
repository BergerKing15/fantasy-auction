"""
backtest.py
Evaluates projection_engine accuracy by holding out each season from
2017 onward, projecting using only prior-season data, and comparing
projected vs actual fantasy points.

Run: python backtest.py
Prints RMSE and MAE per season and overall.
Outputs: data/backtest_results.csv
"""

import os
import sys
import pandas as pd
import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error

from projection_engine import (
    project_players,
    compute_fantasy_points,
    DATA_DIR,
)

# ─── Config ───────────────────────────────────────────────────────────────────

# Hold-out seasons to evaluate (need enough prior history)
EVAL_SEASONS = list(range(2017, 2026))
PPR = 1.0
MIN_ACTUAL_GAMES = 6   # skip players who barely played (injury/cut)


# ─── Backtest Loop ────────────────────────────────────────────────────────────

def backtest(stats: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    rows = []

    for season in EVAL_SEASONS:
        print(f"  Testing {season}...", end=" ")

        # Project using data up to season-1
        projections = project_players(
            stats, players,
            ppr=PPR,
            projection_season=season,
        )

        # Actual performance for this season
        actual = stats[stats["season"] == season].copy()
        actual["actual_fpts"] = compute_fantasy_points(actual, ppr=PPR)
        actual["games"] = actual.get("games", pd.Series(17, index=actual.index)).fillna(1).clip(lower=1)
        actual = actual[actual["games"] >= MIN_ACTUAL_GAMES]
        actual = actual[["player_id", "actual_fpts", "games"]].copy()

        # Merge
        merged = projections.merge(actual, on="player_id", how="inner")
        if len(merged) < 10:
            print(f"too few players ({len(merged)}), skipping.")
            continue

        mae  = mean_absolute_error(merged["actual_fpts"], merged["projected_fpts"])
        rmse = np.sqrt(mean_squared_error(merged["actual_fpts"], merged["projected_fpts"]))

        print(f"n={len(merged):3d}  MAE={mae:.1f}  RMSE={rmse:.1f}")

        merged["season"] = season
        rows.append(merged)

    return pd.concat(rows, ignore_index=True)


def summary(results: pd.DataFrame):
    """Print per-position accuracy breakdown."""
    print("\n─── Per-Position Accuracy (all seasons) ──────────────────────────────")
    for pos in ["QB", "RB", "WR", "TE"]:
        sub = results[results["position"] == pos]
        if sub.empty:
            continue
        mae  = mean_absolute_error(sub["actual_fpts"], sub["projected_fpts"])
        rmse = np.sqrt(mean_squared_error(sub["actual_fpts"], sub["projected_fpts"]))
        print(f"  {pos:3s}  n={len(sub):4d}  MAE={mae:.1f}  RMSE={rmse:.1f}")

    print("\n─── Overall ────────────────────────────────────────────────────────────")
    mae  = mean_absolute_error(results["actual_fpts"], results["projected_fpts"])
    rmse = np.sqrt(mean_squared_error(results["actual_fpts"], results["projected_fpts"]))
    print(f"  n={len(results):5d}  MAE={mae:.1f}  RMSE={rmse:.1f}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    stats_path   = os.path.join(DATA_DIR, "seasonal_stats.csv")
    players_path = os.path.join(DATA_DIR, "players.csv")

    if not os.path.exists(stats_path):
        sys.exit("Missing data/seasonal_stats.csv. Run webscraping.py first.")

    print("Loading data...")
    stats   = pd.read_csv(stats_path, low_memory=False)
    players = pd.read_csv(players_path, low_memory=False) if os.path.exists(players_path) else pd.DataFrame()

    print(f"Backtesting seasons {EVAL_SEASONS[0]}-{EVAL_SEASONS[-1]} (PPR={PPR})...")
    results = backtest(stats, players)

    if results.empty:
        print("No results to report.")
        return

    summary(results)

    out = os.path.join(DATA_DIR, "backtest_results.csv")
    results.to_csv(out, index=False)
    print(f"\nDetailed results saved → {out}")


if __name__ == "__main__":
    main()
