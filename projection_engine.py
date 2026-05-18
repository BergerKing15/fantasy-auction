"""
projection_engine.py
Loads historical stats, applies Bayesian shrinkage projections,
and outputs auction dollar values for each player.

Run: python projection_engine.py
Outputs: data/projections.csv
"""

import os
import sys
import pandas as pd
import numpy as np
from typing import Literal

# ─── Scoring Settings ─────────────────────────────────────────────────────────

# These match the user's league. PPR multiplier passed at call time.
SCORING = {
    "pass_yd":        0.05,   # 1 pt per 20 yds
    "pass_td":        6,
    "pass_int":      -2,
    "rush_yd":        0.1,    # 1 pt per 10 yds
    "rush_td":        6,
    "rec_yd":         0.1,
    "rec_td":         6,
    "fumble_lost":   -2,
}

# ─── League Settings ──────────────────────────────────────────────────────────

LEAGUE = {
    "teams":  12,
    "budget": 200,
    "roster": {
        "QB":   1,
        "RB":   2,
        "WR":   2,
        "TE":   1,
        "FLEX": 1,   # RB/WR/TE eligible
        "K":    1,
        "DST":  1,
    },
    "min_bid": 1,
}

# Approximate fraction of FLEX slots filled by each position (rough historical average)
FLEX_SPLIT = {"RB": 0.40, "WR": 0.50, "TE": 0.10}

# How many seasons of history to weight together for the empirical estimate
SEASONS_USED = 3

# Bayesian shrinkage constant: player needs this many game-seasons to be
# weighted equally against the position prior (16 ≈ one full season)
SHRINKAGE_K = 16.0

DATA_DIR = "data"


# ─── Fantasy Points ────────────────────────────────────────────────────────────

def compute_fantasy_points(df: pd.DataFrame, ppr: float = 1.0) -> pd.Series:
    """
    Compute fantasy points per row.
    ppr: 0 = standard, 0.5 = half PPR, 1.0 = full PPR
    """
    pts = pd.Series(0.0, index=df.index)

    # Passing
    pts += df.get("passing_yards", 0).fillna(0) * SCORING["pass_yd"]
    pts += df.get("passing_tds", 0).fillna(0)   * SCORING["pass_td"]
    pts += df.get("interceptions", 0).fillna(0)  * SCORING["pass_int"]

    # Rushing
    pts += df.get("rushing_yards", 0).fillna(0)  * SCORING["rush_yd"]
    pts += df.get("rushing_tds", 0).fillna(0)    * SCORING["rush_td"]

    # Receiving
    pts += df.get("receptions", 0).fillna(0)         * ppr
    pts += df.get("receiving_yards", 0).fillna(0)    * SCORING["rec_yd"]
    pts += df.get("receiving_tds", 0).fillna(0)      * SCORING["rec_td"]

    # Fumbles lost
    pts += df.get("sack_fumbles_lost", 0).fillna(0)      * SCORING["fumble_lost"]
    pts += df.get("rushing_fumbles_lost", 0).fillna(0)   * SCORING["fumble_lost"]
    pts += df.get("receiving_fumbles_lost", 0).fillna(0) * SCORING["fumble_lost"]

    return pts


# ─── Priors ───────────────────────────────────────────────────────────────────

def compute_position_priors(
    stats: pd.DataFrame,
    ppr: float = 1.0,
    train_seasons: list[int] | None = None,
) -> dict:
    """
    Compute average per-game fantasy points stratified by position × age bracket.
    Used as the Bayesian prior for shrinkage.

    Returns: {(position, age_bracket): avg_ppg}
    """
    df = stats.copy()
    if train_seasons is not None:
        df = df[df["season"].isin(train_seasons)]

    df["fpts"] = compute_fantasy_points(df, ppr=ppr)
    df["games"] = df.get("games", pd.Series(17, index=df.index)).fillna(1).clip(lower=1)
    df["ppg"] = df["fpts"] / df["games"]

    # Age brackets: rookie (<=23), young (24-27), prime (28-30), veteran (31+)
    def age_bracket(age):
        if pd.isna(age) or age <= 23:
            return "rookie"
        elif age <= 27:
            return "young"
        elif age <= 30:
            return "prime"
        else:
            return "veteran"

    if "age" in df.columns:
        df["age_bracket"] = df["age"].apply(age_bracket)
    else:
        df["age_bracket"] = "unknown"

    priors = (
        df.groupby(["position", "age_bracket"])["ppg"]
        .mean()
        .to_dict()
    )
    return priors


def lookup_prior(position: str, age: float | None, priors: dict) -> float:
    """Return prior ppg for a player given their position and age."""
    def bracket(a):
        if a is None or np.isnan(a):
            return "rookie"
        elif a <= 23:
            return "rookie"
        elif a <= 27:
            return "young"
        elif a <= 30:
            return "prime"
        else:
            return "veteran"

    key = (position, bracket(age))
    if key in priors:
        return priors[key]
    # Fallback: any bracket for this position
    for br in ["young", "prime", "rookie", "veteran", "unknown"]:
        fallback = (position, br)
        if fallback in priors:
            return priors[fallback]
    return 0.0


# ─── Projections ──────────────────────────────────────────────────────────────

def project_players(
    stats: pd.DataFrame,
    players: pd.DataFrame,
    ppr: float = 1.0,
    projection_season: int = 2026,
    seasons_used: int = SEASONS_USED,
    shrinkage_k: float = SHRINKAGE_K,
) -> pd.DataFrame:
    """
    For each player, produce a projected-season fantasy points estimate.

    Uses SEASONS_USED most recent seasons (before projection_season) as
    the empirical estimate, Bayesian-shrunk toward position × age priors.
    """
    # Cap at 2024 — nflverse hasn't published 2025 data yet
    max_available = 2024
    train_seasons = [y for y in range(projection_season - seasons_used, projection_season) if y <= max_available]
    prior_seasons = [y for y in range(projection_season - 8, projection_season) if y <= max_available]

    priors = compute_position_priors(stats, ppr=ppr, train_seasons=prior_seasons)

    train = stats[stats["season"].isin(train_seasons)].copy()
    train["fpts"] = compute_fantasy_points(train, ppr=ppr)
    train["games"] = train.get("games", pd.Series(17, index=train.index)).fillna(1).clip(lower=1)
    train["ppg"] = train["fpts"] / train["games"]

    # Aggregate over the training window: weighted by games played
    player_history = (
        train.groupby("player_id")
        .apply(
            lambda g: pd.Series({
                "total_fpts":  g["fpts"].sum(),
                "total_games": g["games"].sum(),
                "seasons":     g["season"].nunique(),
                "last_season_fpts": g.loc[g["season"].idxmax(), "fpts"] if len(g) > 0 else 0,
                "ppg":         g["fpts"].sum() / max(g["games"].sum(), 1),
                "position":    g["position"].iloc[0],
                "player_name": g["player_name"].iloc[0] if "player_name" in g.columns else "",
            }),
            include_groups=False,
        )
        .reset_index()
    )

    # Merge player metadata for age
    if "player_id" in players.columns and "birth_date" in players.columns:
        players_clean = players[["player_id", "birth_date", "display_name"]].copy()
        players_clean["birth_date"] = pd.to_datetime(players_clean["birth_date"], errors="coerce")
        players_clean["age"] = (
            pd.Timestamp(f"{projection_season}-09-01") - players_clean["birth_date"]
        ).dt.days / 365.25
        player_history = player_history.merge(
            players_clean[["player_id", "age", "display_name"]], on="player_id", how="left"
        )
    else:
        player_history["age"] = None

    # Bayesian shrinkage: posterior = w * empirical_ppg + (1-w) * prior_ppg
    # w = total_games / (total_games + K)
    def shrink(row):
        prior_ppg = lookup_prior(row["position"], row.get("age"), priors)
        w = row["total_games"] / (row["total_games"] + shrinkage_k)

        # Apply age regression: players 30+ lose ~3% per year above 30
        age = row.get("age")
        age_adj = 1.0
        if age is not None and not np.isnan(age) and age > 30:
            age_adj = max(0.7, 1.0 - 0.03 * (age - 30))

        posterior_ppg = (w * row["ppg"] + (1 - w) * prior_ppg) * age_adj
        return posterior_ppg * 17   # project over a 17-game season

    player_history["projected_fpts"] = player_history.apply(shrink, axis=1)

    # Carry useful columns
    keep_cols = [
        "player_id", "player_name", "position", "age",
        "total_games", "seasons", "ppg", "projected_fpts",
    ]
    if "display_name" in player_history.columns:
        keep_cols.append("display_name")

    return player_history[[c for c in keep_cols if c in player_history.columns]].copy()


# ─── Auction Values ───────────────────────────────────────────────────────────

def compute_replacement_levels(
    projections: pd.DataFrame,
    league: dict = LEAGUE,
) -> dict[str, float]:
    """
    Determine replacement-level projected points for each position.
    Replacement player = the last starter expected to be rostered.
    """
    teams   = league["teams"]
    roster  = league["roster"]
    flex_n  = teams * roster.get("FLEX", 1)

    starters = {
        "QB":  teams * roster.get("QB", 1),
        "RB":  teams * roster.get("RB", 2) + round(flex_n * FLEX_SPLIT["RB"]),
        "WR":  teams * roster.get("WR", 2) + round(flex_n * FLEX_SPLIT["WR"]),
        "TE":  teams * roster.get("TE", 1) + round(flex_n * FLEX_SPLIT["TE"]),
    }

    levels = {}
    for pos, n in starters.items():
        pos_pts = (
            projections.loc[projections["position"] == pos, "projected_fpts"]
            .sort_values(ascending=False)
            .reset_index(drop=True)
        )
        idx = min(n - 1, len(pos_pts) - 1)
        levels[pos] = float(pos_pts.iloc[idx]) if len(pos_pts) > 0 else 0.0

    return levels


def compute_auction_values(
    projections: pd.DataFrame,
    league: dict = LEAGUE,
) -> pd.DataFrame:
    """
    Convert projected points to auction dollar values using the PAR method.
    Adds 'replacement_pts', 'par', and 'auction_value' columns.
    """
    teams  = league["teams"]
    budget = league["budget"]
    roster = league["roster"]

    total_starter_slots = teams * sum(roster.values())
    min_bid = league["min_bid"]
    hittable = teams * budget - total_starter_slots * min_bid

    repl_levels = compute_replacement_levels(projections, league)

    df = projections.copy()
    df["replacement_pts"] = df["position"].map(repl_levels).fillna(0)
    df["par"] = (df["projected_fpts"] - df["replacement_pts"]).clip(lower=0)

    total_par = df["par"].sum()
    if total_par > 0:
        df["auction_value"] = (min_bid + (df["par"] / total_par) * hittable).round(1)
    else:
        df["auction_value"] = float(min_bid)

    return df.sort_values("auction_value", ascending=False).reset_index(drop=True)


# ─── Main ─────────────────────────────────────────────────────────────────────

def run(ppr: float = 1.0, projection_season: int = 2026) -> pd.DataFrame:
    """Full pipeline: load data → project → compute auction values → save CSV."""
    stats_path   = os.path.join(DATA_DIR, "seasonal_stats.csv")
    players_path = os.path.join(DATA_DIR, "players.csv")

    if not os.path.exists(stats_path):
        sys.exit("Missing data/seasonal_stats.csv. Run webscraping.py first.")

    print("Loading data...")
    stats   = pd.read_csv(stats_path, low_memory=False)
    players = pd.read_csv(players_path, low_memory=False) if os.path.exists(players_path) else pd.DataFrame()

    print(f"Projecting for {projection_season} (PPR={ppr})...")
    projections = project_players(stats, players, ppr=ppr, projection_season=projection_season)

    print("Computing auction values...")
    result = compute_auction_values(projections)

    out = os.path.join(DATA_DIR, "projections.csv")
    result.to_csv(out, index=False)
    print(f"Saved {len(result):,} players → {out}")
    print(result[["player_name", "position", "projected_fpts", "auction_value"]].head(20).to_string(index=False))
    return result


if __name__ == "__main__":
    run(ppr=1.0, projection_season=2026)
