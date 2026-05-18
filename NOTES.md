# Session Notes

## 2026-05-17 — Initial Setup

### Project Summary
Fantasy Football Auction Dashboard for 26-27 season.
Goal: projection engine + live repricing + Streamlit dashboard.

### Decisions Made
- **Dashboard**: Streamlit (not React/Vercel). Deploy free on Streamlit Community Cloud via GitHub.
- **Data**: nfl_data_py (stats) + FantasyPros scraping (ADP). Dropped FantasyData — it has a paywall.
- **ML**: Bayesian shrinkage toward position/age priors. Keep it simple.

### League Settings
- 12 teams, $200 budget
- Roster: QB, RB, RB, WR, WR, TE, FLEX (RB/WR/TE), K, DST + 5-8 bench + 1 IR
- Scoring: 6 pts/TD (ALL TDs including pass), 0.05/pass yd, 0.1/rush+rec yd, PPR configurable
- Weeks 1-14 regular season, 15-17 playoffs, top 6 qualify

### Scoring Note
Passing TDs are 6 pts here (not the standard 4). This bumps QB values noticeably vs standard leagues.
Interceptions and fumbles lost: using standard -2 penalty (not specified by user, confirm if different).

### Status
- [x] README written
- [x] NOTES written
- [ ] webscraping.py — next up
- [ ] projection_engine.py
- [ ] backtest.py
- [ ] reprice_engine.py
- [ ] dashboard/app.py
- [ ] requirements.txt
- [ ] GitHub repo created and pushed

### Key Files
- `data/seasonal_stats.csv` — nfl_data_py seasonal stats, 2014-2025
- `data/players.csv` — player metadata
- `data/adp.csv` — FantasyPros ADP (std/half/ppr)
- `data/projections.csv` — output of projection_engine.py

### nfl_data_py Column Reference (seasonal_data)
Key columns used: player_id, player_name, position, season, week,
  completions, attempts, passing_yards, passing_tds, interceptions,
  carries, rushing_yards, rushing_tds,
  receptions, targets, receiving_yards, receiving_tds,
  sack_fumbles_lost, rushing_fumbles_lost, receiving_fumbles_lost,
  games (games played)

### Auction Value Formula
- total_starters = 12 × (1+2+2+1+1+1+1) = 108 slots
- hittable_budget = 12×$200 - 108×$1 = $2,292
- replacement levels ≈ QB12, RB30, WR30, TE13 (FLEX treated as shared pool)
- value = $1 + (PAR / total_PAR) × $2,292

### Open Questions / TODO
- Confirm INT and fumble lost penalties with user (assumed -2 each)
- Kicker and DST projections: use simpler ADP-based values for now
- FLEX replacement level: decide if treating WR/RB/TE replacement pools separately or combined
