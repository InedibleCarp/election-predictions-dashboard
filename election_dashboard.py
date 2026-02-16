import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import requests
from bs4 import BeautifulSoup
import os
from datetime import datetime, timedelta, timezone
import re
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="Kalshi Elections • RCP Signals", layout="wide")
st.title("🗳️ Kalshi 2026 Elections Dashboard")
st.markdown("**RCP polls → Kalshi signals** | Auto-refreshes every 10 min")

# ====================== KALSHI REST API ======================
KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

# These are SERIES tickers (not event tickers — that was the bug)
HOUSE_SERIES = "CONTROLH"
SENATE_SERIES = "CONTROLS"
COMBO_SERIES = "KXBALANCEPOWERCOMBO"


def kalshi_get(endpoint, params=None):
    """Public Kalshi API — no auth needed for market data."""
    url = f"{KALSHI_BASE}/{endpoint}"
    resp = requests.get(url, params=params, headers={"Accept": "application/json"}, timeout=15)
    resp.raise_for_status()
    return resp.json()


def get_price_pct(market):
    """Extract yes price as 0-100 percentage from a market dict."""
    # Prefer _dollars fields (subpenny-safe, per Jan 2026 changelog)
    for field in ["yes_bid_dollars", "last_price_dollars"]:
        val = market.get(field)
        if val is not None:
            try:
                return round(float(val) * 100, 1)
            except (ValueError, TypeError):
                continue
    # Fallback to cent integers
    for field in ["yes_bid", "last_price"]:
        val = market.get(field)
        if val is not None:
            try:
                return round(float(val), 1)
            except (ValueError, TypeError):
                continue
    return None


# ====================== CANDLESTICK DATA ======================
@st.cache_data(ttl=300)
def fetch_candlesticks(series_ticker, market_ticker, days_back=90, period_interval=1440):
    """
    Fetch OHLCV candlestick data from Kalshi.
    
    Endpoint: GET /series/{series_ticker}/markets/{market_ticker}/candlesticks
    period_interval: 1 (1min), 60 (1hr), 1440 (1day)
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)
    start_ts = int(start.timestamp())
    end_ts = int(now.timestamp())

    try:
        data = kalshi_get(
            f"series/{series_ticker}/markets/{market_ticker}/candlesticks",
            params={
                "start_ts": start_ts,
                "end_ts": end_ts,
                "period_interval": period_interval,
            },
        )
        candles = data.get("candlesticks", [])
        if not candles:
            return pd.DataFrame()

        rows = []
        for c in candles:
            price = c.get("price", {})
            # Skip synthetic candles where all OHLC are null
            close_val = price.get("close_dollars") or price.get("close")
            if close_val is None:
                continue

            ts = c.get("end_period_ts")
            if ts is None:
                continue

            def _parse(p, key):
                dkey = f"{key}_dollars"
                if p.get(dkey) is not None:
                    try:
                        return round(float(p[dkey]) * 100, 2)
                    except (ValueError, TypeError):
                        pass
                if p.get(key) is not None:
                    try:
                        return float(p[key])
                    except (ValueError, TypeError):
                        pass
                return None

            rows.append({
                "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                "open": _parse(price, "open"),
                "high": _parse(price, "high"),
                "low": _parse(price, "low"),
                "close": _parse(price, "close"),
                "volume": c.get("volume", 0),
            })

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("timestamp").reset_index(drop=True)
            df = df.dropna(subset=["close"])
        return df

    except Exception as e:
        st.warning(f"Candlestick fetch failed for {market_ticker}: {e}")
        return pd.DataFrame()


def build_price_chart(candle_dfs):
    """
    Build a Plotly chart with price lines + volume bars.
    candle_dfs: list of (label, color, dataframe) tuples
    """
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.75, 0.25],
    )

    for label, color, df in candle_dfs:
        if df.empty:
            continue

        # Price line (close)
        fig.add_trace(
            go.Scatter(
                x=df["timestamp"], y=df["close"],
                name=label,
                line=dict(color=color, width=2),
                hovertemplate="%{x|%b %d}<br>%{y:.1f}%<extra>" + label + "</extra>",
            ),
            row=1, col=1,
        )

        # High-low range band
        if df["high"].notna().any() and df["low"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=pd.concat([df["timestamp"], df["timestamp"][::-1]]),
                    y=pd.concat([df["high"], df["low"][::-1]]),
                    fill="toself",
                    fillcolor=f"rgba(128,128,128,0.08)",
                    line=dict(width=0),
                    name=f"{label} range",
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=1, col=1,
            )

        # Volume bars
        fig.add_trace(
            go.Bar(
                x=df["timestamp"], y=df["volume"],
                name=f"{label} vol",
                marker_color=color, opacity=0.4,
                showlegend=False,
                hovertemplate="%{x|%b %d}<br>Vol: %{y:,.0f}<extra></extra>",
            ),
            row=2, col=1,
        )

    fig.update_layout(
        height=500,
        margin=dict(t=80, b=20, l=60, r=20),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.08,
            xanchor="center",
            x=0.5,
        ),
        xaxis2_title="Date",
        yaxis_title="Probability (%)",
        yaxis2_title="Contracts",
        hovermode="x unified",
        template="plotly_dark",
    )
    fig.update_yaxes(range=[0, 100], row=1, col=1)

    return fig


# ====================== MARKET DISCOVERY ======================
@st.cache_data(ttl=120)
def discover_all_markets():
    """
    Fetch House, Senate, and Combo markets using series_ticker filtering.
    Returns dict with keys: house_markets, senate_markets, combo_markets.
    """
    result = {"house": [], "senate": [], "combo": []}

    for category, series in [("house", HOUSE_SERIES), ("senate", SENATE_SERIES), ("combo", COMBO_SERIES)]:
        try:
            data = kalshi_get("markets", params={
                "series_ticker": series,
                "status": "open",
                "limit": 50,
            })
            for m in data.get("markets", []):
                result[category].append({
                    "ticker": m["ticker"],
                    "title": m.get("title", ""),
                    "yes_bid_dollars": m.get("yes_bid_dollars"),
                    "last_price_dollars": m.get("last_price_dollars"),
                    "yes_bid": m.get("yes_bid"),
                    "last_price": m.get("last_price"),
                    "volume": m.get("volume", 0),
                })
        except Exception as e:
            st.sidebar.error(f"{category} ({series}): {e}")

    return result


# ====================== COMBO → IMPLIED PROBABILITIES ======================
def derive_from_combos(combo_markets):
    """
    The combo market has 4 outcomes:
      RR = Rep House & Rep Senate
      RD = Rep House & Dem Senate
      DR = Dem House & Rep Senate
      DD = Dem House & Dem Senate

    Implied marginals:
      P(Dem House) = DR + DD
      P(Rep Senate) = RR + DR
      P(Dem Senate) = RD + DD
      P(Rep House) = RR + RD
    """
    prices = {}
    for m in combo_markets:
        ticker = m["ticker"].upper()
        pct = get_price_pct(m)
        if pct is None:
            continue
        # Extract the suffix (RR, RD, DR, DD)
        if ticker.endswith("-RR"):
            prices["RR"] = pct
        elif ticker.endswith("-RD"):
            prices["RD"] = pct
        elif ticker.endswith("-DR"):
            prices["DR"] = pct
        elif ticker.endswith("-DD"):
            prices["DD"] = pct

    if len(prices) < 4:
        return None

    return {
        "dem_house": round(prices["DR"] + prices["DD"], 1),
        "rep_house": round(prices["RR"] + prices["RD"], 1),
        "rep_senate": round(prices["RR"] + prices["DR"], 1),
        "dem_senate": round(prices["RD"] + prices["DD"], 1),
        "combos": prices,
    }


# ====================== RCP SCRAPER ======================
@st.cache_data(ttl=600)
def fetch_rcp_generic():
    url = "https://www.realclearpolling.com/polls/state-of-the-union/generic-congressional-vote"
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        text_candidates = []
        for elem in soup.find_all(["td", "div", "span", "p"]):
            t = elem.get_text(strip=True)
            if "%" in t and ("Dem" in t or "Rep" in t or "Democrats" in t):
                text_candidates.append(t)

        pattern = r'(?:Democrats?|Dem|D)\s*([\d.]+)\s*%.*?(?:Republicans?|Rep|R)\s*([\d.]+)\s*%'

        for text in text_candidates + [soup.body.get_text(separator=" ", strip=True)]:
            match = re.search(pattern, text, re.IGNORECASE | re.DOTALL)
            if match:
                try:
                    return float(match.group(1)), float(match.group(2))
                except ValueError:
                    continue

        st.warning("RCP parse found no matching data — using fallback")
        return 47.0, 43.0
    except Exception as e:
        st.error(f"RCP request failed: {e}")
        return 47.0, 43.0


def rcp_to_house_fair_value(dem_generic, rep_generic):
    """Generic ballot D-R margin → implied Dem House control probability.
    
    Historical rough model: each 1pt of generic ballot margin ≈ 4-5 House seats.
    Dems need ~218 seats for control. Even margin → ~50/50; D+5 → strong Dem.
    """
    margin = dem_generic - rep_generic  # e.g. 47.6 - 42.4 = +5.2
    # D+0 → ~50% control, each point shifts ~4.5 seats, scaled to probability
    implied_seats = 218 + (margin * 4.5)
    house_prob = max(10.0, min(90.0, 50 + (margin * 6)))  # ~6% per point of margin
    return round(house_prob, 1)


# ====================== LOAD DATA ======================
markets = discover_all_markets()
rcp_dem, rcp_rep = fetch_rcp_generic()
house_fair = rcp_to_house_fair_value(rcp_dem, rcp_rep)
SENATE_RCP_FAIR = 58.0  # Placeholder — replace with real model

# Derive implied probs from combos (most reliable source)
combo_implied = derive_from_combos(markets["combo"])

# Find the correct market by ticker suffix (not just highest volume!)
# CONTROLH-2026-D = "Will Democrats win the House" → use for Dem House
# CONTROLS-2026-R = "Will Republicans win the Senate" → use for Rep Senate
def find_by_side(market_list, suffix):
    """Find market whose ticker ends with the given suffix (e.g. '-D', '-R')."""
    for m in market_list:
        if m["ticker"].upper().endswith(suffix.upper()):
            return m
    return None

house_direct = find_by_side(markets["house"], "-D")   # Dem House market
senate_direct = find_by_side(markets["senate"], "-R")  # Rep Senate market

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
    selected = " ✅" if senate_direct and m["ticker"] == senate_direct["ticker"] else ""
    st.sidebar.text(f"  {m['ticker']}: {pct}%{selected}")

if combo_implied:
    st.sidebar.markdown("**Combo-Implied:**")
    st.sidebar.text(f"  Dem House: {combo_implied['dem_house']}%")
    st.sidebar.text(f"  Rep Senate: {combo_implied['rep_senate']}%")

st.sidebar.markdown("---")
st.sidebar.caption(f"House: {len(markets['house'])} mkts | Senate: {len(markets['senate'])} mkts | Combos: {len(markets['combo'])} mkts")

# ====================== SIGNALS ======================
signals = []

if house_kalshi is not None:
    edge = round(house_kalshi - house_fair, 1)
    signals.append({
        "Market": "Dem House Control",
        "Kalshi %": house_kalshi,
        "Source": house_source,
        "RCP Fair %": house_fair,
        "Edge %": edge,
        "Signal": (
            "🟢 Strong Buy" if edge > 8 else
            "🔴 Strong Sell" if edge < -8 else
            "🟡 Watch"
        ),
    })

if senate_kalshi is not None:
    edge = round(senate_kalshi - SENATE_RCP_FAIR, 1)
    signals.append({
        "Market": "Rep Senate Control",
        "Kalshi %": senate_kalshi,
        "Source": senate_source,
        "RCP Fair %": SENATE_RCP_FAIR,
        "Edge %": edge,
        "Signal": (
            "🔴 Sell" if edge > 5 else
            "🟢 Buy" if edge < -5 else
            "🟡 Watch"
        ),
    })

df = pd.DataFrame(signals) if signals else pd.DataFrame()
# Clean up float display
if not df.empty:
    for col in ["Kalshi %", "RCP Fair %", "Edge %"]:
        if col in df.columns:
            df[col] = df[col].map(lambda x: round(x, 1))


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
if not df.empty:
    df_styled = df.style.apply(highlight_signal, axis=1)
    st.dataframe(df_styled, use_container_width=True, hide_index=True)
else:
    st.write("No signals available.")

# ---- Combo breakdown ----
if combo_implied:
    st.subheader("Balance of Power (Combo Markets)")
    combo_df = pd.DataFrame([
        {"Scenario": "🔵 Dem House + 🔴 Rep Senate", "Kalshi %": combo_implied["combos"].get("DR", 0)},
        {"Scenario": "🔵 Dem House + 🔵 Dem Senate", "Kalshi %": combo_implied["combos"].get("DD", 0)},
        {"Scenario": "🔴 Rep House + 🔴 Rep Senate", "Kalshi %": combo_implied["combos"].get("RR", 0)},
        {"Scenario": "🔴 Rep House + 🔵 Dem Senate", "Kalshi %": combo_implied["combos"].get("RD", 0)},
    ])
    col_table, col_chart = st.columns([1, 1])
    with col_table:
        st.dataframe(combo_df, use_container_width=True, hide_index=True)
    with col_chart:
        fig = px.pie(
            combo_df, values="Kalshi %", names="Scenario",
            color_discrete_sequence=["#6366f1", "#3b82f6", "#ef4444", "#f97316"],
            hole=0.4,
        )
        fig.update_layout(margin=dict(t=20, b=20, l=20, r=20), height=300)
        st.plotly_chart(fig, use_container_width=True)

# ====================== PRICE HISTORY CHARTS ======================
st.subheader("Price History")

# Time range selector
range_options = {"1W": 7, "1M": 30, "3M": 90, "6M": 180, "All": 365}
selected_range = st.radio(
    "Time range",
    options=list(range_options.keys()),
    index=2,  # default to 3M
    horizontal=True,
    label_visibility="collapsed",
)
days_back = range_options[selected_range]

# Determine period interval based on range (avoid too many candles)
if days_back <= 7:
    period_interval = 60    # hourly for 1W
elif days_back <= 30:
    period_interval = 1440  # daily for 1M
else:
    period_interval = 1440  # daily for 3M+

# ---- Congressional Control chart ----
chart_data = []

if house_direct:
    house_candles = fetch_candlesticks(HOUSE_SERIES, house_direct["ticker"], days_back, period_interval)
    if not house_candles.empty:
        chart_data.append(("Dem House (Yes)", "#3b82f6", house_candles))

if senate_direct:
    senate_candles = fetch_candlesticks(SENATE_SERIES, senate_direct["ticker"], days_back, period_interval)
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
                combo_candles = fetch_candlesticks(COMBO_SERIES, m["ticker"], days_back, period_interval)
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