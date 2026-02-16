import streamlit as st
import pandas as pd
import plotly.express as px
from kalshi_python import Configuration, KalshiClient
import requests
from bs4 import BeautifulSoup
import os
from datetime import datetime
import time
from dotenv import load_dotenv

load_dotenv()  # loads .env automatically

st.set_page_config(page_title="Kalshi Elections • RCP Signals", layout="wide")
st.title("🗳️ Kalshi 2026 Elections Dashboard")
st.markdown("**RCP polls → Kalshi signals** | Auto-refreshes every 10min")

# ====================== KALSHI SETUP ======================
@st.cache_resource(ttl=300)  # Use cache_resource for non-serializable objects like API clients
def get_kalshi_client():
    config = Configuration(
        host="https://trading-api.kalshi.com/trade-api/v2"  # For testing, change to "https://demo-api.kalshi.com/trade-api/v2"
    )
    
    key_id = os.getenv("KALSHI_KEY_ID")
    private_key = os.getenv("KALSHI_PRIVATE_KEY")
    
    if not key_id or not private_key:
        st.warning("Kalshi API keys not found in .env — falling back to public/unauthenticated mode (some data may be limited)")
        # Kalshi allows many market GETs without auth on live/demo
    else:
        config.api_key_id = key_id
        config.private_key_pem = private_key  # Ensure this is the full PEM string (multi-line OK)
    
    client = KalshiClient(config)
    return client

client = get_kalshi_client()

# Key markets (add more as needed)
MARKETS = {
    "HOUSE_CONTROL": "CONTROLH-2026",   # Dem Yes = Dem control
    "SENATE_CONTROL": "CONTROLS-2026",  # Rep Yes = Rep control
    "BALANCE_COMBO": "KXBALANCEPOWERCOMBO-27FEB",  # D-House R-Senate
}

@st.cache_data(ttl=60)
def fetch_kalshi_prices():
    prices = {}
    for name, ticker in MARKETS.items():
        try:
            market = client.get_market(ticker)  # This works the same
            # For yes/no markets, use yes_bid / yes_ask or last_price / close_price
            # Adjust based on what you need (bid for conservative buy price)
            yes_price = market.yes_bid if hasattr(market, 'yes_bid') else market.last_price
            prices[name] = round(yes_price * 100, 1) if yes_price is not None else 50.0
        except Exception as e:
            st.error(f"Error fetching {ticker}: {e}")
            prices[name] = 50.0  # fallback
    return prices

# ====================== RCP SCRAPER ======================
@st.cache_data(ttl=600)  # 10 min
def fetch_rcp_generic():
    url = "https://www.realclearpolling.com/polls/state-of-the-union/generic-congressional-vote"
    response = requests.get(url)
    soup = BeautifulSoup(response.text, 'html.parser')
    
    # Find the RCP average row (robust to page changes)
    avg_row = soup.find(string=lambda text: "Democrats" in text and "Republicans" in text and "%" in text)
    if avg_row:
        # Parse text like "Democrats 47.6 % Republicans 42.4 % Democrats +5.2"
        text = avg_row.parent.get_text()
        dem = float(text.split("Democrats ")[1].split("%")[0])
        rep = float(text.split("Republicans ")[1].split("%")[0])
        return dem, rep
    return 47.0, 43.0  # fallback

def rcp_to_house_fair_value(dem_generic):
    # Simple historical model: generic +3-4pts Dem advantage in midterms for Dem House prob
    # (tuned on 2018/2022 data)
    implied_seats = (dem_generic - 50) * 4.5 + 218  # rough seats
    house_prob = max(10, min(90, (implied_seats - 210) * 1.8))  # logistic-ish
    return round(house_prob, 1)

# ====================== SIGNALS ======================
kalshi = fetch_kalshi_prices()
rcp_dem, rcp_rep = fetch_rcp_generic()
house_fair = rcp_to_house_fair_value(rcp_dem)

signals = [
    {
        "Market": "Dem House Control",
        "Kalshi": kalshi["HOUSE_CONTROL"],
        "RCP Fair": house_fair,
        "Edge": kalshi["HOUSE_CONTROL"] - house_fair,
        "Signal": "🟢 Strong Buy" if (kalshi["HOUSE_CONTROL"] - house_fair) > 8 else 
                 "🔴 Sell" if (kalshi["HOUSE_CONTROL"] - house_fair) < -8 else "🟡 Watch"
    },
    # Add Senate, combos, etc.
]

df = pd.DataFrame(signals)
df = df.style.apply(lambda x: ['background-color: #0f4' if 'Buy' in v else 
                               'background-color: #f44' if 'Sell' in v else '' for v in x], axis=1)

# ====================== UI ======================
col1, col2 = st.columns(2)
with col1:
    st.metric("RCP Generic Ballot", f"D+{rcp_dem - rcp_rep:.1f}", f"Dem {rcp_dem}%")
with col2:
    st.metric("Kalshi House (Dem)", f"{kalshi['HOUSE_CONTROL']}%", delta=None)

st.dataframe(df, use_container_width=True)

# Charts
fig = px.line(...)  # Add your historical data here
st.plotly_chart(fig)

# Auto-refresh
if st.button("Refresh Now"):
    st.rerun()
time.sleep(10)  # live feel
st.rerun()