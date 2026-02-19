# Kalshi 2026 Elections Dashboard

A Streamlit dashboard that compares public opinion polls from RealClearPolling against prediction market prices on Kalshi to surface trading signals for the 2026 U.S. elections.

## What It Does

- **Aggregates polls** — Scrapes RealClearPolling for generic congressional ballot data (Democrat vs. Republican)
- **Fetches market data** — Pulls live prices from Kalshi's public API for House control, Senate control, and balance-of-power combo markets
- **Generates signals** — Converts polling margins into implied probabilities and compares them to market prices, flagging mispricings as Buy, Sell, or Watch
- **Visualizes trends** — Interactive Plotly charts showing price history, volume, and scenario breakdowns
- **Displays portfolio** — Authenticated view of your Kalshi positions, P&L, resting orders, and settlement history

## Quick Start

```bash
# Clone the repo
git clone https://github.com/InedibleCarp/election-predictions-dashboard.git
cd election-predictions-dashboard

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Copy and configure environment variables
cp .env.example .env
# Edit .env with your Kalshi API credentials (optional — market data is public)

# Run the dashboard
streamlit run app.py
```

The dashboard opens at `http://localhost:8501` and auto-refreshes every 10 minutes.

## Configuration

Copy `.env.example` to `.env` and fill in values as needed:

| Variable | Required | Description |
|---|---|---|
| `KALSHI_KEY_ID` | No | Kalshi API key — enables the Portfolio section |
| `KALSHI_PRIVATE_KEY` | No | Kalshi RSA private key in PEM format |
| `TELEGRAM_BOT_TOKEN` | No | Telegram bot token for price alerts |
| `TELEGRAM_CHAT_ID` | No | Telegram chat ID for alerts |
| `DISCORD_WEBHOOK_URL` | No | Discord webhook URL for alerts |
| `REFRESH_INTERVAL_SECONDS` | No | Data refresh interval in seconds (default: 600) |
| `MIN_EDGE_PCT_FOR_ALERT` | No | Minimum edge % to trigger an alert (default: 8.0) |
| `LOG_LEVEL` | No | Logging level: DEBUG, INFO, or WARNING (default: INFO) |

## Markets Tracked

| Series | Ticker | Description |
|---|---|---|
| House Control | `CONTROLH` | Which party controls the U.S. House |
| Senate Control | `CONTROLS` | Which party controls the U.S. Senate |
| Balance of Power | `KXBALANCEPOWERCOMBO` | Combined outcome scenarios (RR, RD, DR, DD) |

## How Signals Work

1. The generic congressional ballot margin (D vs. R) is scraped from RealClearPolling
2. Historical correlations convert that margin into an implied probability of House control (~4-5 House seats per point of margin)
3. The implied "fair value" is compared to the current Kalshi market price
4. If the edge exceeds a threshold (default 8%), a **Strong Buy** or **Strong Sell** signal is generated; otherwise it's marked **Watch**

## Tech Stack

- **Python** with **Streamlit** for the web UI
- **Plotly** for interactive charts
- **pandas** for data manipulation
- **BeautifulSoup** for RCP scraping
- **requests** for Kalshi API calls

## Project Structure

```
election-predictions-dashboard/
├── app.py               # Streamlit entrypoint (UI layout)
├── kalshi_client.py     # Public + authenticated Kalshi API client
├── rcp.py               # RealClearPolling scraper
├── models.py            # Valuation models and signal generation
├── charts.py            # Plotly chart builders
├── requirements.txt     # Python dependencies
├── .env.example         # Environment variable template
└── .gitignore
```

## License

See repository for license details.