"""
projection_engine.py
Loads historical stats, applies Bayesian shrinkage projections,
and outputs auction dollar values for each player.

Run: python projection_engine.py
Outputs: data/projections.csv
"""

import os
import re
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
BENCH_DEPTH = {"QB": 1.5, "RB": 3.5, "WR": 3.5, "TE": 2.0}

# How many seasons of history to weight together for the empirical estimate
SEASONS_USED = 3

# Recency weights: most recent season (lag=0) counts fully; older seasons are discounted.
# This prevents a 4-game injury year from being averaged equally with a healthy full season.
RECENCY_WEIGHTS = {0: 1.0, 1: 0.7, 2: 0.4}

# Bayesian shrinkage constant: player needs this many game-seasons to be
# weighted equally against the position prior. Lower = trust recent stats more.
# K=8: a player with 3 full seasons (51 games) gets 86% empirical weight.
SHRINKAGE_K = 8.0

# How much ADP-implied value blends into the final auction value (0=pure PAR, 1=pure ADP).
# ADP implicitly captures team context, target share, schedule, and injury risk that
# our stat model can't see. 0.5 gives equal weight to both signals.
ADP_BLEND = 0.5

DATA_DIR = "data"


# ─── ADP Blend ────────────────────────────────────────────────────────────────

def _adp_to_value(rank: float, budget: int = 200, n_slots: int = 192) -> float:
    """Convert ADP rank to implied auction value via log-linear decay.
    Calibrated so rank 1 ≈ 25% of budget, rank n_slots ≈ $1.
    """
    top = budget * 0.25          # top pick worth ~25% of budget
    b   = (top - 1.0) / np.log(max(n_slots, 2))
    return max(1.0, top - b * np.log(max(rank, 1)))


def _clean_adp_name(raw: str) -> str:
    """Strip team abbreviation, bye week, and name suffixes from FantasyPros player string."""
    name = re.sub(r"[A-Z]{2,3}\(.*?\)\s*$", "", raw).strip()
    name = re.sub(r"\s+(Jr\.?|Sr\.?|I{2,3}V?|IV)$", "", name, flags=re.IGNORECASE).strip()
    return name.lower()


def blend_adp(result: pd.DataFrame, adp_path: str,
              blend: float = ADP_BLEND, league: dict = LEAGUE) -> pd.DataFrame:
    """
    Blend PAR-based auction values with ADP-implied values.
    Players not found in ADP (depth/bench players) keep their PAR value.
    After blending, rescales so top-N values still sum to the total budget.
    """
    if blend == 0 or not os.path.exists(adp_path):
        return result

    adp_df = pd.read_csv(adp_path)
    ppr    = adp_df[adp_df["scoring_format"] == "ppr"].copy()
    ppr    = ppr.dropna(subset=["Rank"])
    ppr["adp_name"]  = ppr["PlayerTeam (Bye)"].apply(_clean_adp_name)
    ppr["adp_value"] = ppr["Rank"].apply(
        lambda r: _adp_to_value(r, budget=league["budget"])
    )
    adp_lookup = ppr.set_index("adp_name")["adp_value"].to_dict()

    df = result.copy()
    df["_match"] = df["player_name"].str.lower().str.strip()

    # Strip name suffixes (Jr., Sr., II, III, IV) for fallback matching
    df["_match_bare"] = df["_match"].str.replace(r"\s+(jr\.?|sr\.?|i{2,3}v?|iv)$", "", regex=True, flags=re.IGNORECASE).str.strip()

    def lookup(row):
        return adp_lookup.get(row["_match"]) or adp_lookup.get(row["_match_bare"])

    df["adp_value"] = df.apply(lookup, axis=1)

    has_adp = df["adp_value"].notna()
    df.loc[has_adp, "auction_value"] = (
        blend * df.loc[has_adp, "adp_value"] +
        (1 - blend) * df.loc[has_adp, "auction_value"]
    ).clip(lower=1.0).round(1)

    df.drop(columns=["_match", "_match_bare", "adp_value"], inplace=True)

    # Rescale meaningful values so the top-N still sums to the full league budget.
    # Only scale players above $1 — don't inflate the bench/depth floor.
    n_slots     = league["teams"] * (sum(league["roster"].values()) + league.get("bench", 6) + league.get("IR", 1))
    target_sum  = league["teams"] * league["budget"]
    top_n       = df.nlargest(n_slots, "auction_value")
    current_sum = top_n["auction_value"].sum()
    if current_sum > 0:
        scale = target_sum / current_sum
        above = df["auction_value"] > 1.0
        df.loc[above, "auction_value"] = (df.loc[above, "auction_value"] * scale).clip(lower=1.01).round(1)

    return df


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

    # Recency weighting: multiply each game's contribution by a per-season weight.
    # This means a recent full season dominates over an old injury-shortened one.
    # e.g. CMC 2024 (4 injury games, weight 0.7) = 2.8 effective games vs
    #      CMC 2025 (17 games, weight 1.0) = 17.0 effective games — correct.
    max_season = train["season"].max()
    train["season_lag"] = (max_season - train["season"]).astype(int)
    oldest_weight = RECENCY_WEIGHTS[max(RECENCY_WEIGHTS)]
    train["season_weight"] = train["season_lag"].map(RECENCY_WEIGHTS).fillna(oldest_weight)
    train["w_fpts"]  = train["fpts"]  * train["season_weight"]
    train["w_games"] = train["games"] * train["season_weight"]

    agg_cols = {
        "total_fpts":  ("fpts",          "sum"),
        "total_games": ("games",         "sum"),
        "w_fpts":      ("w_fpts",        "sum"),
        "w_games":     ("w_games",       "sum"),
        "seasons":     ("season",        "nunique"),
        "position":    ("position",      "first"),
    }
    if "player_name" in train.columns:
        agg_cols["player_name"] = ("player_name", "last")  # last = most recent season's name

    player_history = train.groupby("player_id").agg(**agg_cols).reset_index()
    # Weighted PPG: recency-weighted fpts / recency-weighted games
    player_history["ppg"] = player_history["w_fpts"] / player_history["w_games"].clip(lower=0.01)
    player_history.drop(columns=["w_fpts", "w_games"], inplace=True)

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

    print("Blending ADP...")
    adp_path = os.path.join(DATA_DIR, "adp.csv")
    result = blend_adp(result, adp_path)

    # Attach PPR ADP rank for display
    if os.path.exists(adp_path):
        adp_df  = pd.read_csv(adp_path)
        ppr_adp = adp_df[adp_df["scoring_format"] == "ppr"][["Rank", "PlayerTeam (Bye)"]].copy()
        ppr_adp["_match"] = ppr_adp["PlayerTeam (Bye)"].apply(_clean_adp_name)
        ppr_adp = ppr_adp.rename(columns={"Rank": "adp_rank"})
        result["_match"] = result["player_name"].str.lower().str.strip()
        result = result.merge(ppr_adp[["_match", "adp_rank"]], on="_match", how="left")
        result.drop(columns=["_match"], inplace=True)

    # Merge years_of_experience from players for display in dashboard
    if not players.empty and "years_of_experience" in players.columns:
        yoe = players[["gsis_id", "years_of_experience"]].rename(columns={"gsis_id": "player_id"})
        result = result.merge(yoe, on="player_id", how="left")

    # Attach prior-season actual fpts so the dashboard can show last year's performance
    prev_season = projection_season - 1
    prev = stats[stats["season"] == prev_season].copy()
    if len(prev) > 0:
        prev["fpts_prev"] = compute_fantasy_points(prev, ppr=ppr)
        prev_totals = prev.groupby("player_id")["fpts_prev"].sum().reset_index()
        prev_totals.columns = ["player_id", f"fpts_{prev_season}"]
        result = result.merge(prev_totals, on="player_id", how="left")

    # Add K and DST rows — not projected via PAR, all valued at $1
    kd_rows = []

    # Kickers: from players.csv (position == "K"), filter to recently active
    if not players.empty and "position" in players.columns:
        k_all = players[players["position"] == "K"].copy()
        if "last_season" in k_all.columns:
            k_all = k_all[k_all["last_season"] >= 2024]
        k_players = k_all[["gsis_id", "display_name"]].copy()
        k_players.columns = ["player_id", "player_name"]
        k_players["position"] = "K"
        kd_rows.append(k_players)

    # DST: from adp.csv
    adp_path = os.path.join(DATA_DIR, "adp.csv")
    if os.path.exists(adp_path):
        adp = pd.read_csv(adp_path)
        dst = adp[adp["scoring_format"] == "dst"].copy()
        if dst.empty:
            dst = adp[adp["POS"].str.startswith("D", na=False)].drop_duplicates("PlayerTeam (Bye)")
        dst["player_name"] = dst["PlayerTeam (Bye)"].str.replace(r"\(.*\)", "", regex=True).str.strip()
        dst["position"] = "DST"
        dst["player_id"] = "dst_" + dst["player_name"].str.lower().str.replace(" ", "_")
        kd_rows.append(dst[["player_id", "player_name", "position"]])

    if kd_rows:
        kd = pd.concat(kd_rows, ignore_index=True)
        kd["auction_value"]   = 1.0
        kd["projected_fpts"]  = 0.0
        kd["par"]             = 0.0
        kd["replacement_pts"] = 0.0
        result = pd.concat([result, kd], ignore_index=True)

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
