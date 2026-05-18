"""
webscraping.py
Fetches player stats via nfl_data_py and ADP from FantasyPros.
Run directly: python webscraping.py
Outputs: data/seasonal_stats.csv, data/players.csv, data/adp.csv
"""

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
STAT_YEARS = list(range(2014, 2025))   # 2014 through 2024 (latest nflverse release)

ADP_URLS = {
    "std":  "https://www.fantasypros.com/nfl/adp/overall.php",
    "half": "https://www.fantasypros.com/nfl/adp/half-point-ppr-overall.php",
    "ppr":  "https://www.fantasypros.com/nfl/adp/ppr-overall.php",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# ─── Stats ────────────────────────────────────────────────────────────────────

def fetch_seasonal_stats(years: list[int] = STAT_YEARS) -> pd.DataFrame:
    """Download seasonal player stats for the given years via nfl_data_py.

    Seasonal data has no position column, so we merge it from the players file.
    player_id (seasonal) maps to gsis_id (players).
    """
    print(f"Fetching seasonal stats ({years[0]}-{years[-1]})...")
    df = nfl.import_seasonal_data(years, s_type="REG")

    # Merge in position and display_name from players data
    players_path = os.path.join(DATA_DIR, "players.csv")
    if os.path.exists(players_path):
        players = pd.read_csv(players_path, usecols=["gsis_id", "position", "display_name"])
        df = df.merge(players.rename(columns={"gsis_id": "player_id"}), on="player_id", how="left")
    else:
        # Fall back: fetch inline (slower but self-contained)
        players = nfl.import_players()[["gsis_id", "position", "display_name"]]
        df = df.merge(players.rename(columns={"gsis_id": "player_id"}), on="player_id", how="left")

    # Keep only skill positions
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
    df = df[df["position"].isin(["QB", "RB", "WR", "TE"])].copy()

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
    """Fetch ADP for all three scoring formats and combine into one CSV."""
    print("Fetching ADP data...")
    frames = []
    for scoring in ["std", "half", "ppr"]:
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


# ─── Entrypoint ───────────────────────────────────────────────────────────────

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    fetch_player_info()
    fetch_seasonal_stats()
    fetch_all_adp()
    print("\nAll data saved to data/. Run projection_engine.py next.")


if __name__ == "__main__":
    main()
