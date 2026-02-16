import streamlit as st
import pandas as pd
import plotly.express as px
import requests
from bs4 import BeautifulSoup
import os
from datetime import datetime
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


def rcp_to_house_fair_value(dem_generic):
    """Generic ballot margin → implied Dem House control probability."""
    margin = dem_generic - 50
    implied_seats = 218 + (margin * 4.5)
    return round(max(10.0, min(90.0, (implied_seats - 210) * 1.8)), 1)


# ====================== LOAD DATA ======================
markets = discover_all_markets()
rcp_dem, rcp_rep = fetch_rcp_generic()
house_fair = rcp_to_house_fair_value(rcp_dem)
SENATE_RCP_FAIR = 58.0  # Placeholder — replace with real model

# Derive implied probs from combos (most reliable source)
combo_implied = derive_from_combos(markets["combo"])

# Also check direct House/Senate markets
def best_direct(market_list):
    if not market_list:
        return None
    best = max(market_list, key=lambda m: m.get("volume") or 0)
    return best

house_direct = best_direct(markets["house"])
senate_direct = best_direct(markets["senate"])

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
st.sidebar.markdown("### Market Sources")

if house_direct:
    st.sidebar.success(f"House: {house_direct['ticker']} ({get_price_pct(house_direct)}%)")
else:
    st.sidebar.info(f"House: no direct market found via series={HOUSE_SERIES}")

if senate_direct:
    st.sidebar.success(f"Senate: {senate_direct['ticker']} ({get_price_pct(senate_direct)}%)")
else:
    st.sidebar.info(f"Senate: no direct market found via series={SENATE_SERIES}")

if combo_implied:
    st.sidebar.markdown("**Combo-Implied Probs:**")
    st.sidebar.text(f"  Dem House: {combo_implied['dem_house']}%")
    st.sidebar.text(f"  Rep Senate: {combo_implied['rep_senate']}%")
    st.sidebar.text(f"  Raw: {combo_implied['combos']}")
else:
    st.sidebar.warning("No combo markets found")

st.sidebar.markdown("---")
st.sidebar.text(f"House markets found: {len(markets['house'])}")
st.sidebar.text(f"Senate markets found: {len(markets['senate'])}")
st.sidebar.text(f"Combo markets found: {len(markets['combo'])}")

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

# Placeholder for price history
st.subheader("Price History (placeholder)")
st.info("Track with `/markets/{ticker}/candlesticks` endpoint + Plotly line chart")

# ---- Debug ----
with st.expander("🔧 Debug: Raw Market Data"):
    st.json(markets)

# Refresh
if st.button("Refresh Now"):
    st.cache_data.clear()
    st.rerun()

st.markdown('<meta http-equiv="refresh" content="600">', unsafe_allow_html=True)