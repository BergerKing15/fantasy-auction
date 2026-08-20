# Session Notes

Context-recovery notes for Claude Code. Newest session at the top.

---

## 2026-08-20 — Doc Sync (read-through, no code changes)

Read the full codebase and brought README.md / NOTES.md back in line with what the code
actually does. No behavior changed. What the docs had been wrong about:

| Doc claim (old) | Reality |
|---|---|
| ADP from FantasyPros | DraftSharks primary (auth'd CSV export), FantasyPros is the fallback |
| Shrinkage `K = 16` | `SHRINKAGE_K = 8.0` |
| Simple 3-season average | Recency-weighted (1.0 / 0.7 / 0.4) + age decay past 30 |
| No ADP blend documented | `ADP_BLEND = 0.85` toward expert value, PAR-history players only |
| Replacement = QB12/RB30/WR30/TE13 | QB18 / RB89 / WR114 / TE31 (via `BENCH_DEPTH`) |
| Hittable budget $2,292 (108 slots) | $2,208 (192 slots = 12 × (9 starters + 6 bench + 1 IR)) |
| Dashboard = "run streamlit" | 5 tabs, tags, notes, pick management, editable budget plan |
| No `.env` setup documented | DraftSharks credentials required to re-fetch data |
| Backtest table from the K=16 era | Re-run 2026-08-20, numbers below |

Also fixed: the in-app "How are values calculated?" expander quoted the old replacement counts.

**Backtest re-run 2026-08-20** (PPR=1.0, seasons 2017–2025, ≥6 games):

| Position | n | MAE | RMSE |
|---|---|---|---|
| QB | 332 | 97.0 | 121.4 |
| RB | 714 | 61.9 | 74.4 |
| WR | 1,147 | 55.3 | 67.1 |
| TE | 632 | 42.3 | 51.4 |
| Overall | 2,825 | 59.0 | 74.5 |

Per-season MAE 56.2–64.5 — stable, no season dominating. Essentially unchanged from the K=16
numbers overall, but with the tuned model 2025 is now included in the eval set.

**Current data snapshot:** 6,681 stat rows (2014–2025) · 8,725 players · 524 DraftSharks rows ·
998 projections (WR 374, RB 243, TE 195, QB 120, K 66 — **no DST**) · 488 of them carry a DS value.

---

## 2026-05-18 → 05-24 — Sessions 3–5 (DraftSharks, Player Profiles, TODO cleanup)

Reconstructed from git history (`d84405a` → `fc92917`).

### DraftSharks integration (`d84405a`, `3fdda1c`, `f545a24`)
- Replaced FantasyPros as the primary value source. `_ds_login()` posts to `/login` with the
  `_frontendCSRF` token scraped from the login form; creds come from `.env`.
- Two CSV exports pulled: `/auction-values/export` and `/rankings/export`, merged on Player+Team.
- **Critical parameter:** the export endpoint needs `?pprSuperflexSlug=ppr`. Without it the
  endpoint silently returns standard (non-PPR) values — this looked correct but wasn't.
  `scoring=ppr` does *not* work.
- Two dollar columns kept deliberately: `ds_auction_value` (DS model) and
  `market_auction_value` (crowd consensus). The dashboard shows both.
- adp.csv schema changed completely — it is now DraftSharks-shaped, not FantasyPros-shaped.
  `blend_adp()` branches on the presence of `ds_auction_value` to stay backward compatible.

### Calibration (`99394f0`, `63ab318`, `3bef2a0`)
- `SHRINKAGE_K` 16 → 8 (trust recent stats more).
- `BENCH_DEPTH` tuned to RB 3.5 / WR 4.5 / QB 1.5 / TE 2.5.
- `ADP_BLEND` 0.5 → 0.85. The stat model can't see team context, target share, or camp news;
  the expert value can.
- Rookie injection: DS players with no stat history get their DS value + a rookie-prior fpts.

### Player profiles (`155a72f`, `694d672`, `630e4c5`)
- Headshots via `<img>` HTML (Streamlit can't fetch the NFL CDN server-side).
- Row-click on the Player Board sets `nav_to_profile`, then a JS snippet clicks tab index 4.
  Fragile if tab order changes — the index is hardcoded.
- `Styler.applymap` → `Styler.map` for pandas 2.1+.

### TODO.txt items 1–12 (`fddf0be`, `c3aa82c`, `fc92917`)
All 12 user-reported items resolved — see TODO.txt for the item-by-item record. Highlights:
pick delete/price-correct, owner dropdown, target/avoid tags, player notes, rank column,
DS-only filter for skill positions (drops retirees like Flacco/Carr from the board),
editable MIN/MAX budget plan on the Teams tab.

### Deploy status
- GitHub remote is live: `https://github.com/BergerKing15/fantasy-auction.git`, master pushed.
- Streamlit Community Cloud: **not yet set up** — repo → streamlit.io/cloud → main file
  `dashboard/app.py` → Deploy.

---

## 2026-05-17 — Sessions 1 & 2 (Build + Bugfix)

### Decisions Made
- **Dashboard**: Streamlit (not React/Vercel). Deploy free on Streamlit Community Cloud.
- **Data**: nfl_data_py for stats. FantasyData dropped — paywall. (ADP later moved to DraftSharks.)
- **ML**: Bayesian shrinkage toward position/age-group priors. Simple, interpretable.

### League Settings
- 12 teams, $200 budget
- Roster: QB, RB, RB, WR, WR, TE, FLEX (RB/WR/TE), K, DST + 5–8 bench + 1 IR
- Scoring: 6 pts/TD ALL types (including passing — non-standard), 0.05/pass yd, 0.1/rush+rec yd
- Weeks 1–14 regular season, 15–17 playoffs, top 6 qualify
- INT penalty and fumble lost: assumed −2 each (still not confirmed by user)

### Data Notes
- nfl_data_py seasonal data has no position column — merged from players.csv via
  `player_id` → `gsis_id`.
- nflverse's pre-aggregated player_stats file lags for the most recent season;
  `_seasonal_from_pbp()` rebuilds it from play-by-play.

### Bugs Fixed
- `groupby().apply()` O(n) Python loops → vectorized `.agg()`; projection time minutes → 0.15s
- `data/` was gitignored — the deployed app needs the CSVs, so it's tracked now
- `sys.exit()` crashes Streamlit → `raise FileNotFoundError`
- `to_csv()` fails on Streamlit Cloud's read-only fs → wrapped in `try/except OSError`
- Age stratification broken (seasonal_stats had no age column → every prior keyed "unknown")
  → age merged in before computing priors
- `pd.concat([])` crash when all backtest seasons skipped → guarded
- Auction values inflated ($78 top player) → `BENCH_DEPTH` multipliers added

---

## Open Items

- [ ] Deploy to Streamlit Community Cloud (last step of CLAUDE.md task 9)
- [ ] **DST missing entirely** — DraftSharks export has no team-defense rows, and the
      `adp[POS startswith "D"]` fallback in `projection_engine.run()` finds nothing.
      Needs a separate DST source or manual entry.
- [ ] No persistence — a browser refresh mid-auction wipes picks/tags/notes. A JSON
      save/load button on the Live Draft tab would be cheap insurance before draft day.
- [ ] `max_available = 2025` hardcoded in `project_players()` — bump when 2026 stats exist
- [ ] Kickers have DS values but no projected points
- [ ] Confirm INT / fumble scoring against actual league rules
- [ ] Profile-tab navigation depends on the hardcoded tab index 4 in the JS click handler
