import os

FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "").strip()
MARKETDATA_API_KEY = os.getenv("MARKETDATA_API_KEY", "").strip()

DAYS_AHEAD = int(os.getenv("DAYS_AHEAD", "14"))
MAX_TICKERS = int(os.getenv("MAX_TICKERS", "40"))

