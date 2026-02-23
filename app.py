"""Kalshi 2026 Elections Dashboard — main Streamlit entrypoint."""

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv

from charts import build_price_chart, fetch_candlesticks
from kalshi_client import (
    COMBO_SERIES,
    HOUSE_SERIES,
    SENATE_SERIES,
    discover_all_markets,
    fetch_balance,
    fetch_market_price,
    fetch_positions,
    fetch_resting_orders,
    fetch_settlements,
    find_by_side,
    get_price_pct,
    kalshi_get,
    load_auth,
)
from models import build_signals, rcp_to_house_fair_value
from rcp import fetch_rcp_generic

load_dotenv()

st.set_page_config(page_title="Kalshi Elections • RCP Signals", layout="wide")
st.title("🗳️ Kalshi 2026 Elections Dashboard")
st.markdown("**RCP polls → Kalshi signals** | Auto-refreshes every 10 min")

# ====================== MARKET DISCOVERY ======================


@st.cache_data(ttl=120)
def _discover():
    return discover_all_markets()


def derive_from_combos(combo_markets):
    """Combo market (RR/RD/DR/DD) → implied marginal probabilities."""
    prices = {}
    for m in combo_markets:
        ticker = m["ticker"].upper()
        pct = get_price_pct(m)
        if pct is None:
            continue
        for suffix in ("RR", "RD", "DR", "DD"):
            if ticker.endswith(f"-{suffix}"):
                prices[suffix] = pct
                break

    if len(prices) < 4:
        return None

    return {
        "dem_house": round(prices["DR"] + prices["DD"], 1),
        "rep_house": round(prices["RR"] + prices["RD"], 1),
        "rep_senate": round(prices["RR"] + prices["DR"], 1),
        "dem_senate": round(prices["RD"] + prices["DD"], 1),
        "combos": prices,
    }


# ====================== LOAD DATA ======================
markets = _discover()
rcp_dem, rcp_rep = fetch_rcp_generic()
house_fair = rcp_to_house_fair_value(rcp_dem, rcp_rep)

combo_implied = derive_from_combos(markets["combo"])

house_direct = find_by_side(markets["house"], "-D")
senate_direct = find_by_side(markets["senate"], "-R")

# Determine Kalshi prices — prefer direct markets, fall back to combo-derived
house_kalshi = None
house_source = ""
if house_direct:
    house_kalshi = get_price_pct(house_direct)
    house_source = f"direct ({house_direct['ticker']})"
if house_kalshi is None and combo_implied:
    house_kalshi = combo_implied["dem_house"]
    house_source = "combo-implied (DR+DD)"

senate_kalshi = None
senate_source = ""
if senate_direct:
    senate_kalshi = get_price_pct(senate_direct)
    senate_source = f"direct ({senate_direct['ticker']})"
if senate_kalshi is None and combo_implied:
    senate_kalshi = combo_implied["rep_senate"]
    senate_source = "combo-implied (RR+DR)"

# ====================== SIDEBAR ======================
st.sidebar.markdown("### Direct Markets")
for m in markets["house"]:
    pct = get_price_pct(m)
    selected = " ✅" if house_direct and m["ticker"] == house_direct["ticker"] else ""
    st.sidebar.text(f"  {m['ticker']}: {pct}%{selected}")
for m in markets["senate"]:
    pct = get_price_pct(m)
    selected = (
        " ✅" if senate_direct and m["ticker"] == senate_direct["ticker"] else ""
    )
    st.sidebar.text(f"  {m['ticker']}: {pct}%{selected}")

if combo_implied:
    st.sidebar.markdown("**Combo-Implied:**")
    st.sidebar.text(f"  Dem House: {combo_implied['dem_house']}%")
    st.sidebar.text(f"  Rep Senate: {combo_implied['rep_senate']}%")

st.sidebar.markdown("---")
st.sidebar.caption(
    f"House: {len(markets['house'])} mkts | Senate: {len(markets['senate'])} mkts | Combos: {len(markets['combo'])} mkts"
)

# ====================== SIGNALS ======================
signals = build_signals(
    house_kalshi, house_source, house_fair, senate_kalshi, senate_source
)
df_signals = pd.DataFrame(signals) if signals else pd.DataFrame()
if not df_signals.empty:
    for col in ("Kalshi %", "RCP Fair %", "Edge %"):
        if col in df_signals.columns:
            df_signals[col] = df_signals[col].map(lambda x: round(x, 1))


def highlight_signal(row):
    if "Buy" in row["Signal"]:
        bg = "#0f4"
    elif "Sell" in row["Signal"]:
        bg = "#f44"
    else:
        bg = "#ffeb3b"
    return ["background-color: " + bg if col == "Signal" else "" for col in row.index]


# ====================== MAIN UI ======================
col1, col2, col3 = st.columns(3)
with col1:
    st.metric(
        "RCP Generic Ballot",
        f"D +{rcp_dem - rcp_rep:.1f}",
        f"Dem {rcp_dem}% / Rep {rcp_rep}%",
    )
with col2:
    st.metric("Kalshi Dem House", f"{house_kalshi}%" if house_kalshi else "N/A")
with col3:
    st.metric("Kalshi Rep Senate", f"{senate_kalshi}%" if senate_kalshi else "N/A")

st.subheader("Signals")
if not df_signals.empty:
    df_styled = df_signals.style.apply(highlight_signal, axis=1)
    st.dataframe(df_styled, use_container_width=True, hide_index=True)
else:
    st.write("No signals available.")

# ---- Combo breakdown ----
if combo_implied:
    st.subheader("Balance of Power (Combo Markets)")
    combo_df = pd.DataFrame(
        [
            {
                "Scenario": "🔵 Dem House + 🔴 Rep Senate",
                "Kalshi %": combo_implied["combos"].get("DR", 0),
            },
            {
                "Scenario": "🔵 Dem House + 🔵 Dem Senate",
                "Kalshi %": combo_implied["combos"].get("DD", 0),
            },
            {
                "Scenario": "🔴 Rep House + 🔴 Rep Senate",
                "Kalshi %": combo_implied["combos"].get("RR", 0),
            },
            {
                "Scenario": "🔴 Rep House + 🔵 Dem Senate",
                "Kalshi %": combo_implied["combos"].get("RD", 0),
            },
        ]
    )
    col_table, col_chart = st.columns([1, 1])
    with col_table:
        st.dataframe(combo_df, use_container_width=True, hide_index=True)
    with col_chart:
        fig = px.pie(
            combo_df,
            values="Kalshi %",
            names="Scenario",
            color_discrete_sequence=["#6366f1", "#3b82f6", "#ef4444", "#f97316"],
            hole=0.4,
        )
        fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=300)
        st.plotly_chart(fig, use_container_width=True)

# ====================== PORTFOLIO ======================
auth = load_auth()

if auth:
    st.subheader("Portfolio")

    # ── Balance row ──
    balance_data = fetch_balance(auth)
    if balance_data:
        bal_col1, bal_col2 = st.columns(2)
        with bal_col1:
            st.metric(
                "Available Balance",
                f"${balance_data['balance_cents'] / 100:,.2f}",
            )
        with bal_col2:
            st.metric(
                "Portfolio Value",
                f"${balance_data['portfolio_value_cents'] / 100:,.2f}",
            )

    # ── Open positions ──
    positions = fetch_positions(auth)
    if positions:
        st.markdown("#### Open Positions")
        pos_rows = []
        for p in positions:
            ticker = p.get("ticker", "")
            position = p.get("position", 0)
            if position == 0:
                continue

            # Use fixed-point dollar fields when available
            market_exposure = p.get("market_exposure_dollars")
            if market_exposure is not None:
                market_exposure = float(market_exposure)
            else:
                market_exposure = p.get("market_exposure", 0) / 100

            realized_pnl = p.get("realized_pnl_dollars")
            if realized_pnl is not None:
                realized_pnl = float(realized_pnl)
            else:
                realized_pnl = p.get("realized_pnl", 0) / 100

            fees = p.get("fees_paid_dollars")
            if fees is not None:
                fees = float(fees)
            else:
                fees = p.get("fees_paid", 0) / 100

            # Look up current market price for unrealized P&L estimate.
            # Check the already-fetched markets dict first; fall back to a
            # direct per-ticker API call for positions outside the three
            # pre-fetched series (e.g. governor races, individual Senate races).
            current_price = None
            for cat in ("house", "senate", "combo"):
                for m in markets.get(cat, []):
                    if m["ticker"] == ticker:
                        current_price = get_price_pct(m)
                        break
                if current_price is not None:
                    break
            if current_price is None:
                current_price = fetch_market_price(ticker)

            # Unrealized P&L: mark-to-market value minus cost basis (market_exposure)
            unrealized_pnl = None
            if current_price is not None and abs(position) > 0:
                contracts = abs(position)
                if position > 0:  # Yes contracts
                    current_value = (current_price / 100) * contracts
                else:  # No contracts
                    current_value = ((100 - current_price) / 100) * contracts
                unrealized_pnl = current_value - market_exposure

            total_pnl = (
                realized_pnl + unrealized_pnl
                if unrealized_pnl is not None
                else None
            )

            pos_rows.append(
                {
                    "Ticker": ticker,
                    "Side": "Yes" if position > 0 else "No",
                    "Contracts": abs(position),
                    "Market Price": f"{current_price:.1f}%" if current_price else "—",
                    "Exposure": f"${market_exposure:,.2f}",
                    "Realized P&L": f"${realized_pnl:+,.2f}",
                    "Unrealized P&L": (
                        f"${unrealized_pnl:+,.2f}" if unrealized_pnl is not None else "—"
                    ),
                    "Total P&L": (
                        f"${total_pnl:+,.2f}" if total_pnl is not None else "—"
                    ),
                    "Fees": f"${fees:,.2f}",
                }
            )

        if pos_rows:
            df_pos = pd.DataFrame(pos_rows)

            def _color_pnl(val):
                if val.startswith("$+") or val.startswith("+"):
                    return "color: #22c55e"
                elif val.startswith("$-") or val.startswith("-"):
                    return "color: #ef4444"
                return ""

            styled = df_pos.style.map(
                _color_pnl, subset=["Realized P&L", "Unrealized P&L", "Total P&L"]
            )
            st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.info("No open positions.")
    else:
        st.info("No open positions.")

    # ── Resting orders ──
    with st.expander("📋 Resting Orders", expanded=False):
        orders = fetch_resting_orders(auth)
        if orders:
            order_rows = []
            for o in orders:
                price_field = (
                    o.get("yes_price_dollars") or o.get("no_price_dollars") or ""
                )
                if price_field:
                    try:
                        price_display = f"{float(price_field) * 100:.1f}¢"
                    except (ValueError, TypeError):
                        price_display = str(price_field)
                else:
                    price_cents = o.get("yes_price") or o.get("no_price", 0)
                    price_display = f"{price_cents}¢"

                remaining = o.get("remaining_count_fp") or str(
                    o.get("remaining_count", 0)
                )
                order_rows.append(
                    {
                        "Ticker": o.get("ticker", ""),
                        "Side": o.get("side", "").capitalize(),
                        "Action": o.get("action", "").capitalize(),
                        "Price": price_display,
                        "Remaining": remaining,
                        "Created": o.get("created_time", "")[:16],
                    }
                )
            st.dataframe(
                pd.DataFrame(order_rows), use_container_width=True, hide_index=True
            )
        else:
            st.caption("No resting orders.")

    # ── Settlement history ──
    with st.expander("📜 Settlement History", expanded=False):
        settlements = fetch_settlements(auth)
        if settlements:
            settle_rows = []
            for s in settlements:
                revenue_val = s.get("revenue", 0) / 100
                cost_yes = s.get("yes_total_cost", 0) / 100
                cost_no = s.get("no_total_cost", 0) / 100
                total_cost = cost_yes + cost_no
                net = revenue_val - total_cost

                settle_rows.append(
                    {
                        "Ticker": s.get("ticker", ""),
                        "Result": s.get("market_result", "").capitalize(),
                        "Yes Held": s.get("yes_count", 0),
                        "No Held": s.get("no_count", 0),
                        "Revenue": f"${revenue_val:,.2f}",
                        "Cost": f"${total_cost:,.2f}",
                        "Net P&L": f"${net:+,.2f}",
                        "Settled": s.get("settled_time", "")[:16],
                    }
                )
            df_settle = pd.DataFrame(settle_rows)

            def _color_net(val):
                if val.startswith("$+") or val.startswith("+"):
                    return "color: #22c55e"
                elif val.startswith("$-") or val.startswith("-"):
                    return "color: #ef4444"
                return ""

            styled = df_settle.style.map(_color_net, subset=["Net P&L"])
            st.dataframe(styled, use_container_width=True, hide_index=True)
        else:
            st.caption("No settlements yet.")
else:
    st.sidebar.markdown("---")
    st.sidebar.caption(
        "💡 Set KALSHI_KEY_ID and KALSHI_PRIVATE_KEY in .env to see your portfolio."
    )

# ====================== PRICE HISTORY CHARTS ======================
st.subheader("Price History")

range_options = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "All": 365}
selected_range = st.radio(
    "Time range",
    options=list(range_options.keys()),
    index=2,
    horizontal=True,
    label_visibility="collapsed",
)
days_back = range_options[selected_range]

if days_back <= 7:
    period_interval = 60
elif days_back <= 30:
    period_interval = 1440
else:
    period_interval = 1440

# ---- Congressional Control chart ----
chart_data = []

if house_direct:
    house_candles = fetch_candlesticks(
        HOUSE_SERIES, house_direct["ticker"], days_back, period_interval
    )
    if not house_candles.empty:
        chart_data.append(("Dem House (Yes)", "#3b82f6", house_candles))

if senate_direct:
    senate_candles = fetch_candlesticks(
        SENATE_SERIES, senate_direct["ticker"], days_back, period_interval
    )
    if not senate_candles.empty:
        chart_data.append(("Rep Senate (Yes)", "#ef4444", senate_candles))

if chart_data:
    st.caption("Congressional Control — Probability Over Time")
    fig = build_price_chart(chart_data)
    st.plotly_chart(fig, use_container_width=True)
else:
    st.info("No candlestick data available for the selected range.")

# ---- Combo chart (all 4 scenarios) ----
with st.expander("📊 Balance of Power Combo History", expanded=False):
    combo_chart_data = []
    combo_colors = {
        "-DR": ("#6366f1", "Dem House + Rep Senate"),
        "-DD": ("#3b82f6", "Dem House + Dem Senate"),
        "-RR": ("#ef4444", "Rep House + Rep Senate"),
        "-RD": ("#f97316", "Rep House + Dem Senate"),
    }
    for m in markets["combo"]:
        for suffix, (color, label) in combo_colors.items():
            if m["ticker"].upper().endswith(suffix):
                combo_candles = fetch_candlesticks(
                    COMBO_SERIES, m["ticker"], days_back, period_interval
                )
                if not combo_candles.empty:
                    combo_chart_data.append((label, color, combo_candles))
                break

    if combo_chart_data:
        st.caption("Balance of Power Scenarios")
        combo_fig = build_price_chart(combo_chart_data)
        st.plotly_chart(combo_fig, use_container_width=True)
    else:
        st.info("No combo candlestick data available.")

# ---- Debug ----
with st.expander("🔧 Debug: Raw Market Data"):
    st.json(markets)

# Refresh
if st.button("Refresh Now"):
    st.cache_data.clear()
    st.rerun()

st.markdown('<meta http-equiv="refresh" content="600">', unsafe_allow_html=True)