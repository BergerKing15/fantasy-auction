"""
dashboard/app.py
Streamlit Fantasy Football Auction Dashboard.

Run locally:  streamlit run dashboard/app.py
Deploy:       Streamlit Community Cloud (streamlit.io/cloud)
"""

import os
import sys

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
import plotly.express as px
import plotly.graph_objects as go

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from projection_engine import run as run_projections, compute_fantasy_points
from reprice_engine import AuctionState, DraftResult, reprice, position_market_summary

# ─── Constants ────────────────────────────────────────────────────────────────

# DraftSharks uses LAR/JAC/LVR; nfl_data_py schedule uses LA/JAX/LV
DS_TO_NFL = {"LAR": "LA", "JAC": "JAX", "LVR": "LV"}

OWNERS = [
    "Me", "Tov", "Daryl", "Adam", "Rich",
    "Ganz", "Marc", "Ryan", "Rob", "Elliott", "New Guy", "Scott",
]

# Default per-position budget plan (MIN, MAX) for "My Team" tracker
DEFAULT_BUDGET_PLAN = {
    "QB":        (5,  10),
    "RB1":       (20, 30),
    "RB2":       (10, 19),
    "WR1":       (25, 35),
    "WR2":       (15, 25),
    "WR3":       (10, 20),
    "TE":        (8,  14),
    "DEF":       (1,   1),
    "K":         (1,   1),
    "RB3":       (3,   8),
    "RB4":       (3,   8),
    "RB5":       (3,   8),
    "WR4":       (2,   6),
    "WR5":       (2,   6),
    "QB2":       (1,   3),
    "RB/WR":     (3,   6),
    "RB/WR (2)": (1,   3),
}

# ─── Cached Loaders ───────────────────────────────────────────────────────────

@st.cache_data
def load_projections_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)

@st.cache_data
def load_seasonal_stats(path: str) -> pd.DataFrame:
    return pd.read_csv(path, low_memory=False)

@st.cache_data
def load_schedule(path: str) -> pd.DataFrame:
    return pd.read_csv(path)

@st.cache_data(show_spinner="Running projection engine...")
def compute_projections(ppr: float, projection_season: int) -> pd.DataFrame:
    return run_projections(ppr=ppr, projection_season=projection_season)

# ─── Helpers ──────────────────────────────────────────────────────────────────

def player_photo(headshot_url, width: int = 130) -> None:
    """Display player headshot via HTML img (browser-fetches the NFL CDN URL)."""
    if pd.notna(headshot_url) and str(headshot_url).startswith("http"):
        st.markdown(
            f'<img src="{headshot_url}" width="{width}" '
            f'style="border-radius:10px; object-fit:cover;">',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<div style="width:{width}px;height:{width}px;border-radius:10px;'
            f'background:#2a2a2a;display:flex;align-items:center;'
            f'justify-content:center;font-size:40px;">🏈</div>',
            unsafe_allow_html=True,
        )


def get_team_schedule(player_row: pd.Series, schedule_df: pd.DataFrame) -> pd.DataFrame:
    """Return the week-by-week schedule for the player's team, including bye."""
    team = player_row.get("latest_team")
    if pd.isna(team) or not str(team).strip():
        ds = str(player_row.get("Team", "") or "")
        team = DS_TO_NFL.get(ds, ds) or None

    if not team or schedule_df.empty:
        return pd.DataFrame()

    mask = (schedule_df["home_team"] == team) | (schedule_df["away_team"] == team)
    games = schedule_df[mask].copy()
    if games.empty:
        return pd.DataFrame()

    games["Opponent"] = games.apply(
        lambda r: r["away_team"] if r["home_team"] == team else r["home_team"], axis=1
    )
    games["H/A"] = games.apply(
        lambda r: "Home" if r["home_team"] == team else "Away", axis=1
    )
    games = games.rename(columns={"week": "Wk", "gameday": "Date"})

    played_weeks = set(games["Wk"])
    max_wk = int(games["Wk"].max())
    for wk in range(1, max_wk + 2):
        if wk not in played_weeks:
            bye_row = pd.DataFrame([{"Wk": wk, "Date": "", "Opponent": "BYE", "H/A": "—"}])
            games = pd.concat([games, bye_row], ignore_index=True)
            break

    return (
        games[["Wk", "Date", "Opponent", "H/A"]]
        .sort_values("Wk")
        .reset_index(drop=True)
    )


def render_player_panel(player_name: str, repriced: pd.DataFrame,
                        schedule_df: pd.DataFrame, ppr: float,
                        stats_path: str) -> None:
    """Render the full player detail panel (used in both board and profile tab)."""
    matches = repriced[repriced["player_name"] == player_name]
    if matches.empty:
        return
    row = matches.iloc[0]

    st.divider()

    # ── Header row: photo + key metrics ──────────────────────────────────────
    ph_col, info_col = st.columns([1, 5])
    with ph_col:
        player_photo(row.get("headshot_url"), width=120)

    with info_col:
        team_label = (
            str(row.get("latest_team") or row.get("Team") or "").strip() or "—"
        )
        pos = row.get("position", "")

        # Target / Avoid badge
        is_target = player_name in st.session_state.get("targets", set())
        is_avoid  = player_name in st.session_state.get("avoid_list", set())
        badge = " ⭐ TARGET" if is_target else (" ❌ AVOID" if is_avoid else "")
        st.subheader(f"{player_name}  —  {pos}  ·  {team_label}{badge}")

        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("ADP",         f"#{int(row['adp_rank'])}" if pd.notna(row.get("adp_rank")) else "—")
        m2.metric("Pre-Draft $", f"${row['auction_value']:.1f}")
        m3.metric("Proj Pts",    f"{row['projected_fpts']:.0f}" if pd.notna(row.get("projected_fpts")) else "—")
        m4.metric("DS Value",    f"${row['ds_auction_value']:.0f}" if pd.notna(row.get("ds_auction_value")) else "—")
        m5.metric("Injury Risk", str(row.get("injury_risk") or "—"))
        m6.metric("Bye Wk",      int(row["Bye"]) if pd.notna(row.get("Bye")) else "—")

    # ── Target / Avoid controls ───────────────────────────────────────────────
    tag_c1, tag_c2, tag_c3 = st.columns([1, 1, 4])
    with tag_c1:
        if is_target:
            if st.button("Remove Target ⭐", key=f"untarget_{player_name}"):
                st.session_state.targets.discard(player_name)
                st.rerun()
        else:
            if st.button("Mark as Target ⭐", key=f"target_{player_name}"):
                st.session_state.targets.add(player_name)
                st.session_state.avoid_list.discard(player_name)
                st.rerun()
    with tag_c2:
        if is_avoid:
            if st.button("Remove Avoid ❌", key=f"unavoid_{player_name}"):
                st.session_state.avoid_list.discard(player_name)
                st.rerun()
        else:
            if st.button("Mark as Avoid ❌", key=f"avoid_{player_name}"):
                st.session_state.avoid_list.add(player_name)
                st.session_state.targets.discard(player_name)
                st.rerun()

    # ── Comments ──────────────────────────────────────────────────────────────
    comments = st.session_state.get("player_comments", {})
    existing = comments.get(player_name, "")
    new_comment = st.text_area(
        "Notes / Comments",
        value=existing,
        placeholder="Add scouting notes, injury updates, draft strategy...",
        key=f"comment_{player_name}",
        height=80,
    )
    if new_comment != existing:
        st.session_state.player_comments[player_name] = new_comment

    # ── Bottom section: schedule | stats ─────────────────────────────────────
    sched_col, hist_col = st.columns([1, 2])

    with sched_col:
        st.caption("**2026 Schedule**")
        sched = get_team_schedule(row, schedule_df)
        if sched.empty:
            st.caption("Schedule unavailable.")
        else:
            def _style_bye(val):
                return "color: #888; font-style: italic;" if val == "BYE" else ""
            st.dataframe(
                sched.style.map(_style_bye, subset=["Opponent"]),
                use_container_width=True,
                height=400,
                hide_index=True,
            )

    with hist_col:
        st.caption("**Season History**")
        if not os.path.exists(stats_path):
            st.caption("Run webscraping.py to generate seasonal_stats.csv.")
        else:
            stats_df = load_seasonal_stats(stats_path)
            pid = row["player_id"]
            if str(pid).startswith("adp_"):
                st.caption("No historical stats available (rookie).")
            else:
                player_stats = stats_df[stats_df["player_id"] == pid].sort_values("season").copy()
                if player_stats.empty:
                    st.caption("No historical stats found.")
                else:
                    player_stats["fpts"] = compute_fantasy_points(player_stats, ppr=ppr).round(1)
                    player_stats["ppg"]  = (
                        player_stats["fpts"] / player_stats["games"].clip(lower=1)
                    ).round(2)

                    fig = go.Figure()
                    fig.add_trace(go.Bar(
                        x=player_stats["season"].astype(str),
                        y=player_stats["fpts"],
                        name="Fpts",
                        marker_color="steelblue",
                    ))
                    fig.add_trace(go.Scatter(
                        x=player_stats["season"].astype(str),
                        y=player_stats["ppg"],
                        name="PPG",
                        yaxis="y2",
                        mode="lines+markers",
                        line=dict(color="orange", width=2),
                    ))
                    fig.update_layout(
                        yaxis=dict(title="Total Fpts"),
                        yaxis2=dict(title="PPG", overlaying="y", side="right"),
                        legend=dict(orientation="h"),
                        margin=dict(t=10, b=10),
                        height=220,
                    )
                    st.plotly_chart(fig, use_container_width=True)

                    pos = row.get("position", "")
                    stat_map: dict[str, str] = {
                        "season": "Season", "games": "G", "fpts": "Fpts", "ppg": "PPG"
                    }
                    if pos == "QB":
                        stat_map.update({
                            "completions": "Cmp", "attempts": "Att",
                            "passing_yards": "Pass Yds", "passing_tds": "Pass TD",
                            "interceptions": "INT", "rushing_yards": "Rush Yds",
                        })
                    elif pos == "RB":
                        stat_map.update({
                            "carries": "Car", "rushing_yards": "Rush Yds",
                            "rushing_tds": "Rush TD", "receptions": "Rec",
                            "receiving_yards": "Rec Yds", "receiving_tds": "Rec TD",
                        })
                    else:
                        stat_map.update({
                            "targets": "Tgt", "receptions": "Rec",
                            "receiving_yards": "Rec Yds", "receiving_tds": "Rec TD",
                            "rushing_yards": "Rush Yds",
                        })
                    visible = [c for c in stat_map if c in player_stats.columns]
                    tbl = player_stats[visible].rename(columns=stat_map)
                    for c in tbl.select_dtypes(include="number").columns:
                        if c not in ("PPG",):
                            tbl[c] = tbl[c].round(0).astype("Int64", errors="ignore")
                    st.dataframe(tbl, use_container_width=True, hide_index=True)


# ─── Page Config ──────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Fantasy Auction 2026",
    page_icon="🏈",
    layout="wide",
)

# ─── Session State Init ───────────────────────────────────────────────────────

if "auction_state" not in st.session_state:
    st.session_state.auction_state = AuctionState(teams=12, budget=200.0)
if "projections" not in st.session_state:
    st.session_state.projections = None
if "profile_player" not in st.session_state:
    st.session_state.profile_player = None
if "nav_to_profile" not in st.session_state:
    st.session_state.nav_to_profile = False
if "targets" not in st.session_state:
    st.session_state.targets = set()
if "avoid_list" not in st.session_state:
    st.session_state.avoid_list = set()
if "player_comments" not in st.session_state:
    st.session_state.player_comments = {}

# ─── Sidebar: Settings ────────────────────────────────────────────────────────

st.sidebar.title("⚙️ Settings")

ppr = st.sidebar.select_slider(
    "PPR Format",
    options=[0.0, 0.5, 1.0],
    value=1.0,
    format_func=lambda v: {0.0: "Standard (0 PPR)", 0.5: "Half PPR", 1.0: "Full PPR"}[v],
)

num_teams = st.sidebar.number_input("Teams in League", min_value=8, max_value=16, value=12)
budget    = st.sidebar.number_input("Budget per Team ($)", min_value=50, max_value=500, value=200)
st.sidebar.divider()
proj_season = st.sidebar.number_input("Projection Season", min_value=2020, max_value=2030, value=2026)

DATA_DIR         = os.path.join(os.path.dirname(__file__), "..")
PROJECTIONS_PATH = os.path.join(DATA_DIR, "data", "projections.csv")
STATS_PATH       = os.path.join(DATA_DIR, "data", "seasonal_stats.csv")
SCHEDULE_PATH    = os.path.join(DATA_DIR, "data", "schedule.csv")
ADP_PATH         = os.path.join(DATA_DIR, "data", "adp.csv")

if st.sidebar.button("🔄 Load / Refresh Projections"):
    if os.path.exists(PROJECTIONS_PATH):
        df = load_projections_csv(PROJECTIONS_PATH)
    else:
        df = compute_projections(ppr, proj_season)
    st.session_state.projections = df
    st.session_state.auction_state = AuctionState(teams=num_teams, budget=float(budget))
    st.sidebar.success(f"Loaded {len(df):,} players.")

st.sidebar.divider()
if st.sidebar.button("🗑️ Reset Auction"):
    st.session_state.auction_state = AuctionState(teams=num_teams, budget=float(budget))
    st.sidebar.success("Auction state cleared.")

# ─── Auto-load projections on first visit ─────────────────────────────────────

if st.session_state.projections is None and os.path.exists(PROJECTIONS_PATH):
    st.session_state.projections = load_projections_csv(PROJECTIONS_PATH)

projections = st.session_state.projections
state       = st.session_state.auction_state

# ── Filter retirees / non-DS players (Task 9) ────────────────────────────────
# Only show skill players that DraftSharks ranks; filters out retired/irrelevant
# players who appear in historical stats but not in the 2026 DS data.
# K are sourced from adp.csv directly since the projection engine doesn't cover them.
if projections is not None:
    skill_mask = projections["position"].isin(["QB", "RB", "WR", "TE"])
    has_ds = projections["ds_auction_value"].notna() & (projections["ds_auction_value"] > 0)
    projections_display = projections[~skill_mask | has_ds].copy()

    # Bring in K from adp.csv since projection engine doesn't populate them
    if os.path.exists(ADP_PATH):
        adp_raw = pd.read_csv(ADP_PATH)
        k_rows  = adp_raw[adp_raw["position"] == "K"].copy()
        if not k_rows.empty:
            k_rows = k_rows.rename(columns={"Player": "player_name", "Rank": "adp_rank"})
            k_rows["position"]       = "K"
            k_rows["auction_value"]  = k_rows["ds_auction_value"].fillna(1.0)
            k_rows["repriced_value"] = k_rows["auction_value"]
            k_rows["player_id"]      = "adp_k_" + k_rows["player_name"].str.replace(" ", "_")
            k_rows["projected_fpts"] = pd.NA
            k_rows["ppg"]            = pd.NA
            k_rows["seasons"]        = pd.NA
            # Merge into display, skipping kickers already present
            existing_k = set(projections_display[projections_display["position"] == "K"]["player_name"])
            new_k = k_rows[~k_rows["player_name"].isin(existing_k)]
            if not new_k.empty:
                projections_display = pd.concat([projections_display, new_k], ignore_index=True)
else:
    projections_display = projections

# ── Use DraftSharks values as initial prices (Task: DS prices in dashboard) ───
if projections_display is not None and "ds_auction_value" in projections_display.columns:
    has_ds2 = projections_display["ds_auction_value"].notna() & (projections_display["ds_auction_value"] > 0)
    projections_display.loc[has_ds2, "auction_value"] = projections_display.loc[has_ds2, "ds_auction_value"]

# ── Normalize years_of_experience column name ─────────────────────────────────
if projections_display is not None:
    if "years_of_experience_x" in projections_display.columns and "years_of_experience" not in projections_display.columns:
        projections_display = projections_display.rename(columns={"years_of_experience_x": "years_of_experience"})
    if "years_of_experience_y" in projections_display.columns:
        projections_display = projections_display.drop(columns=["years_of_experience_y"], errors="ignore")

repriced     = reprice(projections_display, state) if projections_display is not None else None
schedule_df  = load_schedule(SCHEDULE_PATH) if os.path.exists(SCHEDULE_PATH) else pd.DataFrame()

# ─── Main Tabs ────────────────────────────────────────────────────────────────

tab_board, tab_live, tab_teams, tab_results, tab_player = st.tabs([
    "📋 Player Board",
    "🔴 Live Draft",
    "👥 Teams",
    "📊 Results",
    "🏈 Player Profile",
])

# ─── Tab 1: Player Board ──────────────────────────────────────────────────────

with tab_board:
    st.header("Player Board")

    if repriced is None:
        st.info("Click **Load / Refresh Projections** in the sidebar to get started.")
        st.stop()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        pos_filter = st.multiselect(
            "Position", options=["QB", "RB", "WR", "TE", "K"], default=["QB", "RB", "WR", "TE"]
        )
    with col2:
        max_price = st.number_input("Max $ Value (filter)", min_value=1, max_value=300, value=200)
    with col3:
        show_drafted = st.checkbox("Show Drafted Players", value=False)
    with col4:
        tag_filter = st.selectbox("Tag Filter", ["All", "⭐ Targets Only", "❌ Avoid Only"])

    # Build display df — include drafted players when checkbox is on (Task 8 fix)
    if show_drafted:
        display = reprice(projections_display, state, exclude_drafted=False).copy()
    else:
        display = repriced.copy()

    if pos_filter:
        display = display[display["position"].isin(pos_filter)]
    display = display[display["auction_value"] <= max_price]

    if tag_filter == "⭐ Targets Only":
        display = display[display["player_name"].isin(st.session_state.targets)]
    elif tag_filter == "❌ Avoid Only":
        display = display[display["player_name"].isin(st.session_state.avoid_list)]

    # Add tag and rank columns
    display = display.reset_index(drop=True)
    display.insert(0, "Rank", display.index + 1)
    display["Tag"] = display["player_name"].apply(
        lambda n: "⭐" if n in st.session_state.targets
                  else ("❌" if n in st.session_state.avoid_list else "")
    )
    drafted_ids = state.drafted_ids()
    display["Status"] = display["player_id"].apply(
        lambda pid: "✅ Drafted" if pid in drafted_ids else ""
    )

    prev_col = next((c for c in display.columns if c.startswith("fpts_")), None)
    exp_col  = "years_of_experience" if "years_of_experience" in display.columns else None
    display_cols = {
        "Rank":             "Rank",
        "Tag":              "Tag",
        "player_name":      "Player",
        "position":         "Pos",
        "adp_rank":         "ADP",
        **({"years_of_experience": "Exp"} if exp_col else {}),
        "projected_fpts":   "Proj Pts",
        **(  {prev_col: f"{prev_col[5:]} Actual"} if prev_col else {}),
        "auction_value":    "Pre-Draft $",
        "repriced_value":   "Current $",
        "ppg":              "Recent PPG",
        "Status":           "Status",
    }
    show = (
        display[[c for c in display_cols if c in display.columns]]
        .rename(columns=display_cols)
        .reset_index(drop=True)
    )
    for col in ["Proj Pts", f"{prev_col[5:]} Actual" if prev_col else ""]:
        if col in show.columns:
            show[col] = show[col].round(1)

    # Budget summary line
    total_board_value = display["auction_value"].sum()
    total_league_budget = num_teams * budget
    st.caption(
        f"Showing {len(show)} players · Total projected value: "
        f"**${total_board_value:,.0f}** vs league budget **${total_league_budget:,}**  "
        f"· Click any row to open Player Profile"
    )

    board_event = st.dataframe(
        show,
        use_container_width=True,
        height=500,
        on_select="rerun",
        selection_mode="single-row",
        column_config={
            "Current $":   st.column_config.NumberColumn(format="$%.1f"),
            "Pre-Draft $": st.column_config.NumberColumn(format="$%.1f"),
            "Player":      st.column_config.TextColumn("Player"),
        },
    )

    sel_rows = board_event.selection.rows
    if sel_rows:
        sel_name = show.iloc[sel_rows[0]]["Player"]
        st.session_state.profile_player = sel_name
        st.session_state.nav_to_profile = True
    else:
        if len(repriced) > 0:
            fig = px.bar(
                repriced[repriced["position"].isin(pos_filter or ["QB","RB","WR","TE"])].head(60),
                x="player_name", y="repriced_value", color="position",
                title="Top Players by Current Auction Value",
                labels={"repriced_value": "Auction Value ($)", "player_name": ""},
            )
            fig.update_layout(xaxis_tickangle=-45)
            st.plotly_chart(fig, use_container_width=True)

    # Valuation method explanation (Task 7)
    with st.expander("ℹ️ How are values calculated?"):
        st.markdown("""
**Pre-Draft $** is sourced directly from DraftSharks auction values (PPR, 12-team, $200 budget).
For players not in DraftSharks the value uses a Points-Above-Replacement (PAR) model:

1. **Bayesian projection** — past PPG is shrunk toward a position average using `w = games / (games + 8)`.
   Rookies use the positional average as a prior.
2. **PAR** — subtract replacement-level production (top 36 QB, 60 RB, 60 WR, 24 TE available) to get surplus value.
3. **Auction conversion** — PAR surplus is scaled so the total budget pool (budget × teams) is fully allocated.
4. **Live repricing** — once picks are recorded, each position's factor (actual ÷ projected median) adjusts
   remaining values; a global budget factor shrinks/inflates all values proportionally.
        """)

# ─── Tab 2: Live Draft Input ──────────────────────────────────────────────────

with tab_live:
    st.header("Live Draft — Record Picks")

    if repriced is None:
        st.info("Load projections first (sidebar).")
    else:
        available = reprice(projections_display, state, exclude_drafted=False)
        avail_undrafted = available[~available["player_id"].isin(state.drafted_ids())]

        with st.form("record_pick"):
            st.subheader("Record a Pick")
            col1, col2, col3 = st.columns(3)
            with col1:
                player_options = avail_undrafted["player_name"].tolist()
                selected_player = st.selectbox("Player", options=player_options)
            with col2:
                # Task 3: owner dropdown
                winning_team = st.selectbox("Winning Team", options=OWNERS)
            with col3:
                price_paid = st.number_input("Price Paid ($)", min_value=1, max_value=500, value=1)

            submitted = st.form_submit_button("✅ Record Pick")
            if submitted and selected_player and winning_team:
                row = avail_undrafted[avail_undrafted["player_name"] == selected_player].iloc[0]
                pick = DraftResult(
                    player_id=str(row["player_id"]),
                    player_name=selected_player,
                    position=row["position"],
                    team=winning_team,
                    actual_price=float(price_paid),
                    projected_value=float(row["auction_value"]),
                )
                state.record_pick(pick)
                st.success(f"Recorded: {selected_player} → {winning_team} for ${price_paid}")

        # ── Task 1 & 2: Manage existing picks ────────────────────────────────
        if state.results:
            st.subheader("Manage Picks")
            picks_df = state.picks_df()
            picks_df["#"] = range(1, len(picks_df) + 1)
            st.dataframe(
                picks_df[["#", "player_name", "position", "team", "actual_price"]].rename(
                    columns={"player_name": "Player", "position": "Pos",
                             "team": "Owner", "actual_price": "$ Paid"}
                ),
                use_container_width=True,
                hide_index=True,
                height=250,
            )

            mgmt_c1, mgmt_c2 = st.columns(2)

            with mgmt_c1:
                with st.form("delete_pick_form"):
                    st.caption("**Delete a pick**")
                    del_options = [f"{r.player_name} (${r.actual_price:.0f})" for r in state.results]
                    del_choice  = st.selectbox("Select pick to delete", options=del_options, key="del_pick")
                    del_submit  = st.form_submit_button("🗑️ Delete Pick")
                    if del_submit:
                        idx = del_options.index(del_choice)
                        pid = state.results[idx].player_id
                        state.delete_pick(pid)
                        st.success(f"Deleted: {del_choice}")
                        st.rerun()

            with mgmt_c2:
                with st.form("edit_pick_form"):
                    st.caption("**Correct a price**")
                    edit_options = [f"{r.player_name} (${r.actual_price:.0f})" for r in state.results]
                    edit_choice  = st.selectbox("Select pick to edit", options=edit_options, key="edit_pick")
                    new_price    = st.number_input("New price ($)", min_value=1, max_value=500, value=1)
                    edit_submit  = st.form_submit_button("✏️ Update Price")
                    if edit_submit:
                        idx = edit_options.index(edit_choice)
                        pid = state.results[idx].player_id
                        state.edit_pick_price(pid, float(new_price))
                        st.success(f"Updated {edit_choice} → ${new_price}")
                        st.rerun()

        st.subheader("Position Market Trends")
        mkt = position_market_summary(state)
        if mkt.empty:
            st.info("No picks recorded yet.")
        else:
            st.dataframe(mkt, use_container_width=True)
            fig2 = px.bar(
                mkt, x="Position", y="Factor",
                title="Position Discount / Premium vs Projection",
                labels={"Factor": "Actual / Projected ratio"},
                color="Factor",
                color_continuous_scale=["red", "white", "green"],
                color_continuous_midpoint=1.0,
            )
            fig2.add_hline(y=1.0, line_dash="dash", line_color="gray")
            st.plotly_chart(fig2, use_container_width=True)

# ─── Tab 3: Teams ─────────────────────────────────────────────────────────────

with tab_teams:
    st.header("Team Budgets & Rosters")

    if not state.team_budgets:
        st.info("No picks recorded yet.")
    else:
        team_data = []
        for team, remaining in state.team_budgets.items():
            picks = state.team_rosters.get(team, [])
            spent = sum(p.actual_price for p in picks)
            team_data.append({
                "Team":          team,
                "Spent ($)":     spent,
                "Remaining ($)": remaining,
                "Picks":         len(picks),
            })
        st.dataframe(pd.DataFrame(team_data), use_container_width=True)

        for team, picks in state.team_rosters.items():
            with st.expander(f"{team} — {len(picks)} picks"):
                pick_rows = [
                    {
                        "Player":         p.player_name,
                        "Pos":            p.position,
                        "Price ($)":      p.actual_price,
                        "Projected ($)":  p.projected_value,
                        "Value":          round(p.actual_price / max(p.projected_value, 1), 2),
                    }
                    for p in picks
                ]
                st.dataframe(pd.DataFrame(pick_rows), use_container_width=True)

    # ── Tasks 11 & 12: My Budget Tracker ─────────────────────────────────────
    st.divider()
    st.subheader("My Budget Plan")
    st.caption("Enter target MIN/MAX spend per slot. ACTUAL is filled from picks recorded to 'Me'.")

    my_picks = state.team_rosters.get("Me", [])

    # Group my picks by position (assign to slots in order)
    pos_spend: dict[str, list[float]] = {}
    for p in my_picks:
        pos_spend.setdefault(p.position, []).append(p.actual_price)

    plan_rows = []
    for slot, (lo, hi) in DEFAULT_BUDGET_PLAN.items():
        # Derive base position from slot name (e.g. "RB1" → "RB")
        base_pos = "".join(c for c in slot if c.isalpha()).rstrip("12345").replace("/", "")
        actual = None
        if base_pos in pos_spend and pos_spend[base_pos]:
            actual = pos_spend[base_pos].pop(0)

        plan_rows.append({
            "Slot":   slot,
            "MIN $":  lo,
            "MAX $":  hi,
            "ACTUAL": f"${actual:.0f}" if actual is not None else "—",
            "vs MIN": ("✅" if actual and actual >= lo else ("⚠️ Low" if actual else "")) ,
            "vs MAX": ("⚠️ Over" if actual and actual > hi else ("✅" if actual else "")),
        })

    # Totals
    starters = {k: v for k, v in DEFAULT_BUDGET_PLAN.items()
                if k not in ("RB3","RB4","RB5","WR4","WR5","QB2","RB/WR","RB/WR (2)")}
    bench    = {k: v for k, v in DEFAULT_BUDGET_PLAN.items() if k not in starters}
    total_lo = sum(v[0] for v in DEFAULT_BUDGET_PLAN.values())
    total_hi = sum(v[1] for v in DEFAULT_BUDGET_PLAN.values())
    total_actual = sum(p.actual_price for p in my_picks)
    plan_rows.append({"Slot": "─── Total ───", "MIN $": total_lo, "MAX $": total_hi,
                      "ACTUAL": f"${total_actual:.0f}" if my_picks else "—", "vs MIN": "", "vs MAX": ""})

    plan_df = pd.DataFrame(plan_rows)
    st.dataframe(plan_df, use_container_width=True, hide_index=True)

# ─── Tab 4: Results / Analysis ────────────────────────────────────────────────

with tab_results:
    st.header("Auction Results")

    picks_df = state.picks_df()
    if picks_df.empty:
        st.info("No picks recorded yet.")
    else:
        picks_df["value_ratio"] = (
            picks_df["actual_price"] / picks_df["projected_value"].clip(lower=1)
        ).round(2)

        st.subheader("All Picks")
        st.dataframe(
            picks_df[["player_name", "position", "team", "actual_price", "projected_value", "value_ratio"]],
            use_container_width=True,
            column_config={
                "actual_price":    st.column_config.NumberColumn("Price ($)", format="$%.0f"),
                "projected_value": st.column_config.NumberColumn("Projected ($)", format="$%.1f"),
                "value_ratio":     st.column_config.NumberColumn("Ratio"),
            },
        )

        fig3 = px.scatter(
            picks_df,
            x="projected_value", y="actual_price",
            color="position", text="player_name",
            title="Actual vs Projected Price",
            labels={"projected_value": "Projected ($)", "actual_price": "Actual ($)"},
        )
        max_val = max(picks_df["projected_value"].max(), picks_df["actual_price"].max())
        fig3.add_shape(type="line", x0=0, y0=0, x1=max_val, y1=max_val,
                       line=dict(dash="dash", color="gray"))
        st.plotly_chart(fig3, use_container_width=True)

# ─── Tab 5: Player Profile ────────────────────────────────────────────────────

with tab_player:
    st.header("Player Profile")

    if repriced is None:
        st.info("Load projections first (sidebar).")
    else:
        all_for_profile = reprice(projections_display, state, exclude_drafted=False)
        skill = all_for_profile[all_for_profile["position"].isin(["QB", "RB", "WR", "TE", "K", "DST"])].copy()
        player_names = skill.sort_values("auction_value", ascending=False)["player_name"].tolist()

        default_name = st.session_state.get("profile_player") or player_names[0]
        default_idx  = player_names.index(default_name) if default_name in player_names else 0

        selected = st.selectbox(
            "Search / select a player",
            options=player_names,
            index=default_idx,
        )
        st.session_state.profile_player = selected

        render_player_panel(selected, all_for_profile, schedule_df, ppr, STATS_PATH)

# ─── Tab navigation: JS click ─────────────────────────────────────────────────

if st.session_state.get("nav_to_profile"):
    st.session_state.nav_to_profile = False
    components.html(
        """
        <script>
        (function tryClick(attempts) {
            var tabs = window.parent.document.querySelectorAll('[data-baseweb="tab"]');
            if (tabs && tabs.length > 4) {
                tabs[4].click();
            } else if (attempts > 0) {
                setTimeout(function() { tryClick(attempts - 1); }, 80);
            }
        })(20);
        </script>
        """,
        height=0,
    )
