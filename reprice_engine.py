"""
reprice_engine.py
Live auction repricing: tracks draft results and adjusts projected
auction values based on how each position is actually going.

Used by the Streamlit dashboard — not meant to be run standalone.
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field


# ─── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class DraftResult:
    """One completed pick in the auction."""
    player_id:    str
    player_name:  str
    position:     str
    team:         str          # fantasy team that won the bid
    actual_price: float
    projected_value: float     # what we had projected before the draft


@dataclass
class AuctionState:
    """Mutable state of the in-progress auction."""
    teams:         int   = 12
    budget:        float = 200.0
    results:       list[DraftResult] = field(default_factory=list)
    team_budgets:  dict[str, float]  = field(default_factory=dict)   # remaining budget per team
    team_rosters:  dict[str, list]   = field(default_factory=dict)   # picks per team

    def record_pick(self, pick: DraftResult):
        self.results.append(pick)
        # Deduct from team budget
        if pick.team not in self.team_budgets:
            self.team_budgets[pick.team] = self.budget
        self.team_budgets[pick.team] -= pick.actual_price
        # Track roster
        if pick.team not in self.team_rosters:
            self.team_rosters[pick.team] = []
        self.team_rosters[pick.team].append(pick)

    def drafted_ids(self) -> set[str]:
        return {r.player_id for r in self.results}

    def picks_df(self) -> pd.DataFrame:
        if not self.results:
            return pd.DataFrame()
        return pd.DataFrame([vars(r) for r in self.results])

    def _rebuild_state(self):
        """Recompute team_budgets and team_rosters from self.results."""
        self.team_budgets = {}
        self.team_rosters = {}
        for pick in self.results:
            if pick.team not in self.team_budgets:
                self.team_budgets[pick.team] = self.budget
            self.team_budgets[pick.team] -= pick.actual_price
            if pick.team not in self.team_rosters:
                self.team_rosters[pick.team] = []
            self.team_rosters[pick.team].append(pick)

    def delete_pick(self, player_id: str) -> bool:
        """Remove a pick by player_id; returns True if found."""
        before = len(self.results)
        self.results = [r for r in self.results if r.player_id != player_id]
        if len(self.results) == before:
            return False
        self._rebuild_state()
        return True

    def edit_pick_price(self, player_id: str, new_price: float) -> bool:
        """Update the price of an existing pick; returns True if found."""
        for r in self.results:
            if r.player_id == player_id:
                r.actual_price = new_price
                self._rebuild_state()
                return True
        return False


# ─── Repricing ────────────────────────────────────────────────────────────────

MIN_PICKS_FOR_ADJUSTMENT = 3   # need at least this many sold at a position before adjusting


def compute_position_factors(state: AuctionState) -> dict[str, float]:
    """
    For each position, compute how actual prices compare to projected values.
    factor > 1 means position is going for a premium; factor < 1 means a discount.
    Returns {position: factor}
    """
    picks = state.picks_df()
    if picks.empty:
        return {}

    # Filter to picks where we had a meaningful projection
    picks = picks[picks["projected_value"] > 1].copy()
    picks["ratio"] = picks["actual_price"] / picks["projected_value"]

    factors = {}
    for pos, group in picks.groupby("position"):
        if len(group) >= MIN_PICKS_FOR_ADJUSTMENT:
            # Median is more robust than mean to a single outlier bid
            factors[pos] = float(group["ratio"].median())

    return factors


def reprice(
    projections: pd.DataFrame,
    state: AuctionState,
    remaining_budget_adjustment: bool = True,
    exclude_drafted: bool = True,
) -> pd.DataFrame:
    """
    Adjust projected auction values for all unsold players.

    Steps:
    1. Apply per-position discount/premium from results so far.
    2. Optionally scale for remaining budget (if teams are running low overall).

    Returns a copy of projections with updated 'repriced_value' column.
    Pass exclude_drafted=False to include already-drafted players (e.g. for board display).
    """
    drafted = state.drafted_ids()
    df = projections[~projections["player_id"].isin(drafted)].copy() if exclude_drafted else projections.copy()

    if df.empty:
        return df

    factors = compute_position_factors(state)
    df["position_factor"] = df["position"].map(factors).fillna(1.0)

    # Budget adjustment: if total remaining dollars < total remaining spots × $1,
    # scale down all values proportionally so the math still closes
    budget_factor = 1.0
    if remaining_budget_adjustment and state.team_budgets:
        total_remaining_budget = sum(state.team_budgets.values()) + (
            (state.teams - len(state.team_budgets)) * state.budget
        )
        # Estimate remaining spots: assume average roster size minus picks taken per team
        avg_picks = len(state.results) / max(state.teams, 1)
        remaining_slots_per_team = 9 - avg_picks   # 9 starters; rough estimate
        total_remaining_slots = max(remaining_slots_per_team * state.teams, 1)

        # Total remaining "hittable" budget (above $1 minimums)
        hittable_remaining = total_remaining_budget - total_remaining_slots
        original_hittable  = df["auction_value"].clip(lower=1).sum() - len(df)
        if original_hittable > 0:
            budget_factor = hittable_remaining / original_hittable
            budget_factor = float(np.clip(budget_factor, 0.5, 2.0))  # cap swings

    df["repriced_value"] = (
        df["auction_value"] * df["position_factor"] * budget_factor
    ).round(1).clip(lower=1)

    # Sort by repriced value
    df = df.sort_values("repriced_value", ascending=False).reset_index(drop=True)
    return df


# ─── Summary Helpers (used by dashboard) ──────────────────────────────────────

def position_market_summary(state: AuctionState) -> pd.DataFrame:
    """
    Returns a small DataFrame summarising how each position is trading
    vs projection — useful for the dashboard's position pricing display.
    """
    picks = state.picks_df()
    if picks.empty:
        return pd.DataFrame(columns=["Position", "Picks", "Avg $ Paid", "Avg Projected", "Factor"])

    picks = picks[picks["projected_value"] > 1].copy()
    picks["ratio"] = picks["actual_price"] / picks["projected_value"]

    summary_rows = []
    for pos, grp in picks.groupby("position"):
        summary_rows.append({
            "Position":       pos,
            "Picks":          len(grp),
            "Avg $ Paid":     round(grp["actual_price"].mean(), 1),
            "Avg Projected":  round(grp["projected_value"].mean(), 1),
            "Factor":         round(grp["ratio"].median(), 2),
        })

    return pd.DataFrame(summary_rows).sort_values("Position")
