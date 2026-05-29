from __future__ import annotations

from datetime import date

import requests


class TennisApiClient:
    BASE_URL = "https://api-tennis.p.rapidapi.com"

    def __init__(self, rapidapi_key: str) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "X-RapidAPI-Key": rapidapi_key,
                "X-RapidAPI-Host": "api-tennis.p.rapidapi.com",
            }
        )

    def get_live_matches(self) -> list[dict]:
        """Get currently in-progress ATP/WTA matches."""
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/matches",
                params={"date": date.today().isoformat(), "status": "live"},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                return data.get("result", data) if isinstance(data, dict) else data
        except Exception:
            pass
        return []

    def get_today_matches(self) -> list[dict]:
        """Get all ATP/WTA matches scheduled today."""
        try:
            resp = self.session.get(
                f"{self.BASE_URL}/matches",
                params={"date": date.today().isoformat()},
                timeout=10,
            )
            if resp.ok:
                data = resp.json()
                return data.get("result", data) if isinstance(data, dict) else data
        except Exception:
            pass
        return []

    def find_match(self, p1_name: str, p2_name: str, matches: list[dict]) -> dict | None:
        """Find a match by player last names."""
        p1_last = p1_name.lower().split()[-1]
        p2_last = p2_name.lower().split()[-1]
        for m in matches:
            home = str(m.get("player_1_name", m.get("home", ""))).lower()
            away = str(m.get("player_2_name", m.get("away", ""))).lower()
            if (p1_last in home or p1_last in away) and (p2_last in home or p2_last in away):
                return m
        return None

    def get_score_str(self, match: dict) -> str:
        """Return a short score string like 'LIVE 6-3 4-2*' or 'Scheduled'."""
        status = str(match.get("status", "")).lower()
        if "live" in status or "progress" in status:
            score = match.get("score", match.get("current_score", ""))
            return f"LIVE {score}" if score else "LIVE"
        return match.get("status", "Scheduled")
