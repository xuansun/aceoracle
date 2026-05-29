from __future__ import annotations

import requests


class OddsApiClient:
    BASE_URL = "https://api.the-odds-api.com/v4"
    TENNIS_SPORTS = ["tennis_atp", "tennis_wta"]

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.session = requests.Session()

    def get_tennis_odds(self) -> list[dict]:
        """Fetch upcoming + live tennis matches with h2h odds from all bookmakers."""
        results = []
        for sport in self.TENNIS_SPORTS:
            try:
                resp = self.session.get(
                    f"{self.BASE_URL}/sports/{sport}/odds",
                    params={
                        "apiKey": self.api_key,
                        "regions": "us,eu",
                        "markets": "h2h",
                        "oddsFormat": "decimal",
                    },
                    timeout=10,
                )
                if resp.ok:
                    results.extend(resp.json())
            except Exception:
                pass
        return results

    def get_consensus_prob(self, match: dict, player_name: str) -> float | None:
        """
        Average implied probability for player_name across all bookmakers.
        Strips vig (normalizes so both sides sum to 1).
        Returns None if player not found in any bookmaker.
        """
        probs = []
        name_lower = player_name.lower()
        for bm in match.get("bookmakers", []):
            for market in bm.get("markets", []):
                if market.get("key") != "h2h":
                    continue
                outcomes = market.get("outcomes", [])
                if len(outcomes) != 2:
                    continue
                # Find which outcome matches this player
                for i, outcome in enumerate(outcomes):
                    if name_lower in outcome["name"].lower() or outcome["name"].lower() in name_lower:
                        other = outcomes[1 - i]
                        # Remove vig: normalize
                        total = 1 / outcome["price"] + 1 / other["price"]
                        prob = (1 / outcome["price"]) / total
                        probs.append(prob)
                        break
        return round(sum(probs) / len(probs), 4) if probs else None

    def find_match(self, p1_name: str, p2_name: str, matches: list[dict]) -> dict | None:
        """Find a match by player names using partial matching."""
        p1_lower = p1_name.lower().split()[-1]  # last name
        p2_lower = p2_name.lower().split()[-1]
        for m in matches:
            home = m.get("home_team", "").lower()
            away = m.get("away_team", "").lower()
            if (p1_lower in home or p1_lower in away) and (p2_lower in home or p2_lower in away):
                return m
        return None
