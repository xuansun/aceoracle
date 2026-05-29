from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Input, Label, Static

from edge import compute_edge
from predictor import TennisPredictor

# ---------------------------------------------------------------------------
# Mock data for demo mode (no API key)
# ---------------------------------------------------------------------------

_MOCK_MARKETS = [
    {
        "ticker": "ATP-WIMB-SINNER-WIN",
        "title": "Will Jannik Sinner beat Carlos Alcaraz at Wimbledon?",
        "subtitle": "Men's Singles Final",
        "yes_ask": 48,
        "yes_bid": 46,
        "last_price": 47,
        "volume": 15000,
        "open_interest": 8200,
    },
    {
        "ticker": "ATP-FO-DJOKOVIC-WIN",
        "title": "Novak Djokovic vs. Rafael Nadal — will Djokovic win?",
        "subtitle": "Roland Garros QF",
        "yes_ask": 62,
        "yes_bid": 60,
        "last_price": 61,
        "volume": 9800,
        "open_interest": 4100,
    },
    {
        "ticker": "WTA-USO-SWIATEK-WIN",
        "title": "Will Iga Swiatek beat Aryna Sabalenka at the US Open?",
        "subtitle": "Women's Final",
        "yes_ask": 55,
        "yes_bid": 53,
        "last_price": 54,
        "volume": 7300,
        "open_interest": 3600,
    },
]

_MOCK_PREDICTIONS = [
    {"p1_name": "Jannik Sinner", "p2_name": "Carlos Alcaraz", "p1_prob": 0.52, "surface": "grass", "method": "xgb", "h2h_overall": [3, 5], "h2h_surface": [1, 2]},
    {"p1_name": "Novak Djokovic", "p2_name": "Rafael Nadal", "p1_prob": 0.41, "surface": "clay", "method": "xgb", "h2h_overall": [30, 29], "h2h_surface": [10, 20]},
    {"p1_name": "Iga Swiatek", "p2_name": "Aryna Sabalenka", "p1_prob": 0.60, "surface": "hard", "method": "xgb", "h2h_overall": [8, 4], "h2h_surface": [4, 2]},
]


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class MarketRow:
    ticker: str
    title: str
    p1_name: str | None
    p2_name: str | None
    surface: str
    p1_prob: float
    yes_price: int
    edge_data: dict
    prediction: dict
    raw_market: dict
    consensus_prob: float | None = None
    live_score: str = ""


# ---------------------------------------------------------------------------
# Bet confirmation modal
# ---------------------------------------------------------------------------


class BetModal(ModalScreen[tuple[str, int] | None]):
    """Simple modal to confirm a bet. Returns (side, count) or None on cancel."""

    CSS = """
    BetModal {
        align: center middle;
    }
    #dialog {
        background: $surface;
        border: tall $primary;
        padding: 1 2;
        width: 60;
        height: auto;
    }
    #dialog Label {
        margin-bottom: 1;
    }
    #bet-input {
        margin-bottom: 1;
    }
    #button-row {
        height: auto;
        margin-top: 1;
    }
    Button {
        margin-right: 1;
    }
    """

    def __init__(self, market_row: MarketRow) -> None:
        super().__init__()
        self._row = market_row

    def compose(self) -> ComposeResult:
        row = self._row
        ed = row.edge_data
        side = ed.get("best_side") or "yes"
        kelly_pct = ed.get("kelly_fraction", 0.0) * 100.0
        with Container(id="dialog"):
            yield Label(f"[bold]{row.title}[/bold]")
            yield Label(
                f"Side: [bold]{side.upper()}[/bold]  |  "
                f"Edge: {ed.get('yes_edge' if side == 'yes' else 'no_edge', 0)*100:+.1f}%  |  "
                f"Half-Kelly: {kelly_pct:.1f}%"
            )
            yield Label("Contracts to buy (integer):")
            yield Input(value="1", id="bet-input")
            with Horizontal(id="button-row"):
                yield Button("Confirm", variant="success", id="confirm")
                yield Button("Cancel", variant="error", id="cancel")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        inp = self.query_one("#bet-input", Input)
        try:
            count = int(inp.value.strip())
        except ValueError:
            count = 1
        side = self._row.edge_data.get("best_side") or "yes"
        self.dismiss((side, max(1, count)))


# ---------------------------------------------------------------------------
# Main dashboard
# ---------------------------------------------------------------------------


class BettingDashboard(App):
    CSS = """
    Screen {
        layout: vertical;
    }
    #top-bar {
        height: 1;
        background: $boost;
        color: $text;
        padding: 0 1;
        content-align: left middle;
    }
    #body {
        layout: vertical;
        height: 1fr;
    }
    #table-container {
        height: 60%;
        border: solid $primary;
    }
    #detail-panel {
        height: 40%;
        border: solid $secondary;
        padding: 1 2;
        overflow-y: auto;
    }
    DataTable {
        height: 1fr;
    }
    """

    TITLE = "AceOracle — Kalshi Betting Dashboard"
    BINDINGS = [
        Binding("r", "refresh", "Refresh"),
        Binding("b", "bet", "Bet"),
        Binding("q", "quit", "Quit"),
    ]
    REFRESH_INTERVAL = 120  # seconds

    markets_data: reactive[list[MarketRow]] = reactive([], layout=True)
    selected_idx: reactive[int] = reactive(0)
    balance_cents: reactive[int] = reactive(0)
    _status_msg: reactive[str] = reactive("Loading…")

    def __init__(
        self,
        key_id: str | None = None,
        private_key_path: str | None = None,
    ) -> None:
        super().__init__()
        self._client: Any = None
        self._predictor: TennisPredictor | None = None
        self._demo_mode = not bool(key_id and private_key_path)
        self._init_error: str = ""

        if not self._demo_mode:
            try:
                from client import KalshiClient
                self._client = KalshiClient(key_id, private_key_path)  # type: ignore[arg-type]
            except Exception as e:
                self._demo_mode = True
                self._init_error = str(e)

        try:
            self._predictor = TennisPredictor()
        except Exception:
            self._predictor = None

        # Auto-bet config (all off by default)
        self._autobet_enabled = os.getenv("AUTO_BET", "false").lower() == "true"
        self._autobet_threshold = float(os.getenv("AUTO_BET_EDGE_THRESHOLD", "0.08"))
        self._autobet_max_contracts = int(os.getenv("AUTO_BET_MAX_CONTRACTS", "10"))
        self._bet_tickers: set[str] = set()  # track markets already bet this session

        # Optional external data clients
        self._odds_client = None
        self._tennis_client = None
        self._odds_cache: list[dict] = []
        self._odds_cache_ts: float = 0.0
        odds_key = os.getenv("ODDS_API_KEY")
        tennis_key = os.getenv("TENNIS_RAPIDAPI_KEY")
        if odds_key:
            try:
                from odds_client import OddsApiClient
                self._odds_client = OddsApiClient(odds_key)
            except Exception:
                pass
        if tennis_key:
            try:
                from tennis_client import TennisApiClient
                self._tennis_client = TennisApiClient(tennis_key)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="top-bar")
        with Container(id="body"):
            with Container(id="table-container"):
                table = DataTable(id="markets-table", cursor_type="row")
                table.add_columns(
                    "Match", "Surf", "Our%", "Mkt%", "Edge", "Side", "Kelly%",
                    "Consensus", "Score",
                )
                yield table
            yield Static(id="detail-panel", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self.refresh_data(), exclusive=True)
        self.set_interval(self.REFRESH_INTERVAL, self.action_refresh)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def refresh_data(self) -> None:
        self._set_status("Fetching…")
        rows: list[MarketRow] = []

        if self._demo_mode:
            rows = self._build_demo_rows()
            err_suffix = f" — {self._init_error}" if self._init_error else ""
            self._set_status(
                f"[yellow]DEMO MODE — set KALSHI_API_KEY to connect{err_suffix}[/yellow]"
            )
        else:
            rows = await asyncio.get_event_loop().run_in_executor(None, self._fetch_live_rows)

        # Enrich rows with external data sources (odds + live scores)
        rows = await asyncio.get_event_loop().run_in_executor(None, self._enrich_rows, rows)

        self.markets_data = rows
        self.update_table()
        if rows:
            self._update_detail(0)

    def _fetch_live_rows(self) -> list[MarketRow]:
        rows: list[MarketRow] = []
        try:
            bal = self._client.get_balance()
            self.balance_cents = bal.get("balance", 0)
        except Exception:
            pass

        try:
            markets = self._client.get_tennis_markets()
        except Exception as exc:
            self._set_status(f"[red]API error: {exc}[/red]")
            return rows

        if not markets:
            self._set_status("[yellow]No tennis markets found — showing demo[/yellow]")
            return self._build_demo_rows()

        for m in markets:
            p1, p2, surface = self._client.parse_players_from_market(m)
            yes_price = m.get("last_price") or m.get("yes_ask") or 50

            pred: dict = {}
            if self._predictor and p1 and p2:
                try:
                    pred = self._predictor.predict(p1, p2, surface=surface or "hard")
                except Exception:
                    pred = {}

            p1_prob = pred.get("p1_prob", 0.5)
            edge_data = compute_edge(p1_prob, int(yes_price))

            rows.append(
                MarketRow(
                    ticker=m.get("ticker", ""),
                    title=m.get("title", ""),
                    p1_name=pred.get("p1_name") or p1 or "Player 1",
                    p2_name=pred.get("p2_name") or p2 or "Player 2",
                    surface=surface or "hard",
                    p1_prob=p1_prob,
                    yes_price=int(yes_price),
                    edge_data=edge_data,
                    prediction=pred,
                    raw_market=m,
                )
            )

        self._set_status(f"[green]Loaded {len(rows)} markets[/green]")
        return rows

    def _enrich_rows(self, rows: list[MarketRow]) -> list[MarketRow]:
        """Annotate rows with bookmaker consensus prob and live scores, then auto-bet."""
        # Fetch bookmaker odds (cache for 15 min to save API quota)
        odds_data: list[dict] = []
        if self._odds_client is not None:
            now = time.monotonic()
            if now - self._odds_cache_ts > 900 or not self._odds_cache:
                try:
                    self._odds_cache = self._odds_client.get_tennis_odds()
                    self._odds_cache_ts = now
                except Exception:
                    pass
            odds_data = self._odds_cache

        # Fetch today's tennis matches (live score data)
        tennis_matches: list[dict] = []
        if self._tennis_client is not None:
            try:
                tennis_matches = self._tennis_client.get_today_matches()
            except Exception:
                pass

        for row in rows:
            p1 = row.p1_name or ""
            p2 = row.p2_name or ""

            # Consensus prob from bookmakers
            if self._odds_client is not None and odds_data and p1 and p2:
                odds_match = self._odds_client.find_match(p1, p2, odds_data)
                if odds_match is not None:
                    row.consensus_prob = self._odds_client.get_consensus_prob(odds_match, p1)

            # Live score from API-Tennis
            if self._tennis_client is not None and tennis_matches and p1 and p2:
                tennis_match = self._tennis_client.find_match(p1, p2, tennis_matches)
                if tennis_match is not None:
                    row.live_score = self._tennis_client.get_score_str(tennis_match)

            # Auto-bet if conditions met
            self._maybe_autobet(row)

        return rows

    def _maybe_autobet(self, row: MarketRow) -> None:
        if not self._autobet_enabled or self._demo_mode:
            return
        if row.ticker in self._bet_tickers:
            return
        if not row.edge_data or not row.edge_data.get("has_edge"):
            return
        if row.edge_data.get("best_edge", row.edge_data.get("yes_edge", 0)) < self._autobet_threshold:
            return

        side = row.edge_data.get("best_side")
        if not side:
            return
        yes_price = row.raw_market.get("yes_ask", row.raw_market.get("last_price", 50))
        price = yes_price if side == "yes" else (100 - yes_price)
        kelly = row.edge_data.get("kelly_fraction", 0)
        balance = self.balance_cents

        spend_cents = int(kelly * balance)
        contracts = min(self._autobet_max_contracts, max(1, spend_cents // max(price, 1)))

        try:
            self._client.place_order(row.ticker, side, contracts)
            self._bet_tickers.add(row.ticker)
            self._log_bet(row, side, contracts)
            best_edge = row.edge_data.get("best_edge", row.edge_data.get("yes_edge", 0))
            self._set_status(
                f"Auto-bet {contracts}x {side.upper()} on {row.p1_name} "
                f"({best_edge:.1%} edge)"
            )
        except Exception as e:
            self._set_status(f"Auto-bet failed: {e}")

    def _log_bet(self, row: MarketRow, side: str, contracts: int) -> None:
        from datetime import datetime
        log_path = Path(__file__).parent / "bets.log"
        best_edge = row.edge_data.get("best_edge", row.edge_data.get("yes_edge", 0))
        line = (
            f"{datetime.now().isoformat()} | {row.ticker} | {side.upper()} | "
            f"{contracts} contracts | edge={best_edge:.1%} | "
            f"p1={row.p1_name} vs p2={row.p2_name} | our_prob={row.p1_prob:.3f}\n"
        )
        with open(log_path, "a") as f:
            f.write(line)

    def _build_demo_rows(self) -> list[MarketRow]:
        rows: list[MarketRow] = []
        for mock, pred in zip(_MOCK_MARKETS, _MOCK_PREDICTIONS):
            yes_price = mock.get("last_price", 50)
            p1_prob = pred["p1_prob"]
            edge_data = compute_edge(p1_prob, int(yes_price))
            p1, p2, surface = self._parse_demo_surface(mock)
            rows.append(
                MarketRow(
                    ticker=mock["ticker"],
                    title=mock["title"],
                    p1_name=pred["p1_name"],
                    p2_name=pred["p2_name"],
                    surface=pred["surface"],
                    p1_prob=p1_prob,
                    yes_price=int(yes_price),
                    edge_data=edge_data,
                    prediction=pred,
                    raw_market=mock,
                )
            )
        return rows

    def _parse_demo_surface(self, mock: dict) -> tuple[str | None, str | None, str]:
        title = (mock.get("title") or "").lower()
        if "clay" in title or "roland garros" in title or "french" in title:
            return None, None, "clay"
        elif "grass" in title or "wimbledon" in title:
            return None, None, "grass"
        return None, None, "hard"

    # ------------------------------------------------------------------
    # UI updates
    # ------------------------------------------------------------------

    def _set_status(self, msg: str) -> None:
        self._status_msg = msg
        try:
            bal_str = f"${self.balance_cents / 100:.2f}" if self.balance_cents else "—"
            prefix = "[DEMO] " if self._demo_mode else ""
            self.query_one("#top-bar", Static).update(
                f"{prefix}Balance: {bal_str}  |  {msg}"
            )
        except Exception:
            pass

    def update_table(self) -> None:
        try:
            table = self.query_one("#markets-table", DataTable)
        except Exception:
            return
        table.clear()
        for row in self.markets_data:
            ed = row.edge_data
            edge_val = ed.get("yes_edge" if ed.get("best_side") == "yes" else "no_edge", 0.0)
            edge_pct = edge_val * 100.0

            if edge_pct > 3:
                edge_cell = Text(f"{edge_pct:+.1f}%", style="bold green")
            elif edge_pct > 0:
                edge_cell = Text(f"{edge_pct:+.1f}%", style="yellow")
            else:
                edge_cell = Text(f"{edge_pct:+.1f}%", style="red")

            best_side = ed.get("best_side") or "—"
            side_cell = Text(best_side.upper() if best_side != "—" else "—",
                             style="bold cyan" if best_side != "—" else "dim")

            match_label = f"{row.p1_name} vs {row.p2_name}"
            if len(match_label) > 38:
                match_label = match_label[:35] + "…"

            consensus_str = (
                f"{row.consensus_prob*100:.1f}%" if row.consensus_prob is not None else "—"
            )
            score_str = row.live_score if row.live_score else "—"

            table.add_row(
                match_label,
                row.surface[:4].title(),
                f"{row.p1_prob*100:.1f}%",
                f"{row.yes_price}¢",
                edge_cell,
                side_cell,
                f"{ed.get('kelly_fraction', 0)*100:.1f}%",
                consensus_str,
                score_str,
            )

    def _update_detail(self, idx: int) -> None:
        try:
            panel = self.query_one("#detail-panel", Static)
        except Exception:
            return

        if not self.markets_data or idx >= len(self.markets_data):
            panel.update("No market selected.")
            return

        row = self.markets_data[idx]
        ed = row.edge_data
        pred = row.prediction
        h2h_ov = pred.get("h2h_overall", [0, 0])
        h2h_su = pred.get("h2h_surface", [0, 0])

        yes_edge_pct = ed.get("yes_edge", 0.0) * 100.0
        no_edge_pct = ed.get("no_edge", 0.0) * 100.0
        side = ed.get("best_side") or "none"
        kelly_pct = ed.get("kelly_fraction", 0.0) * 100.0

        detail = (
            f"[bold]{row.title}[/bold]\n"
            f"[dim]Ticker:[/dim] {row.ticker}\n\n"
            f"[bold]Players[/bold]\n"
            f"  {row.p1_name}  vs  {row.p2_name}  ({row.surface})\n\n"
            f"[bold]Probabilities[/bold]\n"
            f"  Our model ({pred.get('method', '?')}): "
            f"[cyan]{row.p1_prob*100:.1f}%[/cyan] / "
            f"[cyan]{(1-row.p1_prob)*100:.1f}%[/cyan]\n"
            f"  Market (YES): [yellow]{row.yes_price}¢[/yellow]  "
            f"(implied {row.yes_price:.0f}%)\n\n"
            f"[bold]Edge[/bold]\n"
            f"  YES edge: [{'green' if yes_edge_pct > 0 else 'red'}]{yes_edge_pct:+.2f}%[/]\n"
            f"  NO  edge: [{'green' if no_edge_pct > 0 else 'red'}]{no_edge_pct:+.2f}%[/]\n"
            f"  Best side: [bold]{side.upper()}[/bold]  |  "
            f"Half-Kelly: [bold]{kelly_pct:.2f}%[/bold]\n\n"
            f"[bold]H2H[/bold]\n"
            f"  Overall:  {row.p1_name} {h2h_ov[0]}–{h2h_ov[1]} {row.p2_name}\n"
            f"  {row.surface.title()}: {h2h_su[0]}–{h2h_su[1]}\n"
        )
        panel.update(detail)

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.selected_idx = event.cursor_row
        self._update_detail(event.cursor_row)

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        self.selected_idx = event.cursor_row
        self._update_detail(event.cursor_row)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_refresh(self) -> None:
        self.run_worker(self.refresh_data(), exclusive=True)

    async def action_bet(self) -> None:
        if not self.markets_data:
            return
        idx = self.selected_idx
        if idx >= len(self.markets_data):
            return
        row = self.markets_data[idx]

        if not row.edge_data.get("has_edge"):
            self._set_status("[yellow]No edge on selected market — bet cancelled[/yellow]")
            return

        result: tuple[str, int] | None = await self.push_screen_wait(BetModal(row))
        if result is None:
            self._set_status("Bet cancelled.")
            return

        side, count = result

        if self._demo_mode or self._client is None:
            self._set_status(
                f"[yellow]DEMO: Would place {count}x {side.upper()} on {row.ticker}[/yellow]"
            )
            return

        try:
            resp = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: self._client.place_order(row.ticker, side, count),
            )
            self._set_status(
                f"[green]Order placed: {count}x {side.upper()} on {row.ticker} — "
                f"order_id={resp.get('order', {}).get('order_id', '?')}[/green]"
            )
        except Exception as exc:
            self._set_status(f"[red]Order failed: {exc}[/red]")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).parent / ".env")  # always load from script dir
    key_id = os.getenv("KALSHI_KEY_ID")
    key_path_raw = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private.pem")
    # Resolve relative paths to script dir so dashboard works from any CWD
    key_path = (
        str(Path(__file__).parent / key_path_raw)
        if not os.path.isabs(key_path_raw)
        else key_path_raw
    )
    app = BettingDashboard(key_id=key_id, private_key_path=key_path)
    app.run()
