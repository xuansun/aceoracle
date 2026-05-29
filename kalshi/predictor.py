from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import xgboost as xgb
    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False

_REPO_ROOT = Path(__file__).parent.parent
_ELO_PATH = _REPO_ROOT / "data" / "processed" / "player_elo.csv"
_H2H_PATH = _REPO_ROOT / "api" / "src" / "h2h.json"
_MODEL_PATH = _REPO_ROOT / "models" / "xgb_match_predictor.json"
_META_PATH = _REPO_ROOT / "models" / "model_metadata.json"

_FEATURE_COLS = [
    "elo_delta",
    "elo_surf_delta",
    "h2h_advantage",
    "h2h_surface_advantage",
    "form_delta",
    "rest_delta",
    "matches_30d_delta",
    "rank_delta",
    "is_clay",
    "is_grass",
    "is_bo5",
]


def _normalize(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = name.encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", name.lower())


class TennisPredictor:
    def __init__(self) -> None:
        self._players: dict[str, dict] = {}
        self._name_index: dict[str, str] = {}
        self._last_name_index: dict[str, str] = {}
        self._h2h: dict[str, dict] = {}
        self.model: Any = None

        self._load_players()
        self._load_h2h()
        self._load_model()

    def _load_players(self) -> None:
        df = pd.read_csv(_ELO_PATH)
        for _, row in df.iterrows():
            pid = str(int(row["player_id"]))
            record = {
                "player_id": pid,
                "name": row["name"],
                "elo_overall": float(row["elo_overall"]),
                "elo_hard": float(row["elo_hard"]),
                "elo_clay": float(row["elo_clay"]),
                "elo_grass": float(row["elo_grass"]),
                "total_matches": int(row["total_matches"]),
            }
            self._players[pid] = record

            norm_full = _normalize(row["name"])
            self._name_index[norm_full] = pid

            parts = row["name"].split()
            if parts:
                last = _normalize(parts[-1])
                if last not in self._last_name_index:
                    self._last_name_index[last] = pid

    def _load_h2h(self) -> None:
        with open(_H2H_PATH) as f:
            self._h2h = json.load(f)

    def _load_model(self) -> None:
        if not _XGB_AVAILABLE:
            return
        try:
            self.model = xgb.Booster()
            self.model.load_model(str(_MODEL_PATH))
        except Exception:
            self.model = None

    def find_player(self, query: str) -> tuple[str | None, dict | None]:
        # exact player_id
        if query in self._players:
            return query, self._players[query]

        norm = _normalize(query)

        # exact normalized full name
        if norm in self._name_index:
            pid = self._name_index[norm]
            return pid, self._players[pid]

        # exact last name
        if norm in self._last_name_index:
            pid = self._last_name_index[norm]
            return pid, self._players[pid]

        # partial match on normalized full name
        for key, pid in self._name_index.items():
            if norm in key or key in norm:
                return pid, self._players[pid]

        return None, None

    def _elo_for_surface(self, player: dict, surface: str) -> float:
        surface_map = {"hard": "elo_hard", "clay": "elo_clay", "grass": "elo_grass"}
        key = surface_map.get(surface, "elo_overall")
        val = player.get(key, 0.0)
        if val == 0.0:
            val = player["elo_overall"]
        return val

    def _h2h_stats(
        self, p1_id: str, p2_id: str, surface: str
    ) -> tuple[list[int], list[int], list[int]]:
        min_id = min(int(p1_id), int(p2_id))
        max_id = max(int(p1_id), int(p2_id))
        key = f"{min_id}:{max_id}"
        entry = self._h2h.get(key)
        if entry is None:
            return [0, 0], [0, 0], [0, 0]

        # index 0 = lower_id wins, index 1 = higher_id wins
        # p1 is the "first" player from caller's perspective
        p1_is_min = int(p1_id) == min_id

        overall = entry.get("overall", [0, 0])
        surface_data = entry.get(surface, entry.get("hard", [0, 0]))

        if p1_is_min:
            p1_overall = [overall[0], overall[1]]
            p1_surface = [surface_data[0], surface_data[1]]
        else:
            p1_overall = [overall[1], overall[0]]
            p1_surface = [surface_data[1], surface_data[0]]

        return p1_overall, p1_surface, overall

    def predict(
        self,
        p1_query: str,
        p2_query: str,
        surface: str = "hard",
        best_of: int = 3,
    ) -> dict:
        p1_id, p1 = self.find_player(p1_query)
        p2_id, p2 = self.find_player(p2_query)

        if p1 is None or p2 is None:
            return {
                "p1_name": p1_query,
                "p2_name": p2_query,
                "p1_prob": 0.5,
                "p2_prob": 0.5,
                "h2h_overall": [0, 0],
                "h2h_surface": [0, 0],
                "method": "unknown",
                "surface": surface,
                "error": f"Player not found: {p1_query if p1 is None else p2_query}",
            }

        p1_surf_elo = self._elo_for_surface(p1, surface)
        p2_surf_elo = self._elo_for_surface(p2, surface)
        elo_surf_delta = p1_surf_elo - p2_surf_elo
        elo_overall_delta = p1["elo_overall"] - p2["elo_overall"]

        p1_h2h, p1_h2h_surf, raw_overall = self._h2h_stats(p1_id, p2_id, surface)
        total_overall = sum(p1_h2h)
        total_surface = sum(p1_h2h_surf)

        h2h_advantage = (p1_h2h[0] / total_overall - 0.5) if total_overall >= 2 else 0.0
        h2h_surf_advantage = (p1_h2h_surf[0] / total_surface - 0.5) if total_surface >= 2 else 0.0

        is_clay = 1.0 if surface == "clay" else 0.0
        is_grass = 1.0 if surface == "grass" else 0.0
        is_bo5 = 1.0 if best_of == 5 else 0.0

        method = "elo"
        p1_prob: float

        if self.model is not None:
            features = np.array(
                [[
                    elo_overall_delta,
                    elo_surf_delta,
                    h2h_advantage,
                    h2h_surf_advantage,
                    0.0,  # form_delta — not available at prediction time
                    0.0,  # rest_delta
                    0.0,  # matches_30d_delta
                    0.0,  # rank_delta
                    is_clay,
                    is_grass,
                    is_bo5,
                ]],
                dtype=np.float32,
            )
            dmat = xgb.DMatrix(features, feature_names=_FEATURE_COLS)
            p1_prob = float(self.model.predict(dmat)[0])
            method = "xgb"
        else:
            p1_prob = 1.0 / (1.0 + 10.0 ** (-elo_surf_delta / 400.0))
            if total_overall >= 3:
                p1_prob += 0.08 * h2h_advantage
            p1_prob = max(0.05, min(0.95, p1_prob))

        return {
            "p1_name": p1["name"],
            "p2_name": p2["name"],
            "p1_prob": round(p1_prob, 4),
            "p2_prob": round(1.0 - p1_prob, 4),
            "h2h_overall": p1_h2h,
            "h2h_surface": p1_h2h_surf,
            "method": method,
            "surface": surface,
        }
