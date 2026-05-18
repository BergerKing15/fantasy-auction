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
import plotly.express as px

# Allow imports from parent directory when running from dashboard/
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from projection_engine import run as run_projections
from reprice_engine import AuctionState, DraftResult, reprice, position_market_summary

# ─── Cached Loaders ───────────────────────────────────────────────────────────
# @st.cache_data memoizes by arguments — Streamlit won't re-read the CSV on
# every rerun (which happens on every user interaction).

@st.cache_data
def load_projections_csv(path: str) -> pd.DataFrame:
    return pd.read_csv(path)

@st.cache_data(show_spinner="Running projection engine...")
def compute_projections(ppr: float, projection_season: int) -> pd.DataFrame:
    return run_projections(ppr=ppr, projection_season=projection_season)

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

PROJECTIONS_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "projections.csv")

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

# Reprice once per render and share across tabs
repriced = reprice(projections, state) if projections is not None else None

# ─── Main Tabs ────────────────────────────────────────────────────────────────

tab_board, tab_live, tab_teams, tab_results = st.tabs([
    "📋 Player Board",
    "🔴 Live Draft",
    "👥 Teams",
    "📊 Results",
])

# ─── Tab 1: Player Board ──────────────────────────────────────────────────────

with tab_board:
    st.header("Player Board")

    if repriced is None:
        st.info("Click **Load / Refresh Projections** in the sidebar to get started.")
        st.stop()

    # Filters
    col1, col2, col3 = st.columns(3)
    with col1:
        pos_filter = st.multiselect(
            "Position", options=["QB", "RB", "WR", "TE"], default=["QB", "RB", "WR", "TE"]
        )
    with col2:
        max_price = st.number_input("Max $ Value (filter)", min_value=1, max_value=300, value=200)
    with col3:
        show_drafted = st.checkbox("Show Drafted Players", value=False)

    display = repriced.copy()
    if not show_drafted:
        display = display[~display["player_id"].isin(state.drafted_ids())]
    if pos_filter:
        display = display[display["position"].isin(pos_filter)]
    display = display[display["repriced_value"] <= max_price]

    # Rename for display — detect whatever fpts_YYYY column exists
    prev_col = next((c for c in display.columns if c.startswith("fpts_")), None)
    display_cols = {
        "player_name":    "Player",
        "position":       "Pos",
        "projected_fpts": "Proj Pts",
        **(  {prev_col: f"{prev_col[5:]} Actual"} if prev_col else {}),
        "auction_value":  "Pre-Draft $",
        "repriced_value": "Current $",
        "ppg":            "Hist PPG",
        "seasons":        "Seasons",
    }
    show = display[[c for c in display_cols if c in display.columns]].rename(columns=display_cols)
    for col in ["Proj Pts", f"{prev_col[5:]} Actual" if prev_col else ""]:
        if col in show.columns:
            show[col] = show[col].round(1)

    st.dataframe(
        show,
        use_container_width=True,
        height=600,
        column_config={
            "Current $":  st.column_config.NumberColumn(format="$%.1f"),
            "Pre-Draft $": st.column_config.NumberColumn(format="$%.1f"),
        },
    )

    # Value distribution chart
    if len(repriced) > 0:
        fig = px.bar(
            repriced[repriced["position"].isin(pos_filter or ["QB","RB","WR","TE"])].head(60),
            x="player_name", y="repriced_value", color="position",
            title="Top Players by Current Auction Value",
            labels={"repriced_value": "Auction Value ($)", "player_name": ""},
        )
        fig.update_layout(xaxis_tickangle=-45)
        st.plotly_chart(fig, use_container_width=True)

# ─── Tab 2: Live Draft Input ──────────────────────────────────────────────────

with tab_live:
    st.header("Live Draft — Record Picks")

    if repriced is None:
        st.info("Load projections first (sidebar).")
    else:
        available = repriced[~repriced["player_id"].isin(state.drafted_ids())]

        with st.form("record_pick"):
            st.subheader("Record a Pick")
            col1, col2, col3 = st.columns(3)
            with col1:
                player_options = available["player_name"].tolist()
                selected_player = st.selectbox("Player", options=player_options)
            with col2:
                winning_team = st.text_input("Winning Team (name)", placeholder="e.g. Team Alpha")
            with col3:
                price_paid = st.number_input("Price Paid ($)", min_value=1, max_value=500, value=1)

            submitted = st.form_submit_button("✅ Record Pick")
            if submitted and selected_player and winning_team:
                row = available[available["player_name"] == selected_player].iloc[0]
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

        # Position market summary
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
                "Team":         team,
                "Spent ($)":    spent,
                "Remaining ($)": remaining,
                "Picks":        len(picks),
            })
        st.dataframe(pd.DataFrame(team_data), use_container_width=True)

        # Team breakdown expander
        for team, picks in state.team_rosters.items():
            with st.expander(f"{team} — {len(picks)} picks"):
                pick_rows = [
                    {
                        "Player": p.player_name,
                        "Pos": p.position,
                        "Price ($)": p.actual_price,
                        "Projected ($)": p.projected_value,
                        "Value": round(p.actual_price / max(p.projected_value, 1), 2),
                    }
                    for p in picks
                ]
                st.dataframe(pd.DataFrame(pick_rows), use_container_width=True)

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
        # y=x reference line
        max_val = max(picks_df["projected_value"].max(), picks_df["actual_price"].max())
        fig3.add_shape(type="line", x0=0, y0=0, x1=max_val, y1=max_val,
                       line=dict(dash="dash", color="gray"))
        st.plotly_chart(fig3, use_container_width=True)
