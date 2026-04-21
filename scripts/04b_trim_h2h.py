import pandas as pd
import json
import os

DATA = os.path.join(os.path.dirname(__file__), "..", "data", "processed")
matches = pd.read_csv(os.path.join(DATA, "matches_enriched.csv"), low_memory=False)

# Find players active since 2022
recent = matches[matches["source_year"] >= 2022]
active_ids = set(recent["winner_id"].astype(int).astype(str)) | set(recent["loser_id"].astype(int).astype(str))
print(f"Active players (since 2022): {len(active_ids)}")

h2h_path = os.path.join(os.path.dirname(__file__), "..", "api", "src", "h2h.json")
with open(h2h_path) as f:
    h2h = json.load(f)

trimmed = {}
for key, val in h2h.items():
    p1, p2 = key.split(":")
    if p1 in active_ids or p2 in active_ids:
        trimmed[key] = val

with open(h2h_path, "w") as f:
    json.dump(trimmed, f)

print(f"Trimmed: {len(h2h)} -> {len(trimmed)} matchups")
