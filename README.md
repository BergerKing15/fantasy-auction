# Fantasy Football Auction Dashboard — 2026–27 Season

A Python tool that scrapes historical NFL stats, builds Bayesian player projections, converts those projections to auction dollar values, and dynamically reprices players during a live draft.

---

## Overview

Auction fantasy football requires answering two questions:
1. **What is each player worth?** (projection engine)
2. **What is each player worth *right now*, given what's already been spent?** (reprice engine)

This tool answers both. Projections use Bayesian shrinkage — every player's forecast regresses toward the historical base rate for their position/age group, preventing outliers and giving reasonable estimates for rookies and injury returnees. Auction values are derived from Points Above Replacement (PAR), so dollar totals always sum to the total league budget.

---

## League Settings

| Setting | Value |
|---|---|
| Teams | 12 |
| Budget per team | $200 |
| Starters | QB, RB, RB, WR, WR, TE, FLEX (RB/WR/TE), K, DST |
| Bench spots | 5–8 |
| IR slots | 1 |
| Scoring | 6 pts/TD (all types), 0.05 pts/pass yd, 0.1 pts/rush+rec yd |
| PPR | Configurable (0, 0.5, or 1.0) |
| Regular season | Weeks 1–14 |
| Playoffs | Weeks 15–17 (top 6 teams) |

---

## Methods

### 1. Data Collection
- **Player stats (2014–2025):** `nfl_data_py` — pulls from nflverse's free, curated dataset.
- **ADP (current season):** Scraped from FantasyPros, which aggregates across ESPN, Yahoo, Sleeper, and NFL.

### 2. Fantasy Points
Each player-season is scored under the league's custom settings. PPR multiplier (0, 0.5, or 1.0) is configurable at runtime.

### 3. Bayesian Projections
For each player, projected points-per-game is:

```
projection = w × empirical_ppg + (1 - w) × position_prior
```

Where `w = games_played / (games_played + K)` and `K = 16`. This means:
- Players with a full season of data are weighted ~50% vs. their position prior.
- Rookies (0 games) receive 100% position prior — projected like an average rookie at their position.
- Veterans with multiple seasons accumulate more empirical weight each year.

Priors are computed from historical data, stratified by position and age bracket.

### 4. Auction Values (PAR Method)
1. Compute replacement level = projected points of the last expected starter at each position (QB12, RB30, WR30, TE13).
2. For each player: `PAR = max(0, projected_pts - replacement_level)`
3. Total hittable budget = `12 × $200 − 108 × $1 = $2,292` (one $1 minimum per starter slot)
4. Auction value = `$1 + (PAR / total_PAR) × $2,292`

### 5. Repricing (Live Draft)
As players are bought during the auction:
- Track actual price vs. projected price for each player sold.
- Compute per-position discount/premium factor: `mean(actual / projected)` for that position.
- Apply this factor to all remaining unsold players at that position.
- Account for remaining roster needs and remaining budgets across teams.

### 6. Backtest
To validate accuracy, projections are generated using only data available through year `Y-1`, then compared against actual `Y` performance. Metrics: RMSE and MAE on projected vs. actual fantasy points.

---

## File Structure

```
fantasy_auction/
├── CLAUDE.md                # Project instructions for Claude Code
├── NOTES.md                 # Session notes for context recovery
├── README.md                # This file
├── requirements.txt         # Python dependencies
├── webscraping.py           # Fetches stats (nfl_data_py) and ADP (FantasyPros)
├── projection_engine.py     # Bayesian projections + auction values
├── backtest.py              # Tests projection accuracy on past seasons
├── reprice_engine.py        # Live-draft repricing logic
├── data/                    # Raw and processed CSVs
│   ├── seasonal_stats.csv   # Historical player stats
│   ├── players.csv          # Player metadata (age, draft info)
│   ├── adp.csv              # ADP data (PPR, half, standard)
│   └── projections.csv      # Output from projection engine
└── dashboard/
    └── app.py               # Streamlit frontend
```

---

## Setup

**Requirements:** Python 3.9+

```bash
git clone https://github.com/BergerKing15/fantasy-auction.git
cd fantasy-auction
pip install -r requirements.txt
```

### Fetch Data
```bash
python webscraping.py
```
Downloads stats (2014–2025) and current ADP into `data/`. First run takes ~2–5 minutes.

### Run Projections
```bash
python projection_engine.py
```
Outputs `data/projections.csv`.

### Backtest
```bash
python backtest.py
```
Prints accuracy metrics across held-out seasons.

### Launch Dashboard
```bash
streamlit run dashboard/app.py
```
Opens at `http://localhost:8501`.

---

## Deployment

Deploy to [Streamlit Community Cloud](https://streamlit.io/cloud) for free — non-technical users get a shareable URL with no installs required:
1. Push this repo to GitHub (public or private).
2. Log in at streamlit.io/cloud with your GitHub account.
3. Select the repo, set `dashboard/app.py` as the entry point.
4. Share the generated URL.

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| [nfl_data_py / nflverse](https://github.com/nflverse/nfl_data_py) | Player stats 2014–2025 | Free |
| [FantasyPros](https://www.fantasypros.com/nfl/adp/ppr-overall.php) | ADP (aggregated) | Free |
