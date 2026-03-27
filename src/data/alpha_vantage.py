"""
Alpha Vantage client — price data and fundamentals, with file-based caching.

Free tier: 25 requests/day.
Daily usage target: 1–3 calls (bulk price + fundamentals per ticker as needed).

Endpoints used:
  - BATCH_STOCK_QUOTES  : bulk daily close prices for all tickers (1 call)
  - INCOME_STATEMENT    : annual/quarterly income statement (cached 90 days)
  - BALANCE_SHEET       : annual/quarterly balance sheet (cached 90 days)
  - CASH_FLOW           : annual/quarterly cash flow (cached 90 days)

Note: BATCH_STOCK_QUOTES requires a premium Alpha Vantage key. If you are on
the free tier, set AV_USE_GLOBAL_QUOTE=true in .env to fall back to individual
GLOBAL_QUOTE calls (15 calls instead of 1 for the full universe).
"""

import logging
import os
from typing import Dict, List, Optional

import requests

from src.data import cache as cache_module
from src.data.cache import TTL_DAILY_PRICE, TTL_FUNDAMENTALS

logger = logging.getLogger(__name__)

AV_BASE_URL = "https://www.alphavantage.co/query"


class AlphaVantageClient:
    def __init__(self, api_key: str):
        self._api_key = api_key
        self._use_global_quote = os.getenv("AV_USE_GLOBAL_QUOTE", "false").lower() == "true"

    # ------------------------------------------------------------------
    # Price data
    # ------------------------------------------------------------------

    def get_bulk_daily_prices(self, tickers: List[str]) -> Dict[str, Dict]:
        """
        Fetch the latest daily price for each ticker.
        Returns: {ticker: {"close": float, "volume": float, "prev_close": float, ...}}

        Uses BATCH_STOCK_QUOTES (1 API call) when available; falls back to
        individual GLOBAL_QUOTE calls if AV_USE_GLOBAL_QUOTE=true.
        """
        if self._use_global_quote:
            return self._bulk_via_global_quote(tickers)
        return self._bulk_via_batch(tickers)

    def _bulk_via_batch(self, tickers: List[str]) -> Dict[str, Dict]:
        # Try cache first (all-tickers, keyed as a group)
        cached = cache_module.get("av", "ALL", "batch_quotes", TTL_DAILY_PRICE)
        if cached is not None:
            return cached

        symbols = ",".join(tickers)
        params = {"function": "BATCH_STOCK_QUOTES", "symbols": symbols, "apikey": self._api_key}
        resp = self._get(params)

        if "Stock Quotes" not in resp:
            logger.warning("BATCH_STOCK_QUOTES unavailable, falling back to GLOBAL_QUOTE")
            return self._bulk_via_global_quote(tickers)

        result = {}
        for quote in resp["Stock Quotes"]:
            ticker = quote["1. symbol"]
            result[ticker] = {
                "close": float(quote["2. price"]),
                "volume": float(quote["3. volume"]),
                "timestamp": quote["4. timestamp"],
            }

        cache_module.set("av", "ALL", "batch_quotes", result)
        return result

    def _bulk_via_global_quote(self, tickers: List[str]) -> Dict[str, Dict]:
        result = {}
        for ticker in tickers:
            cached = cache_module.get("av", ticker, "global_quote", TTL_DAILY_PRICE)
            if cached is not None:
                result[ticker] = cached
                continue

            params = {"function": "GLOBAL_QUOTE", "symbol": ticker, "apikey": self._api_key}
            resp = self._get(params)
            quote = resp.get("Global Quote", {})
            if not quote:
                logger.warning("No GLOBAL_QUOTE data for %s", ticker)
                continue

            data = {
                "close": float(quote.get("05. price", 0)),
                "prev_close": float(quote.get("08. previous close", 0)),
                "volume": float(quote.get("06. volume", 0)),
                "change_pct": quote.get("10. change percent", "0%").rstrip("%"),
            }
            cache_module.set("av", ticker, "global_quote", data)
            result[ticker] = data

        return result

    def get_time_series_daily(self, ticker: str, outputsize: str = "compact") -> Optional[Dict]:
        """
        Fetch daily time series for a single ticker (used for momentum/volume calculations).
        outputsize: "compact" = last 100 days, "full" = up to 20 years.
        Cached for 1 day.
        """
        cached = cache_module.get("av", ticker, "time_series_daily", TTL_DAILY_PRICE)
        if cached is not None:
            return cached

        params = {
            "function": "TIME_SERIES_DAILY",
            "symbol": ticker,
            "outputsize": outputsize,
            "apikey": self._api_key,
        }
        resp = self._get(params)
        series = resp.get("Time Series (Daily)")
        if not series:
            logger.warning("No daily time series for %s", ticker)
            return None

        cache_module.set("av", ticker, "time_series_daily", series)
        return series

    # ------------------------------------------------------------------
    # Fundamentals
    # ------------------------------------------------------------------

    def get_income_statement(self, ticker: str) -> Optional[Dict]:
        return self._get_fundamental(ticker, "INCOME_STATEMENT", "income_statement")

    def get_balance_sheet(self, ticker: str) -> Optional[Dict]:
        return self._get_fundamental(ticker, "BALANCE_SHEET", "balance_sheet")

    def get_cash_flow(self, ticker: str) -> Optional[Dict]:
        return self._get_fundamental(ticker, "CASH_FLOW", "cash_flow")

    def get_overview(self, ticker: str) -> Optional[Dict]:
        """Company overview: P/E, market cap, EPS, sector, etc. Cached 90 days."""
        return self._get_fundamental(ticker, "OVERVIEW", "overview")

    def _get_fundamental(self, ticker: str, function: str, cache_key: str) -> Optional[Dict]:
        cached = cache_module.get("av", ticker, cache_key, TTL_FUNDAMENTALS)
        if cached is not None:
            return cached

        params = {"function": function, "symbol": ticker, "apikey": self._api_key}
        resp = self._get(params)
        if not resp or "Symbol" not in resp and "annualReports" not in resp:
            logger.warning("No %s data for %s", function, ticker)
            return None

        cache_module.set("av", ticker, cache_key, resp)
        return resp

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get(self, params: Dict) -> Dict:
        try:
            resp = requests.get(AV_BASE_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            if "Note" in data:
                logger.warning("Alpha Vantage rate limit hit: %s", data["Note"])
            if "Information" in data:
                logger.warning("Alpha Vantage API info: %s", data["Information"])
            return data
        except requests.RequestException as e:
            logger.error("Alpha Vantage request failed: %s", e)
            return {}
