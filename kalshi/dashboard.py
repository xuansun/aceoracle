from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
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

        if not self._demo_mode:
            try:
                from client import KalshiClient
                self._client = KalshiClient(key_id, private_key_path)  # type: ignore[arg-type]
            except Exception:
                self._demo_mode = True

        try:
            self._predictor = TennisPredictor()
        except Exception:
            self._predictor = None

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
                    "Match", "Surf", "Our%", "Mkt%", "Edge", "Side", "Kelly%"
                )
                yield table
            yield Static(id="detail-panel", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        self.run_worker(self.refresh_data(), exclusive=True)

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    async def refresh_data(self) -> None:
        self._set_status("Fetching…")
        rows: list[MarketRow] = []

        if self._demo_mode:
            rows = self._build_demo_rows()
            self._set_status("[yellow]DEMO MODE — set KALSHI_API_KEY to connect[/yellow]")
        else:
            rows = await asyncio.get_event_loop().run_in_executor(None, self._fetch_live_rows)

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

            table.add_row(
                match_label,
                row.surface[:4].title(),
                f"{row.p1_prob*100:.1f}%",
                f"{row.yes_price}¢",
                edge_cell,
                side_cell,
                f"{ed.get('kelly_fraction', 0)*100:.1f}%",
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

    load_dotenv()
    key_id = os.getenv("KALSHI_KEY_ID")
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private.pem")
    app = BettingDashboard(key_id=key_id, private_key_path=key_path)
    app.run()
