from __future__ import annotations

import base64
import re
import time
from typing import Any

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


class KalshiClient:
    BASE_URL = "https://api.kalshi.com/trade-api/v2"

    def __init__(self, key_id: str, private_key_path: str) -> None:
        self.key_id = key_id
        with open(private_key_path, "rb") as f:
            self._private_key = serialization.load_pem_private_key(
                f.read(), password=None, backend=default_backend()
            )
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _auth_headers(self, method: str, path: str) -> dict[str, str]:
        ts = str(int(time.time() * 1000))
        msg = f"{ts}{method.upper()}{path}"
        sig = self._private_key.sign(msg.encode(), padding.PKCS1v15(), hashes.SHA256())
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
        }

    def _get(self, path: str, params: dict | None = None) -> dict:
        # Build the signed path including query string
        full_path = f"/trade-api/v2{path}"
        if params:
            qs = "&".join(f"{k}={v}" for k, v in params.items() if v is not None)
            if qs:
                full_path = f"{full_path}?{qs}"
        resp = self.session.get(
            f"{self.BASE_URL}{path}",
            params=params,
            headers=self._auth_headers("GET", full_path),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        full_path = f"/trade-api/v2{path}"
        resp = self.session.post(
            f"{self.BASE_URL}{path}",
            json=body,
            headers=self._auth_headers("POST", full_path),
            timeout=10,
        )
        resp.raise_for_status()
        return resp.json()

    def get_balance(self) -> dict:
        return self._get("/portfolio/balance")

    def get_markets(self, status: str = "open", limit: int = 200, **kwargs) -> dict:
        params = {"status": status, "limit": limit, **kwargs}
        return self._get("/markets", params=params)

    def get_tennis_markets(self) -> list[dict]:
        tennis_keywords = {
            "tennis", "atp", "wta", "wimbledon", "roland garros",
            "us open", "australian open", "french open",
        }
        results: list[dict] = []

        try:
            data = self.get_markets(status="open", limit=200)
            for m in data.get("markets", []):
                combined = (
                    (m.get("title") or "")
                    + " " + (m.get("subtitle") or "")
                    + " " + (m.get("series_ticker") or "")
                    + " " + (m.get("category") or "")
                ).lower()
                if any(kw in combined for kw in tennis_keywords):
                    results.append(m)
        except Exception:
            pass

        seen = {m.get("ticker") for m in results}
        for series in ("TENNIS", "ATP", "WTA"):
            try:
                data = self._get(
                    "/events", params={"series_ticker": series, "limit": 100, "status": "open"}
                )
                for event in data.get("events", []):
                    for m in event.get("markets", []):
                        if m.get("ticker") not in seen:
                            results.append(m)
                            seen.add(m.get("ticker"))
            except Exception:
                pass

        return results

    def get_market(self, ticker: str) -> dict:
        return self._get(f"/markets/{ticker}")

    def get_positions(self) -> dict:
        return self._get("/portfolio/positions")

    def place_order(self, ticker: str, side: str, count: int, order_type: str = "market") -> dict:
        return self._post("/portfolio/orders", {
            "ticker": ticker,
            "side": side,
            "count": count,
            "type": order_type,
            "action": "buy",
        })

    def parse_players_from_market(
        self, market: dict
    ) -> tuple[str | None, str | None, str | None]:
        title = (market.get("title") or "") + " " + (market.get("subtitle") or "")

        _name = r"[A-Z][A-Za-záéíóúàèìòùäöüñ\-'\.]+"
        _full = rf"(?:{_name}(?:\s+{_name}){{0,2}})"
        patterns = [
            rf"Will ({_full})\s+(?:beat|defeat)\s+({_full})(?:\s+(?:in|at|during|to\s)|\?|$)",
            rf"({_full})\s+vs\.?\s+({_full})(?:\s*[-—?]|$)",
            rf"({_full})\s+to win (?:against|vs\.?)\s+({_full})(?:\?|$)",
        ]
        p1: str | None = None
        p2: str | None = None
        for pat in patterns:
            m = re.search(pat, title, re.IGNORECASE)
            if m:
                p1, p2 = m.group(1).strip(), m.group(2).strip()
                break

        title_lower = title.lower()
        if "roland garros" in title_lower or "clay" in title_lower or "french open" in title_lower:
            surface = "clay"
        elif "wimbledon" in title_lower or "grass" in title_lower:
            surface = "grass"
        else:
            surface = "hard"

        return p1, p2, surface
