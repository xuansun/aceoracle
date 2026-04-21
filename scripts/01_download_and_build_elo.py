"""
AceOracle — Step 1: Download Sackmann ATP data & compute Elo ratings
=====================================================================
Run this first. It will:
  1. Clone the Sackmann tennis_atp repo (or pull latest)
  2. Parse all ATP match CSVs from 2000–present
  3. Compute Elo ratings (overall + per-surface)
  4. Output player_elo.csv and matches_enriched.csv

Usage:
  python 01_download_and_build_elo.py

Requires: pandas, numpy
"""

import os
import subprocess
import glob
import pandas as pd
import numpy as np
from datetime import datetime

# === CONFIG ===
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
REPO_URL = "https://github.com/JeffSackmann/tennis_atp.git"
REPO_DIR = os.path.join(DATA_DIR, "tennis_atp")
START_YEAR = 2000  # enough history for stable Elo
K_FACTOR = 32      # standard Elo K-factor
INITIAL_ELO = 1500

# Surface mapping (Sackmann uses these values)
SURFACE_MAP = {
    "Hard": "hard",
    "Clay": "clay",
    "Grass": "grass",
    "Carpet": "hard",  # carpet courts are closest to hard
}


def clone_or_pull_repo():
    """Clone the Sackmann ATP repo, or pull latest if it already exists."""
    os.makedirs(DATA_DIR, exist_ok=True)
    if os.path.exists(REPO_DIR):
        print("📂 Repo exists, pulling latest...")
        subprocess.run(["git", "-C", REPO_DIR, "pull"], check=True)
    else:
        print("📥 Cloning Sackmann ATP dataset...")
        subprocess.run(["git", "clone", REPO_URL, REPO_DIR], check=True)
    print("✅ Data ready\n")


def load_matches():
    """Load all ATP match CSVs from START_YEAR to present."""
    current_year = datetime.now().year
    all_dfs = []

    for year in range(START_YEAR, current_year + 1):
        filepath = os.path.join(REPO_DIR, f"atp_matches_{year}.csv")
        if os.path.exists(filepath):
            df = pd.read_csv(filepath, low_memory=False)
            df["source_year"] = year
            all_dfs.append(df)
            print(f"  Loaded {year}: {len(df)} matches")

    matches = pd.concat(all_dfs, ignore_index=True)
    print(f"\n📊 Total matches loaded: {len(matches)}")
    return matches


def clean_matches(matches):
    """Clean and prepare match data for Elo computation."""
    # Keep only completed matches with known winner/loser
    matches = matches.dropna(subset=["winner_id", "loser_id", "tourney_date"])

    # Parse date (format: YYYYMMDD)
    matches["date"] = pd.to_datetime(matches["tourney_date"], format="%Y%m%d", errors="coerce")
    matches = matches.dropna(subset=["date"])

    # Normalize surface
    matches["surface_clean"] = matches["surface"].map(SURFACE_MAP).fillna("hard")

    # Sort chronologically (critical for Elo computation)
    matches = matches.sort_values("date").reset_index(drop=True)

    # Select columns we need
    cols = [
        "date", "tourney_id", "tourney_name", "surface_clean",
        "round", "best_of",
        "winner_id", "winner_name", "winner_rank",
        "loser_id", "loser_name", "loser_rank",
        "score",
        # Match stats (available from ~1991)
        "w_ace", "w_df", "w_svpt", "w_1stIn", "w_1stWon", "w_2ndWon",
        "w_SvGms", "w_bpSaved", "w_bpFaced",
        "l_ace", "l_df", "l_svpt", "l_1stIn", "l_1stWon", "l_2ndWon",
        "l_SvGms", "l_bpSaved", "l_bpFaced",
        "source_year",
    ]
    available_cols = [c for c in cols if c in matches.columns]
    matches = matches[available_cols]

    print(f"✅ Cleaned matches: {len(matches)}")
    return matches


def compute_elo(matches):
    """
    Compute Elo ratings for all players.
    
    Returns:
      - elo_overall: dict of player_id -> current overall Elo
      - elo_surface: dict of (player_id, surface) -> current surface Elo
      - matches with Elo columns added (pre-match Elos for both players)
    """
    elo_overall = {}   # player_id -> elo
    elo_surface = {}   # (player_id, surface) -> elo
    match_count = {}   # player_id -> number of matches played

    # Pre-allocate columns for match Elo snapshots
    w_elo_pre = np.zeros(len(matches))
    l_elo_pre = np.zeros(len(matches))
    w_elo_surf_pre = np.zeros(len(matches))
    l_elo_surf_pre = np.zeros(len(matches))

    print("\n⚡ Computing Elo ratings...")
    for i, row in matches.iterrows():
        w_id = row["winner_id"]
        l_id = row["loser_id"]
        surface = row["surface_clean"]

        # Get current Elos (or initialize)
        w_elo = elo_overall.get(w_id, INITIAL_ELO)
        l_elo = elo_overall.get(l_id, INITIAL_ELO)
        w_surf = elo_surface.get((w_id, surface), INITIAL_ELO)
        l_surf = elo_surface.get((l_id, surface), INITIAL_ELO)

        # Record pre-match Elos
        w_elo_pre[i] = w_elo
        l_elo_pre[i] = l_elo
        w_elo_surf_pre[i] = w_surf
        l_elo_surf_pre[i] = l_surf

        # Expected scores
        exp_w = 1 / (1 + 10 ** ((l_elo - w_elo) / 400))
        exp_l = 1 - exp_w
        exp_w_surf = 1 / (1 + 10 ** ((l_surf - w_surf) / 400))
        exp_l_surf = 1 - exp_w_surf

        # Adaptive K-factor: higher for players with fewer matches
        w_matches = match_count.get(w_id, 0)
        l_matches = match_count.get(l_id, 0)
        k_w = K_FACTOR * 2 if w_matches < 30 else K_FACTOR
        k_l = K_FACTOR * 2 if l_matches < 30 else K_FACTOR

        # Update overall Elo (winner scored 1, loser scored 0)
        elo_overall[w_id] = w_elo + k_w * (1 - exp_w)
        elo_overall[l_id] = l_elo + k_l * (0 - exp_l)

        # Update surface Elo
        elo_surface[(w_id, surface)] = w_surf + k_w * (1 - exp_w_surf)
        elo_surface[(l_id, surface)] = l_surf + k_l * (0 - exp_l_surf)

        # Track match count
        match_count[w_id] = w_matches + 1
        match_count[l_id] = l_matches + 1

        if (i + 1) % 25000 == 0:
            print(f"  Processed {i + 1}/{len(matches)} matches...")

    matches["w_elo_pre"] = w_elo_pre
    matches["l_elo_pre"] = l_elo_pre
    matches["w_elo_surf_pre"] = w_elo_surf_pre
    matches["l_elo_surf_pre"] = l_elo_surf_pre

    print(f"✅ Elo computed for {len(elo_overall)} players\n")
    return elo_overall, elo_surface, match_count, matches


def build_player_table(elo_overall, elo_surface, match_count, matches):
    """Build a player lookup table with current Elo ratings."""
    # Get the latest name for each player
    latest_names = (
        matches.sort_values("date")
        .groupby("winner_id")["winner_name"]
        .last()
        .to_dict()
    )
    # Also get names from loser column for players who haven't won recently
    loser_names = (
        matches.sort_values("date")
        .groupby("loser_id")["loser_name"]
        .last()
        .to_dict()
    )
    latest_names.update({k: v for k, v in loser_names.items() if k not in latest_names})

    rows = []
    for pid, elo in elo_overall.items():
        name = latest_names.get(pid, f"Player_{pid}")
        n_matches = match_count.get(pid, 0)
        elo_hard = elo_surface.get((pid, "hard"), INITIAL_ELO)
        elo_clay = elo_surface.get((pid, "clay"), INITIAL_ELO)
        elo_grass = elo_surface.get((pid, "grass"), INITIAL_ELO)

        rows.append({
            "player_id": int(pid),
            "name": name,
            "elo_overall": round(elo, 1),
            "elo_hard": round(elo_hard, 1),
            "elo_clay": round(elo_clay, 1),
            "elo_grass": round(elo_grass, 1),
            "total_matches": n_matches,
        })

    players = pd.DataFrame(rows)
    players = players.sort_values("elo_overall", ascending=False).reset_index(drop=True)
    return players


def validate_elo(players):
    """Quick sanity check: top 20 by Elo should include known top players."""
    print("🏆 Top 20 players by overall Elo:")
    print("-" * 60)
    top = players.head(20)
    for _, row in top.iterrows():
        print(
            f"  {row['name']:25s}  "
            f"Overall: {row['elo_overall']:7.1f}  "
            f"Hard: {row['elo_hard']:7.1f}  "
            f"Clay: {row['elo_clay']:7.1f}  "
            f"Grass: {row['elo_grass']:7.1f}  "
            f"({row['total_matches']} matches)"
        )
    print()


def check_prediction_accuracy(matches):
    """
    Backtest: how often does the higher-Elo player win?
    Only check matches from the last 2 full years for relevance.
    """
    recent = matches[matches["source_year"] >= (datetime.now().year - 2)].copy()
    recent = recent.dropna(subset=["w_elo_pre", "l_elo_pre"])

    # The winner always has w_elo_pre, loser has l_elo_pre
    # "Correct" prediction = winner had higher Elo pre-match
    overall_correct = (recent["w_elo_pre"] > recent["l_elo_pre"]).sum()
    overall_total = len(recent)
    overall_acc = overall_correct / overall_total if overall_total > 0 else 0

    surface_correct = (recent["w_elo_surf_pre"] > recent["l_elo_surf_pre"]).sum()
    surface_acc = surface_correct / overall_total if overall_total > 0 else 0

    print(f"📈 Backtest accuracy (last 2 years, {overall_total} matches):")
    print(f"  Overall Elo:  {overall_acc:.1%}")
    print(f"  Surface Elo:  {surface_acc:.1%}")
    print()

    # Per-surface breakdown
    for surface in ["hard", "clay", "grass"]:
        subset = recent[recent["surface_clean"] == surface]
        if len(subset) == 0:
            continue
        acc = (subset["w_elo_surf_pre"] > subset["l_elo_surf_pre"]).mean()
        print(f"  {surface.capitalize():8s} surface Elo accuracy: {acc:.1%} ({len(subset)} matches)")

    print()


def main():
    # Step 1: Get the data
    clone_or_pull_repo()

    # Step 2: Load and clean
    raw = load_matches()
    matches = clean_matches(raw)

    # Step 3: Compute Elo
    elo_overall, elo_surface, match_count, matches = compute_elo(matches)

    # Step 4: Build player table
    players = build_player_table(elo_overall, elo_surface, match_count, matches)

    # Step 5: Validate
    validate_elo(players)
    check_prediction_accuracy(matches)

    # Step 6: Save outputs
    out_dir = os.path.join(DATA_DIR, "processed")
    os.makedirs(out_dir, exist_ok=True)

    players_path = os.path.join(out_dir, "player_elo.csv")
    matches_path = os.path.join(out_dir, "matches_enriched.csv")

    players.to_csv(players_path, index=False)
    matches.to_csv(matches_path, index=False)

    print(f"💾 Saved: {players_path} ({len(players)} players)")
    print(f"💾 Saved: {matches_path} ({len(matches)} matches)")
    print()
    print("✅ Step 1 complete! Next: run 02_train_prediction_model.py")


if __name__ == "__main__":
    main()
