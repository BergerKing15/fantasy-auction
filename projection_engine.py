"""
projection_engine.py
Loads historical stats, applies Bayesian shrinkage projections,
and outputs auction dollar values for each player.

Run: python projection_engine.py
Outputs: data/projections.csv
"""

import os
import pandas as pd
import numpy as np

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
    "bench": 6,   # midpoint of 5-8 bench spots
    "IR":    1,
    "min_bid": 1,
}

# Approximate fraction of FLEX slots filled by each position (rough historical average)
FLEX_SPLIT = {"RB": 0.40, "WR": 0.50, "TE": 0.10}

# How many players of each position teams typically roster including bench.
# Lowering replacement level to include bench depth is what keeps values realistic —
# only ~80 players have PAR if you use starter counts alone, concentrating all
# dollars at the top. Bench depth spreads the pool to ~150 players.
BENCH_DEPTH = {"QB": 1.5, "RB": 2.5, "WR": 2.5, "TE": 1.5}

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

    # Age brackets vectorized (no row-by-row apply)
    if "age" in df.columns:
        a = df["age"]
        df["age_bracket"] = np.select(
            [a.isna() | (a <= 23), a <= 27, a <= 30],
            ["rookie",              "young",  "prime"],
            default="veteran",
        )
    else:
        df["age_bracket"] = "unknown"

    priors = (
        df.groupby(["position", "age_bracket"])["ppg"]
        .mean()
        .to_dict()
    )
    return priors


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
    # Cap at most recent season with data (2025 fetched via PBP fallback in webscraping.py)
    max_available = 2025
    train_seasons = [y for y in range(projection_season - seasons_used, projection_season) if y <= max_available]
    prior_seasons = [y for y in range(projection_season - 8, projection_season) if y <= max_available]

    # Build age lookup once — reused for both prior computation and player history.
    # seasonal_stats.csv has no age column; it lives in players.csv.
    if "player_id" in players.columns and "birth_date" in players.columns:
        age_lookup = players[["player_id", "birth_date"]].copy()
        age_lookup["birth_date"] = pd.to_datetime(age_lookup["birth_date"], errors="coerce")
        age_lookup["age"] = (
            pd.Timestamp(f"{projection_season}-09-01") - age_lookup["birth_date"]
        ).dt.days / 365.25
    else:
        age_lookup = None

    stats_with_age = (
        stats.merge(age_lookup[["player_id", "age"]], on="player_id", how="left")
        if age_lookup is not None else stats
    )
    priors = compute_position_priors(stats_with_age, ppr=ppr, train_seasons=prior_seasons)

    train = stats[stats["season"].isin(train_seasons)].copy()
    train["fpts"] = compute_fantasy_points(train, ppr=ppr)
    train["games"] = train.get("games", pd.Series(17, index=train.index)).fillna(1).clip(lower=1)
    train["ppg"] = train["fpts"] / train["games"]

    # Aggregate over the training window — vectorized, no row-by-row apply
    agg_cols = {"total_fpts": ("fpts", "sum"), "total_games": ("games", "sum"),
                "seasons": ("season", "nunique"), "position": ("position", "first")}
    if "player_name" in train.columns:
        agg_cols["player_name"] = ("player_name", "first")

    player_history = train.groupby("player_id").agg(**agg_cols).reset_index()
    player_history["ppg"] = player_history["total_fpts"] / player_history["total_games"].clip(lower=1)

    if age_lookup is not None:
        player_history = player_history.merge(age_lookup[["player_id", "age"]], on="player_id", how="left")
    else:
        player_history["age"] = None

    # Bayesian shrinkage — fully vectorized, no row-by-row apply
    age_arr = player_history["age"].to_numpy(dtype=float, na_value=np.nan)
    age_bracket_arr = np.select(
        [np.isnan(age_arr) | (age_arr <= 23), age_arr <= 27, age_arr <= 30],
        ["rookie",                              "young",       "prime"],
        default="veteran",
    )

    # Build prior_ppg vector using the priors dict
    fallback_order = ["young", "prime", "rookie", "veteran", "unknown"]
    prior_ppg_arr = np.array([
        priors.get((pos, bracket),
            next((priors[pos, fb] for fb in fallback_order if (pos, fb) in priors), 0.0))
        for pos, bracket in zip(player_history["position"], age_bracket_arr)
    ])

    w = player_history["total_games"].to_numpy() / (
        player_history["total_games"].to_numpy() + shrinkage_k
    )
    age_adj = np.where(age_arr > 30, np.clip(1.0 - 0.03 * (age_arr - 30), 0.7, 1.0), 1.0)

    player_history["projected_fpts"] = (
        (w * player_history["ppg"].to_numpy() + (1 - w) * prior_ppg_arr) * age_adj * 17
    )

    keep_cols = [
        "player_id", "player_name", "position", "age",
        "total_games", "seasons", "ppg", "projected_fpts",
    ]
    return player_history[[c for c in keep_cols if c in player_history.columns]].copy()


# ─── Auction Values ───────────────────────────────────────────────────────────

def compute_replacement_levels(
    projections: pd.DataFrame,
    league: dict = LEAGUE,
) -> dict[str, float]:
    """
    Determine replacement-level projected points for each position.

    Replacement player = the last player expected to be rostered (starters + bench).
    Using starter counts alone puts only ~80 players above replacement, which
    concentrates all budget at the top and inflates values. Including bench depth
    expands the pool to ~150 players and produces realistic auction prices.
    """
    teams   = league["teams"]
    roster  = league["roster"]
    flex_n  = teams * roster.get("FLEX", 1)
    depth   = BENCH_DEPTH

    starters = {
        "QB":  round(teams * roster.get("QB", 1) * depth["QB"]),
        "RB":  round(teams * roster.get("RB", 2) * depth["RB"] + flex_n * FLEX_SPLIT["RB"]),
        "WR":  round(teams * roster.get("WR", 2) * depth["WR"] + flex_n * FLEX_SPLIT["WR"]),
        "TE":  round(teams * roster.get("TE", 1) * depth["TE"] + flex_n * FLEX_SPLIT["TE"]),
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
    bench  = league.get("bench", 6)
    ir     = league.get("IR", 1)

    # Hittable = total budget minus every roster spot's minimum bid.
    # Including bench and IR here because those slots do consume real budget.
    total_roster_slots = teams * (sum(roster.values()) + bench + ir)
    min_bid  = league["min_bid"]
    hittable = teams * budget - total_roster_slots * min_bid

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
        raise FileNotFoundError("Missing data/seasonal_stats.csv — run webscraping.py first.")

    print("Loading data...")
    stats   = pd.read_csv(stats_path, low_memory=False)
    players = pd.read_csv(players_path, low_memory=False) if os.path.exists(players_path) else pd.DataFrame()

    print(f"Projecting for {projection_season} (PPR={ppr})...")
    projections = project_players(stats, players, ppr=ppr, projection_season=projection_season)

    print("Computing auction values...")
    result = compute_auction_values(projections)

    try:
        out = os.path.join(DATA_DIR, "projections.csv")
        result.to_csv(out, index=False)
        print(f"Saved {len(result):,} players -> {out}")
    except OSError:
        pass  # read-only filesystem on Streamlit Cloud — return result without saving

    print(result[["player_name", "position", "projected_fpts", "auction_value"]].head(20).to_string(index=False))
    return result


if __name__ == "__main__":
    run(ppr=1.0, projection_season=2026)
