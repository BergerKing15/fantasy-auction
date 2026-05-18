"""
webscraping.py
Fetches player stats via nfl_data_py and ADP/auction values from DraftSharks
(with FantasyPros as fallback).
Run directly: python webscraping.py
Outputs: data/seasonal_stats.csv, data/players.csv, data/adp.csv
"""

import io
import os
import time

import pandas as pd
import requests
from bs4 import BeautifulSoup

try:
    import nfl_data_py as nfl
except ImportError:
    raise ImportError("Run: pip install nfl_data_py")

# ─── Config ───────────────────────────────────────────────────────────────────

DATA_DIR = "data"
STAT_YEARS = list(range(2014, 2026))   # 2014 through 2025

ADP_URLS = {
    "std":  "https://www.fantasypros.com/nfl/adp/overall.php",
    "half": "https://www.fantasypros.com/nfl/adp/half-point-ppr-overall.php",
    "ppr":  "https://www.fantasypros.com/nfl/adp/ppr-overall.php",
    "dst":  "https://www.fantasypros.com/nfl/adp/dst.php",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ─── Stats ────────────────────────────────────────────────────────────────────

def _seasonal_from_pbp(year: int) -> pd.DataFrame:
    """
    Aggregate seasonal stats from play-by-play for a single year.
    Used as a fallback when nflverse hasn't published the pre-aggregated file yet.
    """
    print(f"    {year}: seasonal file not yet published — aggregating from play-by-play...")
    cols = [
        "game_id", "season", "season_type",
        "passer_player_id", "pass_attempt", "complete_pass",
        "passing_yards", "pass_touchdown", "interception", "sack",
        "rusher_player_id", "rush_attempt", "rushing_yards", "rush_touchdown",
        "receiver_player_id", "receiving_yards",
        "fumbled_1_player_id", "fumble_lost",
    ]
    pbp = nfl.import_pbp_data([year], columns=cols)
    pbp = pbp[pbp["season_type"] == "REG"].copy()

    # ── Passing ──────────────────────────────────────────────────────────────
    pass_plays = pbp[pbp["passer_player_id"].notna() & (pbp["pass_attempt"] == 1)]
    passing = (
        pass_plays.groupby("passer_player_id")
        .agg(
            completions=("complete_pass", "sum"),
            attempts=("pass_attempt", "sum"),
            passing_yards=("passing_yards", "sum"),
            passing_tds=("pass_touchdown", "sum"),
            interceptions=("interception", "sum"),
            sacks=("sack", "sum"),
            games_p=("game_id", "nunique"),
        )
        .reset_index()
        .rename(columns={"passer_player_id": "player_id"})
    )
    sack_fum = (
        pbp[(pbp["sack"] == 1) & (pbp["fumble_lost"] == 1) &
            (pbp["fumbled_1_player_id"] == pbp["passer_player_id"])]
        .groupby("passer_player_id").size()
        .reset_index(name="sack_fumbles_lost")
        .rename(columns={"passer_player_id": "player_id"})
    )
    passing = passing.merge(sack_fum, on="player_id", how="left")

    # ── Rushing ──────────────────────────────────────────────────────────────
    rush_plays = pbp[pbp["rusher_player_id"].notna() & (pbp["rush_attempt"] == 1)]
    rushing = (
        rush_plays.groupby("rusher_player_id")
        .agg(
            carries=("rush_attempt", "sum"),
            rushing_yards=("rushing_yards", "sum"),
            rushing_tds=("rush_touchdown", "sum"),
            games_r=("game_id", "nunique"),
        )
        .reset_index()
        .rename(columns={"rusher_player_id": "player_id"})
    )
    rush_fum = (
        pbp[(pbp["rush_attempt"] == 1) & (pbp["fumble_lost"] == 1) &
            (pbp["fumbled_1_player_id"] == pbp["rusher_player_id"])]
        .groupby("rusher_player_id").size()
        .reset_index(name="rushing_fumbles_lost")
        .rename(columns={"rusher_player_id": "player_id"})
    )
    rushing = rushing.merge(rush_fum, on="player_id", how="left")

    # ── Receiving ─────────────────────────────────────────────────────────────
    rec_plays = pbp[pbp["receiver_player_id"].notna() & (pbp["pass_attempt"] == 1)]
    receiving = (
        rec_plays.groupby("receiver_player_id")
        .agg(
            targets=("pass_attempt", "sum"),
            receptions=("complete_pass", "sum"),
            receiving_yards=("receiving_yards", "sum"),
            receiving_tds=("pass_touchdown", "sum"),
            games_rec=("game_id", "nunique"),
        )
        .reset_index()
        .rename(columns={"receiver_player_id": "player_id"})
    )
    rec_fum = (
        pbp[(pbp["complete_pass"] == 1) & (pbp["fumble_lost"] == 1) &
            (pbp["fumbled_1_player_id"] == pbp["receiver_player_id"])]
        .groupby("receiver_player_id").size()
        .reset_index(name="receiving_fumbles_lost")
        .rename(columns={"receiver_player_id": "player_id"})
    )
    receiving = receiving.merge(rec_fum, on="player_id", how="left")

    # ── Merge all three ───────────────────────────────────────────────────────
    all_ids = (
        set(passing["player_id"]) |
        set(rushing["player_id"]) |
        set(receiving["player_id"])
    )
    df = (
        pd.DataFrame({"player_id": list(all_ids)})
        .merge(passing, on="player_id", how="left")
        .merge(rushing, on="player_id", how="left")
        .merge(receiving, on="player_id", how="left")
    )

    num_cols = [
        "completions", "attempts", "passing_yards", "passing_tds", "interceptions",
        "sacks", "sack_fumbles_lost",
        "carries", "rushing_yards", "rushing_tds", "rushing_fumbles_lost",
        "targets", "receptions", "receiving_yards", "receiving_tds", "receiving_fumbles_lost",
    ]
    df[num_cols] = df[num_cols].fillna(0)
    df["games"] = df[["games_p", "games_r", "games_rec"]].max(axis=1).fillna(0)
    df = df.drop(columns=["games_p", "games_r", "games_rec"])
    df["season"] = year
    return df


def fetch_seasonal_stats(years: list[int] = STAT_YEARS) -> pd.DataFrame:
    """Download seasonal player stats for the given years.

    Uses nfl_data_py import_seasonal_data where available; falls back to
    play-by-play aggregation for years where the pre-aggregated file hasn't
    been published yet (e.g. the most recent completed season).

    Seasonal data has no position column, so we merge it from players.csv.
    player_id (seasonal) maps to gsis_id (players).
    """
    print(f"Fetching seasonal stats ({years[0]}-{years[-1]})...")

    frames = []
    # Collect years that work with import_seasonal_data vs those that need PBP
    standard_years, pbp_years = [], []
    for y in years:
        try:
            nfl.import_seasonal_data([y], s_type="REG")
            standard_years.append(y)
        except Exception:
            pbp_years.append(y)

    if standard_years:
        frames.append(nfl.import_seasonal_data(standard_years, s_type="REG"))
    for y in pbp_years:
        frames.append(_seasonal_from_pbp(y))

    df = pd.concat(frames, ignore_index=True)

    # Merge in position and player_name from players.csv
    players_path = os.path.join(DATA_DIR, "players.csv")
    if os.path.exists(players_path):
        players = pd.read_csv(players_path, usecols=["gsis_id", "position", "display_name"])
    else:
        players = nfl.import_players()[["gsis_id", "position", "display_name"]]
    df = df.merge(players.rename(columns={"gsis_id": "player_id"}), on="player_id", how="left")

    df = df[df["position"].isin(["QB", "RB", "WR", "TE"])].copy()
    df = df.rename(columns={"display_name": "player_name"})
    df.reset_index(drop=True, inplace=True)

    out = os.path.join(DATA_DIR, "seasonal_stats.csv")
    df.to_csv(out, index=False)
    print(f"  Saved {len(df):,} rows -> {out}")
    return df


def fetch_player_info() -> pd.DataFrame:
    """Download player metadata: name, position, DOB, draft details, etc."""
    print("Fetching player info...")
    df = nfl.import_players()
    df = df[df["position"].isin(["QB", "RB", "WR", "TE", "K"])].copy()

    out = os.path.join(DATA_DIR, "players.csv")
    df.to_csv(out, index=False)
    print(f"  Saved {len(df):,} rows -> {out}")
    return df


# ─── ADP ──────────────────────────────────────────────────────────────────────

def _parse_fantasypros_adp(html: str, scoring: str) -> pd.DataFrame:
    """Parse a FantasyPros ADP page into a DataFrame."""
    soup = BeautifulSoup(html, "html.parser")

    # FantasyPros wraps the ADP table in <table id="data">
    table = soup.find("table", {"id": "data"})
    if table is None:
        # Fallback: grab the first table that has an ADP-looking header
        for t in soup.find_all("table"):
            header_text = t.get_text().lower()
            if "adp" in header_text and "player" in header_text:
                table = t
                break

    if table is None:
        raise ValueError("Could not locate ADP table in FantasyPros HTML.")

    thead = table.find("thead")
    col_headers = [th.get_text(strip=True) for th in thead.find_all("th")]

    rows = []
    for tr in table.find("tbody").find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all("td")]
        if cells:
            rows.append(cells)

    df = pd.DataFrame(rows, columns=col_headers)
    df["scoring_format"] = scoring
    return df


def fetch_adp(scoring: str = "ppr") -> pd.DataFrame:
    """Fetch current-season ADP for one scoring format."""
    url = ADP_URLS[scoring]
    print(f"  Fetching {scoring.upper()} ADP from FantasyPros...")
    resp = requests.get(url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    return _parse_fantasypros_adp(resp.text, scoring)


def fetch_all_adp() -> pd.DataFrame:
    """Fetch ADP for all scoring formats (skill + K + DST) and combine into one CSV."""
    print("Fetching ADP data...")
    frames = []
    for scoring in ["std", "half", "ppr", "dst"]:
        try:
            frames.append(fetch_adp(scoring))
        except Exception as exc:
            print(f"  Warning: could not fetch {scoring} ADP — {exc}")
        time.sleep(1.5)   # polite crawl delay

    if not frames:
        raise RuntimeError("All ADP fetches failed. Check your internet connection.")

    combined = pd.concat(frames, ignore_index=True)
    out = os.path.join(DATA_DIR, "adp.csv")
    combined.to_csv(out, index=False)
    print(f"  Saved {len(combined):,} rows -> {out}")
    return combined


# ─── DraftSharks ──────────────────────────────────────────────────────────────

DS_BASE = "https://www.draftsharks.com"


def _ds_login() -> requests.Session:
    """Authenticate with DraftSharks using credentials from .env or environment."""
    env_file = ".env" if os.path.exists(".env") else os.path.join(os.path.dirname(__file__), ".env")
    creds: dict[str, str] = {}
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                k, _, v = line.strip().partition("=")
                if k and v:
                    creds[k] = v

    email    = creds.get("DRAFT_SHARKS_USER")    or os.environ.get("DRAFT_SHARKS_USER", "")
    password = creds.get("DRAFT_SHARKS_PASSWORD") or os.environ.get("DRAFT_SHARKS_PASSWORD", "")
    if not email or not password:
        raise RuntimeError("DraftSharks credentials missing — set DRAFT_SHARKS_USER / DRAFT_SHARKS_PASSWORD in .env")

    s = requests.Session()
    s.headers.update({"User-Agent": HEADERS["User-Agent"]})

    r = s.get(f"{DS_BASE}/login", timeout=15)
    soup = BeautifulSoup(r.text, "html.parser")
    csrf_input = soup.find("input", {"name": "_frontendCSRF"})
    if csrf_input is None:
        raise RuntimeError("Could not find CSRF token on DraftSharks login page")

    resp = s.post(f"{DS_BASE}/login", data={
        "_frontendCSRF":         csrf_input["value"],
        "LoginForm[email]":      email,
        "LoginForm[password]":   password,
    }, timeout=15)

    # Successful login redirects to "/" — still at /login means failure
    if resp.url.rstrip("/").endswith("/login"):
        raise RuntimeError("DraftSharks login failed — check credentials in .env")
    return s


def fetch_draftsharks_data() -> pd.DataFrame:
    """
    Fetch auction values and PPR rankings from DraftSharks CSV exports.

    Returns a DataFrame written to data/adp.csv that is backward-compatible
    with the old FantasyPros format (same column names) while also including
    DraftSharks-specific fields: ds_auction_value, ds_proj, consensus_proj,
    injury_risk, sos used by projection_engine for more accurate blending.
    """
    print("Fetching DraftSharks data...")
    s = _ds_login()

    print("  Downloading auction values...")
    av_resp = s.get(f"{DS_BASE}/auction-values/export?format=csv", timeout=30)
    av_resp.raise_for_status()
    av_df = pd.read_csv(io.StringIO(av_resp.text))

    print("  Downloading PPR rankings...")
    rk_resp = s.get(f"{DS_BASE}/rankings/export?format=csv", timeout=30)
    rk_resp.raise_for_status()
    rk_df = pd.read_csv(io.StringIO(rk_resp.text))

    # Parse DS AuctionValue: "$49" -> 49.0
    av_df["ds_auction_value"] = (
        av_df["DS AuctionValue"].str.replace("$", "", regex=False).astype(float)
    )

    # Position rank: 1 = best at that position
    av_df = av_df.sort_values("Rank").copy()
    av_df["pos_rank"] = av_df.groupby("Fantasy Position").cumcount() + 1

    # POS column in FantasyPros style ("RB3", "WR12") for backward compat
    av_df["POS"] = av_df["Fantasy Position"] + av_df["pos_rank"].astype(str)

    # Merge snake-draft ADP from rankings data
    rk_merge = rk_df[["Player", "Team", "ADP"]].rename(columns={"ADP": "ds_adp"})
    av_df = av_df.merge(rk_merge, on=["Player", "Team"], how="left")

    # Build "PlayerTeam (Bye)" for backward compat with projection_engine name matching
    av_df["PlayerTeam (Bye)"] = (
        av_df["Player"] + " " + av_df["Team"].fillna("") +
        "(" + av_df["Bye"].astype(str) + ")"
    )

    result = av_df.rename(columns={
        "Fantasy Position": "position",
        "DS Proj":          "ds_proj",
        "Consensus Proj":   "consensus_proj",
        "InjuryRisk":       "injury_risk",
        "SOS":              "sos",
    }).copy()
    result["scoring_format"] = "ppr"

    keep = [
        "scoring_format", "Rank", "Player", "Team", "position",
        "POS", "PlayerTeam (Bye)", "Bye", "ds_auction_value", "ds_adp",
        "ds_proj", "consensus_proj", "injury_risk", "sos",
    ]
    result = result[[c for c in keep if c in result.columns]]

    out = os.path.join(DATA_DIR, "adp.csv")
    result.to_csv(out, index=False)
    print(f"  Saved {len(result):,} rows -> {out}")
    return result


# ─── Entrypoint ───────────────────────────────────────────────────────────────

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    fetch_player_info()
    fetch_seasonal_stats()
    # Prefer DraftSharks (richer data); fall back to FantasyPros on failure
    try:
        fetch_draftsharks_data()
    except Exception as exc:
        print(f"  DraftSharks failed ({exc}), falling back to FantasyPros ADP...")
        fetch_all_adp()
    print("\nAll data saved to data/. Run projection_engine.py next.")


if __name__ == "__main__":
    main()
