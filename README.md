# Fantasy Football Auction Dashboard — 2026–27 Season

A Python tool that pulls historical NFL stats and expert auction data, builds Bayesian player
projections, converts them to auction dollar values, and dynamically reprices players during a
live draft. The frontend is a Streamlit dashboard you can run locally or share as a URL.

---

## Overview

Auction fantasy football requires answering two questions:
1. **What is each player worth?** (projection engine)
2. **What is each player worth *right now*, given what's already been spent?** (reprice engine)

This tool answers both.

Values come from two sources that are deliberately kept separate on the board:

| Column | Source |
|---|---|
| **DS Value $** | DraftSharks' own PPR auction model (their number, used as-is when available) |
| **Market $** | DraftSharks' crowd-sourced auction market consensus — what owners actually pay |
| **Current $** | DS Value adjusted live by the reprice engine as picks come in |
| **Proj Pts** | This repo's Bayesian projection (recency-weighted, shrunk toward position priors) |

For any skill player DraftSharks *doesn't* cover, the value falls back to this repo's own
Points-Above-Replacement model. See [Methods](#methods).

---

## League Settings

| Setting | Value |
|---|---|
| Teams | 12 |
| Budget per team | $200 |
| Starters | QB, RB, RB, WR, WR, TE, FLEX (RB/WR/TE), K, DST |
| Bench spots | 5–8 (engine assumes 6) |
| IR slots | 1 |
| Scoring | 6 pts/TD (all types, including passing), 0.05 pts/pass yd, 0.1 pts/rush+rec yd |
| Turnovers | −2 per INT, −2 per fumble lost (assumed — not confirmed by league rules) |
| PPR | Configurable (0, 0.5, or 1.0); all shipped data is built at full PPR |
| Regular season | Weeks 1–14 |
| Playoffs | Weeks 15–17 (top 6 teams) |

Teams and budget are adjustable in the dashboard sidebar. Roster composition is currently fixed in
`LEAGUE` inside [projection_engine.py](projection_engine.py#L31).

---

## Methods

### 1. Data Collection ([webscraping.py](webscraping.py))
- **Player stats (2014–2025):** `nfl_data_py` (nflverse). The most recent season is usually not
  published as a pre-aggregated file yet, so `_seasonal_from_pbp()` rebuilds it from play-by-play.
- **Player metadata:** `nfl_data_py.import_players()` — name, position, birth date, headshot, team.
- **Schedule (2026):** `nfl_data_py.import_schedules()` → `data/schedule.csv`.
- **Auction values + ADP:** **DraftSharks** CSV exports (requires a paid account — see
  [Setup](#setup)). Pulls DS auction value, market auction value, ADP, DS/consensus projections,
  floor/ceiling, injury risk, and strength of schedule.
- **Team defenses:** DraftSharks' *auction-values* export contains no defenses, so all 32 come
  from the *rankings* export and are appended separately (`_draftsharks_defenses`).
- **Fallback (currently broken):** `fetch_all_adp()` scrapes free FantasyPros ADP if DraftSharks
  fails, but FantasyPros is now JS-rendered — the table parser finds nothing, and only the top 5
  rows are even present in the page source. Treat DraftSharks as the only working value source
  until this is rewritten. The committed CSVs mean a broken fetch never breaks the dashboard.

### 2. Fantasy Points
Each player-season is scored under the league's custom settings. The PPR multiplier (0, 0.5, 1.0)
is a runtime argument, so the whole pipeline can be rerun for any format.

### 3. Bayesian Projections ([projection_engine.py](projection_engine.py))

**Recency-weighted empirical rate.** The 3 most recent seasons are pooled, with older seasons
discounted so a stale year can't outvote a current one:

| Seasons back | Weight |
|---|---|
| 0 (most recent) | 1.0 |
| 1 | 0.7 |
| 2 | 0.4 |

`ppg = Σ(fpts × weight) / Σ(games × weight)`. A 4-game injury season two years back contributes
~1.6 effective games; a healthy 17-game season last year contributes 17.

**Shrinkage toward a prior.** Projected per-game points is

```
projection_ppg = w × empirical_ppg + (1 − w) × prior_ppg
w = total_games / (total_games + 8)
```

Priors are the mean PPG for that **position × age bracket** (rookie ≤23, young ≤27, prime ≤30,
veteran 31+), computed from the prior 8 seasons. With `K = 8`, a player with three full seasons
(~51 games) gets ~86% empirical weight; a rookie with no games gets 100% prior — i.e. projected
like an average rookie at their position.

**Age decay.** Players over 30 lose 3% per year past 30, floored at −30%.

**Season total** = `projection_ppg × age_adjustment × 17`.

### 4. Auction Values — PAR Method
1. **Replacement level** = the projected points of the last player expected to be rostered at that
   position, including bench depth. Starter counts alone leave only ~80 players above replacement,
   which concentrates every dollar at the top. With `BENCH_DEPTH` multipliers the pool is:

   | Position | Players above replacement |
   |---|---|
   | QB | 18 |
   | RB | 89 |
   | WR | 114 |
   | TE | 31 |

2. **PAR** = `max(0, projected_fpts − replacement_pts)`.
3. **Hittable budget** = `12 × $200 − 192 roster slots × $1 = $2,208`
   (192 = 12 teams × (9 starters + 6 bench + 1 IR)).
4. **Auction value** = `$1 + (PAR / total_PAR) × $2,208`.
5. **ADP blend** — the PAR value is blended 85/15 toward the expert value
   (`ADP_BLEND = 0.85`). Only players with real PAR history are blended; blending $1 depth players
   toward high expert values would bloat the pool and crush everyone else.
6. **Missing players are injected** — anyone in the DraftSharks export with no stat history
   (rookies, name mismatches) is added with their expert value and a rookie-prior point estimate.

> **In practice:** the dashboard overwrites `auction_value` with the raw DraftSharks value wherever
> one exists (~488 of 1,001 players), so the PAR model mainly drives **Proj Pts**, the reprice
> baseline, and the values for players DraftSharks doesn't rank.

### 4b. Kickers and Defenses
Neither goes through the PAR model — we don't score kicking, and the league's DST scoring rules
were never defined, so projecting either would be invention. Both are taken from the expert export
instead:

| Position | Count | Value source |
|---|---|---|
| K | 39 | DraftSharks dollar values (Aubrey $6 down to $1) |
| DST | 32 | Position-rank log curve, $3 for DST1 down to $1 |

Defenses show **Proj Pts** blank rather than 0 — DraftSharks' own DST projection is carried through
as a column so you can still rank them, alongside ADP, bye, and SOS.

### 5. Repricing — Live Draft ([reprice_engine.py](reprice_engine.py))
- Every recorded pick stores actual price vs. the value we had projected.
- Once **3+** players at a position have sold, that position gets a factor =
  `median(actual / projected)` — median, not mean, so one panic bid doesn't move the market.
- A **budget factor** compares dollars still on the table against the value still on the board and
  scales everything proportionally, clipped to [0.5, 2.0] to cap swings.
- `Current $ = auction_value × position_factor × budget_factor`, floored at $1.

### 6. Backtest ([backtest.py](backtest.py))
Projections for season *Y* are built using only data through *Y−1*, then compared against actual
*Y* results (players with ≥6 games only).

**Current accuracy (2017–2025, full PPR, n = 2,825):**

| Position | n | MAE | RMSE |
|---|---|---|---|
| QB | 332 | 97.0 | 121.4 |
| RB | 714 | 61.9 | 74.4 |
| WR | 1,147 | 55.3 | 67.1 |
| TE | 632 | 42.3 | 51.4 |
| **Overall** | **2,825** | **59.0** | **74.5** |

Per-season MAE is stable at 56–65, i.e. no single season drives the average. QB error is the
largest in absolute terms simply because QBs score the most points under this league's 6-pt
passing TD rule.

---

## File Structure

```
fantasy_auction/
├── CLAUDE.md                # Project instructions for Claude Code
├── NOTES.md                 # Session notes for context recovery
├── README.md                # This file
├── TODO.txt                 # User-reported issues / feature requests + resolutions
├── requirements.txt         # Python dependencies
├── .env                     # DraftSharks credentials (gitignored — see Setup)
├── pyrightconfig.json       # Pylance/Pyright path resolution for the IDE
├── webscraping.py           # Stats, players, schedule (nfl_data_py) + DraftSharks/FantasyPros
├── projection_engine.py     # Bayesian projections + PAR auction values + ADP blend + K/DST
├── backtest.py              # Tests projection accuracy on held-out seasons
├── reprice_engine.py        # Live-draft auction state + repricing logic
├── session_store.py         # Save/restore the live draft session (JSON)
├── data/                    # Raw and processed CSVs (committed so the deployed app has data)
│   ├── seasonal_stats.csv   # Historical player stats 2014–2025 (6,681 rows)
│   ├── players.csv          # Player metadata: age, headshot, team (8,725 rows)
│   ├── adp.csv              # DraftSharks auction values + ADP + defenses (552 rows)
│   ├── schedule.csv         # 2026 regular-season schedule
│   ├── projections.csv      # Engine output (1,001 players incl. 39 K, 32 DST)
│   ├── draft_state.json     # Autosaved live draft (gitignored)
│   └── backtest_results.csv # Backtest detail (gitignored)
└── dashboard/
    └── app.py               # Streamlit frontend (5 tabs)
```

---

## Setup

**Requirements:** Python 3.11+

```bash
git clone https://github.com/BergerKing15/fantasy-auction.git
cd fantasy-auction
pip install -r requirements.txt
```

### DraftSharks credentials (only needed to re-fetch data)

`webscraping.py` logs into DraftSharks to download the auction-value and rankings CSV exports.
Create a `.env` in the repo root:

```
DRAFT_SHARKS_USER=your@email.com
DRAFT_SHARKS_PASSWORD=yourpassword
```

`.env` is gitignored. Without it the scraper prints a warning and falls back to free FantasyPros
ADP. **The dashboard itself needs no credentials** — the CSVs in `data/` are committed, so a
cloned or deployed copy runs immediately.

### Refresh Data
```bash
python webscraping.py
```
Downloads stats, players, schedule, and DraftSharks values into `data/`. First run takes ~2–5
minutes (play-by-play aggregation for the newest season is the slow part).

### Run Projections
```bash
python projection_engine.py
```
Outputs `data/projections.csv`.

### Backtest
```bash
python backtest.py
```
Prints per-season and per-position MAE/RMSE.

### Launch Dashboard
```bash
streamlit run dashboard/app.py
```
Opens at `http://localhost:8501`.

---

## Using the Dashboard

**Sidebar** — PPR format, number of teams, budget per team, projection season.
**Load / Refresh Projections** reads `data/projections.csv` if present, otherwise runs the engine
live. **Reset Auction** clears all recorded picks (after copying the current save to
`draft_state.backup.json`).

### 💾 Draft State — your work survives a refresh
Picks, tags, notes, and budget edits are saved automatically to `data/draft_state.json` after every
change, and reloaded when you open the app. A refresh, a closed laptop, or a crashed browser costs
you nothing.

Two independent safety nets, because they fail in different situations:
- **Autosave** — writes to disk. Works locally; on Streamlit Cloud a container restart wipes it, and
  a read-only filesystem blocks it entirely. The sidebar caption tells you which state you're in.
- **⬇️ Download draft state / ⬆️ Restore from file** — a JSON file you keep. Works everywhere and
  survives anything. **Before draft day, click Download once so you know where the button is.**

If the save file is ever unreadable, the app says so, turns autosave *off* rather than overwriting
it, and keeps running — restore from a download instead.

### 📋 Player Board
Every rostered-caliber player, ranked by current value. Filter by position (including K and DST),
max price, drafted status, or tag (⭐ target / ❌ avoid). The **Rank** column is the board's own ordering; **ADP** is
the DraftSharks rank, so the two can be compared side by side. **Click any row to jump to that
player's profile.** The caption shows total board value vs. total league budget as a sanity check.

### 🔴 Live Draft
Record each pick: player, winning owner (dropdown of the 12 league owners), price paid. Recorded
picks appear in **Manage Picks**, where you can delete a pick or correct a mistyped price. The
**Position Market Trends** chart shows which positions are going over or under projection — the
same factors the reprice engine is applying.

### 👥 Teams
Spent/remaining budget and roster for every owner, plus **My Budget Plan** — an editable MIN/MAX
target per roster slot that auto-fills ACTUAL from picks recorded under owner "Me" and flags each
slot ✅ / ⚠️ over / ⚠️ under.

### 📊 Results
All picks with actual-vs-projected price and a value ratio, plus a scatter plot against the
break-even line.

### 🏈 Player Profile
Headshot, ADP, DS value, market value, projected points, injury risk, bye week; target/avoid
buttons; a free-text notes field; the player's 2026 schedule with bye highlighted; and a
season-by-season stat table and fpts/PPG chart. Tags and notes added here are saved with the rest
of the draft state.

---

## Deployment

The repo is on GitHub at [BergerKing15/fantasy-auction](https://github.com/BergerKing15/fantasy-auction).
To put it in front of non-technical users:

1. Log in at [streamlit.io/cloud](https://streamlit.io/cloud) with the GitHub account.
2. New app → select the repo → main file path `dashboard/app.py` → Deploy.
3. Share the generated URL. No installs, no credentials — the committed CSVs are all it needs.

Because `data/` is committed, refreshing data for the deployed app means re-running
`webscraping.py` + `projection_engine.py` locally and pushing the updated CSVs.

---

## Known Limitations

- **K and DST are expert-value only.** Both are on the board with values, ADP, and byes, but
  neither has a points projection from this repo's model — see [4b](#4b-kickers-and-defenses).
  DST dollar values are rank-derived, not a DraftSharks number.
- **Autosave is per-machine.** The save file lives next to the data, so opening the dashboard on a
  different computer starts empty. Move a draft between machines with Download → Restore.
- **Turnover scoring assumed.** INT and fumble-lost are −2 each; confirm against league rules.
- **Two-source values.** Where DraftSharks covers a player their number wins outright, so board
  values are only as good as DraftSharks' model. This repo's PAR model is the check on it — a big
  gap between **Proj Pts** rank and **DS Value $** rank is a signal worth investigating.
- **`max_available = 2025`** is hardcoded in `project_players()`. Bump it when 2026 stats exist.
- **`BENCH_DEPTH` is calibrated, not validated.** The multipliers were tuned so top-player values
  land in the same range as published expert values, not fit to historical auction results.

---

## Data Sources

| Source | Data | Cost |
|---|---|---|
| [nfl_data_py / nflverse](https://github.com/nflverse/nfl_data_py) | Player stats 2014–2025, metadata, schedules | Free |
| [DraftSharks](https://www.draftsharks.com/auction-values/ppr) | Auction values, market values, ADP, projections, injury risk, defenses | Paid account |
| [FantasyPros](https://www.fantasypros.com/nfl/adp/ppr-overall.php) | ADP — fallback, **scraper broken** (site is JS-rendered) | Free |

FantasyData (the source originally listed in CLAUDE.md) was dropped — its stat and ADP pages are
paywalled.
