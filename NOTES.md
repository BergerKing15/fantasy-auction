# Session Notes

## 2026-05-17 — Session 1 & 2 (Build + Bugfix)

### Task Status (from CLAUDE.md)
1. [x] Gather Context
2. [x] Ask Questions / Recommendations
3. [x] Write initial README
4. [x] Webscrape
5. [x] Build projection engine
6. [x] Backtest
7. [x] Build reprice engine
8. [x] Build dashboard
9. [ ] Deploy — PARTIALLY DONE (git initialized, needs GitHub remote + Streamlit Cloud)

---

### Decisions Made
- **Dashboard**: Streamlit (not React/Vercel). Deploy free on Streamlit Community Cloud.
- **Data**: nfl_data_py (stats) + FantasyPros scraping (ADP). Dropped FantasyData — paywall.
- **ML**: Bayesian shrinkage toward position/age-group priors. Simple, interpretable.

### League Settings
- 12 teams, $200 budget
- Roster: QB, RB, RB, WR, WR, TE, FLEX (RB/WR/TE), K, DST + 5-8 bench + 1 IR
- Scoring: 6 pts/TD ALL types (including passing — non-standard), 0.05/pass yd, 0.1/rush+rec yd, PPR configurable
- Weeks 1-14 regular season, 15-17 playoffs, top 6 qualify
- INT penalty and fumble lost: assumed -2 each (not confirmed by user — ask if wrong)

---

### Data Notes
- nfl_data_py seasonal data has no position column — merged from players.csv via player_id -> gsis_id
- nflverse pre-aggregated player_stats file is not yet published for 2025 — webscraping.py uses play-by-play fallback (_seasonal_from_pbp)
- STAT_YEARS: 2014-2025
- Data downloaded: 6,681 player-season rows, 8,342 players, 1,189 ADP rows

### Key Files
- data/seasonal_stats.csv — historical player stats 2014-2025
- data/players.csv — player metadata (name, position, birth_date, gsis_id)
- data/adp.csv — FantasyPros ADP (std/half/ppr)
- data/projections.csv — 818 players with projected fpts and auction values

---

### Backtest Results (PPR=1.0, seasons 2017-2024)
| Position | n    | MAE  | RMSE  |
|----------|------|------|-------|
| QB       | 295  | 95.5 | 118.4 |
| RB       | 645  | 61.9 | 73.9  |
| WR       | 1023 | 55.5 | 66.5  |
| TE       | 557  | 42.7 | 51.4  |
| Overall  | 2520 | 59.0 | 73.6  |

### Auction Value Calibration
- Budget check: top-192 players sum to exactly $2,400 (12 x $200) ✓
- 162 players priced above $1
- Top values: CMC $51.7, Jahmyr Gibbs $44.1, Ja'Marr Chase $41.8, Josh Allen (QB) $28.0, Trey McBride (TE) $21.1
- Calibrated against RotoWire 12-team values — comparable to their $44 top player range
- Key config: BENCH_DEPTH = {QB:1.5, RB:2.5, WR:2.5, TE:1.5} — using starter-only counts produced only 80 players with PAR and inflated top values to $78+

---

### Bugs Fixed (Session 2)
- groupby().apply() was O(n) Python loops → replaced with vectorized .agg() — projection time: minutes -> 0.15s
- data/ directory was gitignored — removed
- sys.exit() crashes Streamlit — replaced with raise FileNotFoundError
- result.to_csv() fails on Streamlit Cloud read-only fs — wrapped in try/except OSError
- Age stratification was broken — seasonal_stats had no age column, all priors keyed to "unknown" — fixed by merging age into stats before computing priors
- Duplicate birth_date → age computation — deduplicated into single age_lookup
- Dead lookup_prior() function after vectorization — removed
- Unused imports (LEAGUE, np, scipy, Literal, sys) — removed
- pd.concat([]) crash when all backtest seasons skipped — guarded with if rows else pd.DataFrame()
- display_name fetched in merge then discarded — removed from merge
- 2025 nflverse pre-aggregated file unavailable (HTTP 404) — PBP aggregation fallback added
- Auction values inflated ($78+ top player) — added BENCH_DEPTH multipliers to replacement levels

---

### Deploy Status (Task 9)
- [x] Git repo initialized (master branch, 2 commits)
- [ ] GitHub remote not yet pushed — user needs to:
  1. Create repo at github.com (name: fantasy-auction, public, no README)
  2. Run: git remote add origin https://github.com/BergerKing15/fantasy-auction.git
  3. Run: git push -u origin master
- [ ] Streamlit Cloud not yet set up — after push:
  1. Go to streamlit.io/cloud, sign in with GitHub
  2. New app -> select repo -> main file: dashboard/app.py -> Deploy

---

### Known Limitations / TODO
- Kicker (K) and DST projections not implemented — values are implicitly $1 (minimum)
- max_available = 2025 in projection_engine.py — update if nflverse publishes 2025 pre-aggregated stats (would speed up webscraping.py)
- dashboard/frontend.tsx is an empty leftover file — safe to delete
- BENCH_DEPTH factors are tuned against RotoWire but not exhaustively validated
