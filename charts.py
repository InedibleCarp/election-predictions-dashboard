"""Plotly chart builders for the election dashboard."""

from datetime import datetime, timedelta, timezone

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from kalshi_client import kalshi_get


@st.cache_data(ttl=300)
def fetch_candlesticks(
    series_ticker: str,
    market_ticker: str,
    days_back: int = 90,
    period_interval: int = 1440,
) -> pd.DataFrame:
    """Fetch OHLCV candlestick data from Kalshi.

    period_interval: 1 (1min), 60 (1hr), 1440 (1day)
    """
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days_back)

    try:
        data = kalshi_get(
            f"series/{series_ticker}/markets/{market_ticker}/candlesticks",
            params={
                "start_ts": int(start.timestamp()),
                "end_ts": int(now.timestamp()),
                "period_interval": period_interval,
            },
        )
        candles = data.get("candlesticks", [])
        if not candles:
            return pd.DataFrame()

        rows = []
        for c in candles:
            price = c.get("price", {})
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

            rows.append(
                {
                    "timestamp": datetime.fromtimestamp(ts, tz=timezone.utc),
                    "open": _parse(price, "open"),
                    "high": _parse(price, "high"),
                    "low": _parse(price, "low"),
                    "close": _parse(price, "close"),
                    "volume": c.get("volume", 0),
                }
            )

        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.sort_values("timestamp").reset_index(drop=True)
            df = df.dropna(subset=["close"])
        return df

    except Exception as e:
        st.warning(f"Candlestick fetch failed for {market_ticker}: {e}")
        return pd.DataFrame()


def build_price_chart(candle_dfs: list[tuple[str, str, pd.DataFrame]]):
    """Build a Plotly chart with price lines + volume bars.

    candle_dfs: list of (label, color, dataframe) tuples
    """
    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.75, 0.25],
    )

    for label, color, df in candle_dfs:
        if df.empty:
            continue

        fig.add_trace(
            go.Scatter(
                x=df["timestamp"],
                y=df["close"],
                name=label,
                line=dict(color=color, width=2),
                hovertemplate="%{x|%b %d}<br>%{y:.1f}%<extra>" + label + "</extra>",
            ),
            row=1,
            col=1,
        )

        if df["high"].notna().any() and df["low"].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=pd.concat([df["timestamp"], df["timestamp"][::-1]]),
                    y=pd.concat([df["high"], df["low"][::-1]]),
                    fill="toself",
                    fillcolor="rgba(128,128,128,0.08)",
                    line=dict(width=0),
                    name=f"{label} range",
                    showlegend=False,
                    hoverinfo="skip",
                ),
                row=1,
                col=1,
            )

        fig.add_trace(
            go.Bar(
                x=df["timestamp"],
                y=df["volume"],
                name=f"{label} vol",
                marker_color=color,
                opacity=0.4,
                showlegend=False,
                hovertemplate="%{x|%b %d}<br>Vol: %{y:,.0f}<extra></extra>",
            ),
            row=2,
            col=1,
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