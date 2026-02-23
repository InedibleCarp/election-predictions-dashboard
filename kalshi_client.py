"""Kalshi API client — public market data + authenticated portfolio endpoints."""

import base64
import os
import time

import requests
import streamlit as st
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"

HOUSE_SERIES = "CONTROLH"
SENATE_SERIES = "CONTROLS"
COMBO_SERIES = "KXBALANCEPOWERCOMBO"


# ── helpers ──────────────────────────────────────────────────────────


def get_price_pct(market):
    """Extract yes price as 0-100 percentage from a market dict."""
    for field in ("yes_bid_dollars", "last_price_dollars"):
        val = market.get(field)
        if val is not None:
            try:
                return round(float(val) * 100, 1)
            except (ValueError, TypeError):
                continue
    for field in ("yes_bid", "last_price"):
        val = market.get(field)
        if val is not None:
            try:
                return round(float(val), 1)
            except (ValueError, TypeError):
                continue
    return None


def find_by_side(market_list, suffix):
    """Find market whose ticker ends with the given suffix (e.g. '-D', '-R')."""
    for m in market_list:
        if m["ticker"].upper().endswith(suffix.upper()):
            return m
    return None


# ── public (unauthenticated) endpoints ───────────────────────────────


def kalshi_get(endpoint, params=None):
    """Public Kalshi API — no auth needed for market data."""
    url = f"{KALSHI_BASE}/{endpoint}"
    resp = requests.get(
        url, params=params, headers={"Accept": "application/json"}, timeout=15
    )
    resp.raise_for_status()
    return resp.json()


# ── market discovery ─────────────────────────────────────────────────


def discover_all_markets() -> dict:
    """Fetch House, Senate, and Combo markets using series_ticker filtering."""
    result = {"house": [], "senate": [], "combo": []}

    for category, series in [
        ("house", HOUSE_SERIES),
        ("senate", SENATE_SERIES),
        ("combo", COMBO_SERIES),
    ]:
        try:
            data = kalshi_get(
                "markets",
                params={"series_ticker": series, "status": "open", "limit": 50},
            )
            for m in data.get("markets", []):
                result[category].append(
                    {
                        "ticker": m["ticker"],
                        "title": m.get("title", ""),
                        "yes_bid_dollars": m.get("yes_bid_dollars"),
                        "last_price_dollars": m.get("last_price_dollars"),
                        "yes_bid": m.get("yes_bid"),
                        "last_price": m.get("last_price"),
                        "volume": m.get("volume", 0),
                    }
                )
        except Exception as e:
            import streamlit as st

            st.sidebar.error(f"{category} ({series}): {e}")

    return result


# ── authenticated client ─────────────────────────────────────────────


class KalshiAuth:
    """RSA-PSS request signing for authenticated Kalshi endpoints."""

    def __init__(self, key_id: str, private_key_pem: str):
        self.key_id = key_id
        self.private_key = serialization.load_pem_private_key(
            private_key_pem.encode("utf-8"), password=None
        )

    def _sign(self, message: str) -> str:
        sig = self.private_key.sign(
            message.encode("utf-8"),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(sig).decode("utf-8")

    def headers(self, method: str, path: str) -> dict:
        ts = str(int(time.time() * 1000))
        path_no_qs = path.split("?")[0]
        sig = self._sign(f"{ts}{method}{path_no_qs}")
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-TIMESTAMP": ts,
            "KALSHI-ACCESS-SIGNATURE": sig,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def get(self, endpoint: str, params=None):
        path = f"/trade-api/v2/{endpoint}"
        url = f"{KALSHI_BASE}/{endpoint}"
        hdrs = self.headers("GET", path)
        resp = requests.get(url, params=params, headers=hdrs, timeout=15)
        resp.raise_for_status()
        return resp.json()


@st.cache_data(ttl=120)
def fetch_market_price(ticker: str) -> float | None:
    """Fetch the current yes price for any single ticker (public endpoint)."""
    try:
        data = kalshi_get(f"markets/{ticker}")
        market = data.get("market", {})
        return get_price_pct(market)
    except Exception:
        return None


def load_auth() -> KalshiAuth | None:
    """Load Kalshi credentials from environment; return None if missing."""
    key_id = os.getenv("KALSHI_KEY_ID", "").strip()
    raw_pem = os.getenv("KALSHI_PRIVATE_KEY", "").strip()
    if not key_id or not raw_pem or raw_pem.startswith("your_"):
        return None
    # Handle escaped newlines from .env files
    pem = raw_pem.replace("\\n", "\n")
    try:
        return KalshiAuth(key_id, pem)
    except Exception:
        return None


# ── portfolio data fetching ──────────────────────────────────────────


@st.cache_data(ttl=120)
def fetch_balance(_auth: KalshiAuth) -> dict | None:
    """Fetch account balance and portfolio value."""
    try:
        data = _auth.get("portfolio/balance")
        return {
            "balance_cents": data.get("balance", 0),
            "portfolio_value_cents": data.get("portfolio_value", 0),
        }
    except Exception as e:
        st.warning(f"Balance fetch failed: {e}")
        return None


@st.cache_data(ttl=120)
def fetch_positions(_auth: KalshiAuth) -> list[dict]:
    """Fetch open (unsettled) positions."""
    try:
        data = _auth.get(
            "portfolio/positions", params={"count_filter": "position", "limit": 100}
        )
        return data.get("market_positions", [])
    except Exception as e:
        st.warning(f"Positions fetch failed: {e}")
        return []


@st.cache_data(ttl=120)
def fetch_resting_orders(_auth: KalshiAuth) -> list[dict]:
    """Fetch currently resting (open) orders."""
    try:
        data = _auth.get(
            "portfolio/orders", params={"status": "resting", "limit": 100}
        )
        return data.get("orders", [])
    except Exception as e:
        st.warning(f"Orders fetch failed: {e}")
        return []


@st.cache_data(ttl=300)
def fetch_settlements(_auth: KalshiAuth) -> list[dict]:
    """Fetch settlement history."""
    try:
        data = _auth.get("portfolio/settlements", params={"limit": 100})
        return data.get("settlements", [])
    except Exception as e:
        st.warning(f"Settlements fetch failed: {e}")
        return []