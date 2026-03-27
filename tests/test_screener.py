"""Tests for src/screener/universe.py"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass
from typing import List
from unittest.mock import MagicMock, patch

import pytest

from src.screener.universe import rank_tickers, _compute_momentum, _compute_relative_volume


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass
class FakeConfig:
    screener_momentum_weight: float = 0.6
    screener_volume_weight: float = 0.4


def _make_series(closes, volumes=None):
    """Build a minimal daily time series dict suitable for the screener."""
    if volumes is None:
        volumes = [1_000_000] * len(closes)
    # TradingAgents/AV format: {"YYYY-MM-DD": {"4. close": "...", "5. volume": "..."}}
    dates = [f"2026-01-{i + 1:02d}" for i in range(len(closes))]
    return {
        d: {"4. close": str(c), "5. volume": str(v)}
        for d, c, v in zip(dates, closes, volumes)
    }


# ---------------------------------------------------------------------------
# _compute_momentum
# ---------------------------------------------------------------------------

def test_momentum_positive():
    # price rose 10% over 30 days
    closes = [100.0] * 31
    closes[0] = 110.0  # most recent
    series = _make_series(closes)
    m = _compute_momentum(series)
    assert m == pytest.approx(0.10, rel=1e-3)


def test_momentum_negative():
    closes = [100.0] * 31
    closes[0] = 90.0  # most recent
    series = _make_series(closes)
    m = _compute_momentum(series)
    assert m == pytest.approx(-0.10, rel=1e-3)


def test_momentum_insufficient_history():
    series = _make_series([100.0] * 20)  # only 20 days — need 31
    assert _compute_momentum(series) is None


# ---------------------------------------------------------------------------
# _compute_relative_volume
# ---------------------------------------------------------------------------

def test_relative_volume_above_average():
    # 10-day avg = 2M, 50-day avg = 1M → ratio = 2.0
    volumes = [2_000_000] * 10 + [1_000_000] * 40
    closes = [100.0] * 51
    series = _make_series(closes, volumes)
    rv = _compute_relative_volume(series)
    assert rv == pytest.approx(2.0, rel=1e-3)


def test_relative_volume_insufficient_history():
    series = _make_series([100.0] * 40)  # need 51
    assert _compute_relative_volume(series) is None


# ---------------------------------------------------------------------------
# rank_tickers integration
# ---------------------------------------------------------------------------

def test_rank_returns_all_tickers_even_with_partial_data():
    """Tickers with no data end up at the back, not silently dropped."""
    tickers = ["AAPL", "NVDA", "MSFT"]
    mock_av = MagicMock()

    # AAPL and NVDA have enough data; MSFT has none
    def fake_time_series(ticker, outputsize="compact"):
        if ticker == "MSFT":
            return None
        closes = [100.0] * 51
        closes[0] = 105.0 if ticker == "NVDA" else 102.0
        return _make_series(closes)

    mock_av.get_time_series_daily.side_effect = fake_time_series

    ranked = rank_tickers(tickers, FakeConfig(), mock_av)
    assert set(ranked) == {"AAPL", "NVDA", "MSFT"}
    assert ranked[-1] == "MSFT"  # no data → back of the list


def test_rank_all_no_data_returns_original_order():
    tickers = ["A", "B", "C"]
    mock_av = MagicMock()
    mock_av.get_time_series_daily.return_value = None

    ranked = rank_tickers(tickers, FakeConfig(), mock_av)
    assert ranked == tickers  # original order preserved


def test_rank_higher_momentum_ranked_first():
    tickers = ["LOW", "HIGH"]
    mock_av = MagicMock()

    def fake_time_series(ticker, outputsize="compact"):
        closes = [100.0] * 51
        closes[0] = 120.0 if ticker == "HIGH" else 101.0  # HIGH has 20% momentum
        return _make_series(closes)

    mock_av.get_time_series_daily.side_effect = fake_time_series

    ranked = rank_tickers(tickers, FakeConfig(), mock_av)
    assert ranked[0] == "HIGH"
