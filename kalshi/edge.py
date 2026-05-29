from __future__ import annotations


def compute_edge(our_prob: float, yes_price_cents: int) -> dict:
    market_prob = yes_price_cents / 100.0
    yes_edge = our_prob - market_prob

    no_prob = 1.0 - our_prob
    no_price_cents = 100 - yes_price_cents
    no_edge = no_prob - no_price_cents / 100.0

    # Kelly for YES side: b = net odds per dollar risked
    b_yes = (100 - yes_price_cents) / yes_price_cents
    if b_yes > 0:
        kelly_yes = (b_yes * our_prob - (1.0 - our_prob)) / b_yes
    else:
        kelly_yes = 0.0

    # Kelly for NO side
    b_no = yes_price_cents / (100 - yes_price_cents) if yes_price_cents < 100 else 0.0
    if b_no > 0:
        kelly_no = (b_no * no_prob - our_prob) / b_no
    else:
        kelly_no = 0.0

    if yes_edge > 0 and yes_edge >= no_edge:
        best_side: str | None = "yes"
        kelly_fraction = max(0.0, kelly_yes / 2.0)
    elif no_edge > 0 and no_edge > yes_edge:
        best_side = "no"
        kelly_fraction = max(0.0, kelly_no / 2.0)
    else:
        best_side = None
        kelly_fraction = 0.0

    has_edge = best_side is not None

    return {
        "market_prob": round(market_prob, 4),
        "yes_edge": round(yes_edge, 4),
        "no_edge": round(no_edge, 4),
        "best_side": best_side,
        "kelly_fraction": round(kelly_fraction, 4),
        "has_edge": has_edge,
    }
