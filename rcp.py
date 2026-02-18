"""RealClearPolling scraper for generic congressional ballot data."""

import re

import requests
import streamlit as st
from bs4 import BeautifulSoup


@st.cache_data(ttl=600)
def fetch_rcp_generic() -> tuple[float, float]:
    """Scrape RCP generic congressional ballot; returns (dem%, rep%)."""
    url = "https://www.realclearpolling.com/polls/state-of-the-union/generic-congressional-vote"
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")

        text_candidates = []
        for elem in soup.find_all(["td", "div", "span", "p"]):
            t = elem.get_text(strip=True)
            if "%" in t and ("Dem" in t or "Rep" in t or "Democrats" in t):
                text_candidates.append(t)

        pattern = r"(?:Democrats?|Dem|D)\s*([\d.]+)\s*%.*?(?:Republicans?|Rep|R)\s*([\d.]+)\s*%"

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