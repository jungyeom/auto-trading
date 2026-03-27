"""
File-based JSON cache with TTL support.

Key format: data/cache/{source}_{ticker}_{data_type}_{date}.json
Each file contains: {"timestamp": <unix float>, "data": <payload>}

TTLs (in seconds) for each data type — callers pass the TTL they need.
"""

import json
import os
import time
from typing import Any, Optional

CACHE_DIR = "data/cache"

# Canonical TTL constants (seconds) — import these in data clients.
TTL_DAILY_PRICE = 60 * 60 * 24        # 1 day
TTL_FUNDAMENTALS = 60 * 60 * 24 * 90  # 90 days
TTL_NEWS = 60 * 60 * 4                # 4 hours
TTL_SENTIMENT = 60 * 60 * 24          # 1 day
TTL_INSIDER = 60 * 60 * 24 * 7        # 7 days


def _cache_path(source: str, ticker: str, data_type: str) -> str:
    """Build the cache file path for a given key triple."""
    from datetime import date
    today = date.today().isoformat()
    filename = f"{source}_{ticker}_{data_type}_{today}.json"
    return os.path.join(CACHE_DIR, filename)


def get(source: str, ticker: str, data_type: str, ttl_seconds: int) -> Optional[Any]:
    """
    Return cached data if it exists and is within TTL, else None.
    Searches today's cache file; returns None if expired or missing.
    """
    path = _cache_path(source, ticker, data_type)
    if not os.path.exists(path):
        return None

    with open(path, "r") as f:
        entry = json.load(f)

    age = time.time() - entry["timestamp"]
    if age > ttl_seconds:
        return None

    return entry["data"]


def set(source: str, ticker: str, data_type: str, data: Any) -> None:
    """Write data to the cache with the current timestamp."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = _cache_path(source, ticker, data_type)
    entry = {"timestamp": time.time(), "data": data}
    with open(path, "w") as f:
        json.dump(entry, f)
