"""
session_store.py
Save and restore a live draft session.

Streamlit holds everything in memory, so a page refresh, a laptop sleep, or a
dropped connection loses the entire auction. This module snapshots the parts
worth keeping — recorded picks, target/avoid tags, player notes, and the budget
plan — as JSON.

Two independent safety nets, because they fail in different situations:
  - autosave to a file beside the data (works locally; a container restart on
    Streamlit Cloud wipes it, and a read-only filesystem blocks it entirely)
  - an explicit download the user keeps (works everywhere, survives anything)

Kept free of Streamlit imports so it can be tested and reused on its own.
"""

import json
import os
import shutil

from reprice_engine import AuctionState

SNAPSHOT_VERSION = 1


# ─── Build / apply ────────────────────────────────────────────────────────────

def build_snapshot(
    state: AuctionState,
    targets: set | list | None = None,
    avoid: set | list | None = None,
    comments: dict | None = None,
    budget_plan: dict | None = None,
) -> dict:
    """Capture everything the user would hate to re-enter."""
    return {
        "version":     SNAPSHOT_VERSION,
        "auction":     state.to_dict(),
        "targets":     sorted(targets or []),
        "avoid":       sorted(avoid or []),
        "comments":    {k: v for k, v in (comments or {}).items() if v},
        "budget_plan": {k: list(v) for k, v in (budget_plan or {}).items()},
    }


def parse_snapshot(raw: str | bytes) -> dict:
    """Parse and validate a snapshot. Raises ValueError on anything unusable."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {exc}") from exc

    if not isinstance(data, dict) or "auction" not in data:
        raise ValueError("Not a draft-state file (no 'auction' key).")

    version = data.get("version")
    if version != SNAPSHOT_VERSION:
        raise ValueError(
            f"Snapshot version {version!r} — this build expects {SNAPSHOT_VERSION}."
        )

    # Fail here rather than halfway through restoring into the live session
    AuctionState.from_dict(data["auction"])
    return data


def snapshot_to_json(snapshot: dict) -> str:
    """Stable JSON text — sorted keys so identical state compares equal."""
    return json.dumps(snapshot, indent=2, sort_keys=True)


# ─── File I/O ─────────────────────────────────────────────────────────────────

def save_snapshot(path: str, snapshot: dict) -> bool:
    """Write a snapshot to disk. Returns False if the filesystem won't allow it
    (read-only deploys), which is a normal condition, not an error."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(snapshot_to_json(snapshot))
        os.replace(tmp, path)   # atomic — a crash mid-write can't truncate the real file
        return True
    except OSError:
        return False


def load_snapshot(path: str) -> dict | None:
    """Read a snapshot from disk. Returns None if absent; raises ValueError if
    present but unreadable, so the caller can warn instead of silently
    overwriting a file the user may still want."""
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        raise ValueError(f"Could not read {path}: {exc}") from exc
    return parse_snapshot(raw)


def backup_existing(path: str) -> str | None:
    """Copy the current save aside before a destructive action. Returns the
    backup path, or None if there was nothing to back up."""
    if not os.path.exists(path):
        return None
    backup = f"{os.path.splitext(path)[0]}.backup.json"
    try:
        shutil.copyfile(path, backup)
        return backup
    except OSError:
        return None


def describe_picks(snapshot: dict) -> str:
    """One-line human summary of a snapshot, for confirming a restore."""
    picks = snapshot.get("auction", {}).get("results", [])
    spent = sum(float(p.get("actual_price", 0)) for p in picks)
    return f"{len(picks)} pick{'' if len(picks) == 1 else 's'}, ${spent:,.0f} spent"
