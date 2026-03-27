"""
Finnhub client — news, sentiment, and insider transactions, with caching.

Free tier: 60 requests/minute.
"""

import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import finnhub

from src.data import cache as cache_module
from src.data.cache import TTL_INSIDER, TTL_NEWS, TTL_SENTIMENT

logger = logging.getLogger(__name__)


class FinnhubDataClient:
    def __init__(self, api_key: str):
        self._client = finnhub.Client(api_key=api_key)

    # ------------------------------------------------------------------
    # News
    # ------------------------------------------------------------------

    def get_company_news(self, ticker: str, days_back: int = 7) -> Optional[List[Dict]]:
        """
        Fetch recent company news articles for a ticker.
        Cached for 4 hours. Returns list of article dicts.
        """
        cached = cache_module.get("finnhub", ticker, "news", TTL_NEWS)
        if cached is not None:
            return cached

        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")

        try:
            news = self._client.company_news(ticker, _from=from_date, to=to_date)
        except Exception as e:
            logger.error("Finnhub news fetch failed for %s: %s", ticker, e)
            return None

        if not news:
            logger.debug("No news returned for %s", ticker)
            news = []

        cache_module.set("finnhub", ticker, "news", news)
        return news

    # ------------------------------------------------------------------
    # Sentiment
    # ------------------------------------------------------------------

    def get_social_sentiment(self, ticker: str) -> Optional[Dict]:
        """
        Fetch social media sentiment (Reddit + Twitter aggregates).
        Cached for 1 day. Returns dict with bullish/bearish scores.
        """
        cached = cache_module.get("finnhub", ticker, "sentiment", TTL_SENTIMENT)
        if cached is not None:
            return cached

        to_date = datetime.now().strftime("%Y-%m-%d")
        from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        try:
            sentiment = self._client.stock_social_sentiment(ticker, _from=from_date, to=to_date)
        except Exception as e:
            logger.error("Finnhub sentiment fetch failed for %s: %s", ticker, e)
            return None

        cache_module.set("finnhub", ticker, "sentiment", sentiment)
        return sentiment

    # ------------------------------------------------------------------
    # Insider transactions
    # ------------------------------------------------------------------

    def get_insider_transactions(self, ticker: str) -> Optional[List[Dict]]:
        """
        Fetch insider buy/sell transactions.
        Cached for 7 days. Returns list of transaction dicts.
        """
        cached = cache_module.get("finnhub", ticker, "insider", TTL_INSIDER)
        if cached is not None:
            return cached

        try:
            data = self._client.stock_insider_transactions(ticker)
        except Exception as e:
            logger.error("Finnhub insider fetch failed for %s: %s", ticker, e)
            return None

        transactions = data.get("data", []) if data else []
        cache_module.set("finnhub", ticker, "insider", transactions)
        return transactions

    # ------------------------------------------------------------------
    # Company metrics (for fundamentals analyst)
    # ------------------------------------------------------------------

    def get_basic_financials(self, ticker: str) -> Optional[Dict]:
        """
        Fetch basic financial metrics (P/E, EV/EBITDA, 52w high/low, etc.).
        Cached for 1 day.
        """
        cached = cache_module.get("finnhub", ticker, "basic_financials", TTL_SENTIMENT)
        if cached is not None:
            return cached

        try:
            data = self._client.company_basic_financials(ticker, "all")
        except Exception as e:
            logger.error("Finnhub basic financials fetch failed for %s: %s", ticker, e)
            return None

        if not data:
            return None

        cache_module.set("finnhub", ticker, "basic_financials", data)
        return data
