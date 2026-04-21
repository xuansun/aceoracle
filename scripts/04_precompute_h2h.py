"""Precompute H2H records into a compact JSON file for the API."""
import pandas as pd
import json
import os

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
matches = pd.read_csv(os.path.join(DATA, "matches_enriched.csv"), low_memory=False)

h2h = {}
for _, row in matches.iterrows():
    w = str(int(row["winner_id"]))
    l = str(int(row["loser_id"]))
    surface = row["surface_clean"]
    key = f"{min(w,l)}:{max(w,l)}"

    if key not in h2h:
        h2h[key] = {"overall": [0, 0], "hard": [0, 0], "clay": [0, 0], "grass": [0, 0]}

    idx = 0 if w == min(w, l) else 1
    h2h[key]["overall"][idx] += 1
    if surface in h2h[key]:
        h2h[key][surface][idx] += 1

out_path = os.path.join(os.path.dirname(__file__), "..", "api", "src", "h2h.json")
with open(out_path, "w") as f:
    json.dump(h2h, f)

print(f"✅ H2H data: {len(h2h)} matchups saved to {out_path}")
