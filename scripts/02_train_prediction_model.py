"""
AceOracle — Step 2: Train prediction model
===========================================
Run after 01_download_and_build_elo.py.

Uses the enriched match data to train an XGBoost model that predicts
match outcomes based on:
  - Elo delta (overall + surface-specific)
  - Head-to-head record
  - Recent form (win% in last 10 matches)
  - Fatigue (days since last match, matches in last 30 days)
  - Serve/return stats
  - Age, rank

Outputs: models/xgb_match_predictor.json + accuracy report

Usage:
  python 02_train_prediction_model.py

Requires: pandas, numpy, scikit-learn, xgboost
  pip install pandas numpy scikit-learn xgboost
"""

import os
import json
import pandas as pd
import numpy as np
from datetime import timedelta
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import accuracy_score, log_loss, brier_score_loss

# Try xgboost, fall back to sklearn gradient boosting
try:
    from xgboost import XGBClassifier
    USE_XGB = True
    print("✅ Using XGBoost")
except ImportError:
    from sklearn.ensemble import GradientBoostingClassifier
    USE_XGB = False
    print("⚠️  XGBoost not found, using sklearn GradientBoosting")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")

TRAIN_START = 2005   # enough history for stable features
TRAIN_END = 2023     # train through 2023
VAL_YEARS = [2024, 2025]  # validate on recent data


def load_data():
    """Load enriched match data from Step 1."""
    path = os.path.join(DATA_DIR, "processed", "matches_enriched.csv")
    df = pd.read_csv(path, parse_dates=["date"], low_memory=False)
    print(f"📊 Loaded {len(df)} matches")
    return df


def compute_h2h_features(df):
    """For each match, compute head-to-head record between the two players."""
    print("  Computing H2H features...")
    h2h_wins = np.zeros(len(df))
    h2h_losses = np.zeros(len(df))
    h2h_surface_wins = np.zeros(len(df))
    h2h_surface_losses = np.zeros(len(df))

    # Track H2H: (player_a, player_b) -> [a_wins, b_wins]
    h2h_record = {}
    h2h_surface_record = {}

    for i, row in df.iterrows():
        w_id = row["winner_id"]
        l_id = row["loser_id"]
        surface = row["surface_clean"]

        # Canonical key (smaller id first)
        key = (min(w_id, l_id), max(w_id, l_id))
        s_key = (min(w_id, l_id), max(w_id, l_id), surface)

        # Get current record before this match
        rec = h2h_record.get(key, [0, 0])
        s_rec = h2h_surface_record.get(s_key, [0, 0])

        # From winner's perspective
        if w_id == key[0]:
            h2h_wins[i] = rec[0]
            h2h_losses[i] = rec[1]
            h2h_surface_wins[i] = s_rec[0]
            h2h_surface_losses[i] = s_rec[1]
            rec[0] += 1
            s_rec[0] += 1
        else:
            h2h_wins[i] = rec[1]
            h2h_losses[i] = rec[0]
            h2h_surface_wins[i] = s_rec[1]
            h2h_surface_losses[i] = s_rec[0]
            rec[1] += 1
            s_rec[1] += 1

        h2h_record[key] = rec
        h2h_surface_record[s_key] = s_rec

    df["h2h_wins"] = h2h_wins
    df["h2h_losses"] = h2h_losses
    df["h2h_surface_wins"] = h2h_surface_wins
    df["h2h_surface_losses"] = h2h_surface_losses
    return df


def compute_form_and_fatigue(df):
    """Compute recent form and fatigue for the winner (from pre-match perspective)."""
    print("  Computing form & fatigue features...")

    # We'll track each player's recent results
    player_dates = {}    # player_id -> list of (date, won_bool)
    
    w_form = np.full(len(df), 0.5)
    l_form = np.full(len(df), 0.5)
    w_days_rest = np.full(len(df), 14.0)
    l_days_rest = np.full(len(df), 14.0)
    w_matches_30d = np.zeros(len(df))
    l_matches_30d = np.zeros(len(df))

    for i, row in df.iterrows():
        w_id = row["winner_id"]
        l_id = row["loser_id"]
        match_date = row["date"]

        for pid, is_winner in [(w_id, True), (l_id, False)]:
            history = player_dates.get(pid, [])

            # Recent form: win% in last 10 matches
            last_10 = history[-10:] if len(history) >= 10 else history
            if last_10:
                form = sum(1 for _, won in last_10 if won) / len(last_10)
            else:
                form = 0.5

            # Days since last match
            if history:
                last_date = history[-1][0]
                days_rest = (match_date - last_date).days
                days_rest = max(0, min(days_rest, 180))  # cap at 180
            else:
                days_rest = 14.0  # default for first match

            # Matches in last 30 days
            cutoff = match_date - timedelta(days=30)
            recent_count = sum(1 for d, _ in history if d >= cutoff)

            if is_winner:
                w_form[i] = form
                w_days_rest[i] = days_rest
                w_matches_30d[i] = recent_count
            else:
                l_form[i] = form
                l_days_rest[i] = days_rest
                l_matches_30d[i] = recent_count

        # Update history after computing features
        if w_id not in player_dates:
            player_dates[w_id] = []
        if l_id not in player_dates:
            player_dates[l_id] = []
        player_dates[w_id].append((match_date, True))
        player_dates[l_id].append((match_date, False))

    df["w_form_10"] = w_form
    df["l_form_10"] = l_form
    df["w_days_rest"] = w_days_rest
    df["l_days_rest"] = l_days_rest
    df["w_matches_30d"] = w_matches_30d
    df["l_matches_30d"] = l_matches_30d
    return df


def build_features(df):
    """
    Build feature matrix.
    
    IMPORTANT: We frame each match as "Player A vs Player B" and randomly
    assign who is A/B to avoid the model learning that the first player 
    always wins. The target is: did Player A win?
    """
    print("  Building feature matrix...")

    # Random assignment: 50% of the time, swap winner/loser columns
    np.random.seed(42)
    swap = np.random.random(len(df)) > 0.5

    features = pd.DataFrame(index=df.index)

    # Elo deltas (player A - player B)
    features["elo_delta"] = np.where(
        swap,
        df["l_elo_pre"] - df["w_elo_pre"],
        df["w_elo_pre"] - df["l_elo_pre"],
    )
    features["elo_surf_delta"] = np.where(
        swap,
        df["l_elo_surf_pre"] - df["w_elo_surf_pre"],
        df["w_elo_surf_pre"] - df["l_elo_surf_pre"],
    )

    # H2H advantage (A wins - A losses in H2H)
    features["h2h_advantage"] = np.where(
        swap,
        df["h2h_losses"] - df["h2h_wins"],  # from loser's perspective
        df["h2h_wins"] - df["h2h_losses"],   # from winner's perspective
    )
    features["h2h_surface_advantage"] = np.where(
        swap,
        df["h2h_surface_losses"] - df["h2h_surface_wins"],
        df["h2h_surface_wins"] - df["h2h_surface_losses"],
    )

    # Form delta
    features["form_delta"] = np.where(
        swap,
        df["l_form_10"] - df["w_form_10"],
        df["w_form_10"] - df["l_form_10"],
    )

    # Fatigue features
    features["rest_delta"] = np.where(
        swap,
        df["l_days_rest"] - df["w_days_rest"],
        df["w_days_rest"] - df["l_days_rest"],
    )
    features["matches_30d_delta"] = np.where(
        swap,
        df["l_matches_30d"] - df["w_matches_30d"],
        df["w_matches_30d"] - df["l_matches_30d"],
    )

    # Rank delta (lower rank = better; invert so positive = A is better)
    w_rank = df["winner_rank"].fillna(500)
    l_rank = df["loser_rank"].fillna(500)
    features["rank_delta"] = np.where(
        swap,
        w_rank - l_rank,    # loser's perspective: winner's rank - loser's rank
        l_rank - w_rank,    # winner's perspective: loser's rank - winner's rank
    )

    # Surface one-hot
    features["is_clay"] = (df["surface_clean"] == "clay").astype(int)
    features["is_grass"] = (df["surface_clean"] == "grass").astype(int)

    # Best of 5 flag
    features["is_bo5"] = (df["best_of"] == 5).astype(int)

    # Target: did Player A win?
    target = np.where(swap, 0, 1)  # if swapped, "A" is the loser

    # Year for train/val split
    features["year"] = df["source_year"]

    return features, target


def train_model(features, target):
    """Train the prediction model with time-series validation."""
    print("\n🏋️ Training model...")

    # Split by year
    train_mask = features["year"].between(TRAIN_START, TRAIN_END)
    val_mask = features["year"].isin(VAL_YEARS)

    feature_cols = [c for c in features.columns if c != "year"]

    X_train = features.loc[train_mask, feature_cols].values
    y_train = target[train_mask]
    X_val = features.loc[val_mask, feature_cols].values
    y_val = target[val_mask]

    print(f"  Train: {len(X_train)} matches ({TRAIN_START}–{TRAIN_END})")
    print(f"  Val:   {len(X_val)} matches ({VAL_YEARS})")

    if USE_XGB:
        model = XGBClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            colsample_bytree=0.8,
            eval_metric="logloss",
            random_state=42,
            verbosity=0,
        )
        model.fit(
            X_train, y_train,
            eval_set=[(X_val, y_val)],
            verbose=False,
        )
    else:
        model = GradientBoostingClassifier(
            n_estimators=300,
            max_depth=5,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )
        model.fit(X_train, y_train)

    # Evaluate
    val_probs = model.predict_proba(X_val)[:, 1]
    val_preds = (val_probs > 0.5).astype(int)
    acc = accuracy_score(y_val, val_preds)
    brier = brier_score_loss(y_val, val_probs)
    logloss = log_loss(y_val, val_probs)

    print(f"\n📈 Validation results:")
    print(f"  Accuracy:    {acc:.1%}")
    print(f"  Brier score: {brier:.4f} (lower is better)")
    print(f"  Log loss:    {logloss:.4f} (lower is better)")

    # Feature importance
    if USE_XGB:
        importance = dict(zip(feature_cols, model.feature_importances_))
    else:
        importance = dict(zip(feature_cols, model.feature_importances_))

    print(f"\n🔍 Feature importance:")
    for feat, imp in sorted(importance.items(), key=lambda x: -x[1]):
        bar = "█" * int(imp * 50)
        print(f"  {feat:28s} {imp:.3f} {bar}")

    return model, feature_cols, {
        "accuracy": round(acc, 4),
        "brier_score": round(brier, 4),
        "log_loss": round(logloss, 4),
        "train_years": f"{TRAIN_START}-{TRAIN_END}",
        "val_years": str(VAL_YEARS),
        "n_train": len(X_train),
        "n_val": len(X_val),
        "feature_cols": feature_cols,
    }


def save_model(model, feature_cols, metrics):
    """Save model and metadata."""
    os.makedirs(MODEL_DIR, exist_ok=True)

    if USE_XGB:
        model_path = os.path.join(MODEL_DIR, "xgb_match_predictor.json")
        model.save_model(model_path)
    else:
        import pickle
        model_path = os.path.join(MODEL_DIR, "gb_match_predictor.pkl")
        with open(model_path, "wb") as f:
            pickle.dump(model, f)

    meta_path = os.path.join(MODEL_DIR, "model_metadata.json")
    with open(meta_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print(f"\n💾 Model saved: {model_path}")
    print(f"💾 Metadata saved: {meta_path}")


def main():
    # Load enriched data
    df = load_data()

    # Filter to reasonable date range
    df = df[df["source_year"] >= TRAIN_START].copy().reset_index(drop=True)

    # Compute features
    df = compute_h2h_features(df)
    df = compute_form_and_fatigue(df)
    features, target = build_features(df)

    # Train
    model, feature_cols, metrics = train_model(features, target)

    # Save
    save_model(model, feature_cols, metrics)

    print("\n✅ Step 2 complete! Next: run 03_setup_api.sh to scaffold the Hono API")


if __name__ == "__main__":
    main()
