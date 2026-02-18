"""Valuation models and signal generation."""


def rcp_to_house_fair_value(dem_generic: float, rep_generic: float) -> float:
    """Generic ballot D-R margin → implied Dem House control probability.

    Historical rough model: each 1pt of generic ballot margin ≈ 4-5 House seats.
    Dems need ~218 seats for control. Even margin → ~50/50; D+5 → strong Dem.
    """
    margin = dem_generic - rep_generic
    house_prob = max(10.0, min(90.0, 50 + (margin * 6)))
    return round(house_prob, 1)


# Placeholder — replace with a real model based on state-level Senate polling
SENATE_RCP_FAIR = 58.0


def build_signals(
    house_kalshi: float | None,
    house_source: str,
    house_fair: float,
    senate_kalshi: float | None,
    senate_source: str,
) -> list[dict]:
    """Compare Kalshi prices against fair values and generate signals."""
    signals = []

    if house_kalshi is not None:
        edge = round(house_kalshi - house_fair, 1)
        signals.append(
            {
                "Market": "Dem House Control",
                "Kalshi %": house_kalshi,
                "Source": house_source,
                "RCP Fair %": house_fair,
                "Edge %": edge,
                "Signal": (
                    "🟢 Strong Buy"
                    if edge > 8
                    else "🔴 Strong Sell" if edge < -8 else "🟡 Watch"
                ),
            }
        )

    if senate_kalshi is not None:
        edge = round(senate_kalshi - SENATE_RCP_FAIR, 1)
        signals.append(
            {
                "Market": "Rep Senate Control",
                "Kalshi %": senate_kalshi,
                "Source": senate_source,
                "RCP Fair %": SENATE_RCP_FAIR,
                "Edge %": edge,
                "Signal": (
                    "🔴 Sell"
                    if edge > 5
                    else "🟢 Buy" if edge < -5 else "🟡 Watch"
                ),
            }
        )

    return signals