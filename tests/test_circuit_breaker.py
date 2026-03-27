"""Tests for src/risk/circuit_breaker.py"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass
from typing import List

import pytest

from src.risk.circuit_breaker import check_portfolio_drawdown, scan_stop_losses


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

@dataclass
class FakeConfig:
    portfolio_drawdown_limit_pct: float = 0.15
    stop_loss_pct: float = 0.08


@dataclass
class FakePosition:
    ticker: str
    current_price: float
    entry_price: float
    qty: float = 10.0
    market_value: float = 0.0
    unrealized_pl_pct: float = 0.0


# ---------------------------------------------------------------------------
# check_portfolio_drawdown
# ---------------------------------------------------------------------------

def test_no_drawdown():
    drawdown, halt = check_portfolio_drawdown(10_000, 10_000, FakeConfig())
    assert drawdown == 0.0
    assert not halt


def test_below_limit():
    # 10% drawdown, 15% limit
    drawdown, halt = check_portfolio_drawdown(9_000, 10_000, FakeConfig())
    assert abs(drawdown - 0.10) < 1e-6
    assert not halt


def test_at_limit():
    # exactly at limit — should halt
    drawdown, halt = check_portfolio_drawdown(8_500, 10_000, FakeConfig())
    assert drawdown == pytest.approx(0.15)
    assert halt


def test_above_limit():
    drawdown, halt = check_portfolio_drawdown(8_000, 10_000, FakeConfig())
    assert drawdown == pytest.approx(0.20)
    assert halt


def test_equity_higher_than_peak():
    # New high — drawdown should be negative or zero, never halt
    drawdown, halt = check_portfolio_drawdown(11_000, 10_000, FakeConfig())
    assert drawdown < 0
    assert not halt


def test_zero_peak_equity():
    drawdown, halt = check_portfolio_drawdown(10_000, 0, FakeConfig())
    assert drawdown == 0.0
    assert not halt


# ---------------------------------------------------------------------------
# scan_stop_losses
# ---------------------------------------------------------------------------

def test_no_positions():
    assert scan_stop_losses([], FakeConfig()) == []


def test_position_above_stop():
    pos = FakePosition("AAPL", current_price=100, entry_price=95)  # +5.3%, no stop
    assert scan_stop_losses([pos], FakeConfig()) == []


def test_position_exactly_at_stop():
    # entry=100, current=92 → -8% = exactly at limit → should trigger
    pos = FakePosition("NVDA", current_price=92, entry_price=100)
    assert scan_stop_losses([pos], FakeConfig()) == ["NVDA"]


def test_position_below_stop():
    pos = FakePosition("TSLA", current_price=85, entry_price=100)  # -15% → stop
    assert scan_stop_losses([pos], FakeConfig()) == ["TSLA"]


def test_mixed_positions():
    positions = [
        FakePosition("AAPL", current_price=150, entry_price=140),  # +7.1% — ok
        FakePosition("NVDA", current_price=80, entry_price=100),   # -20% — stop
        FakePosition("MSFT", current_price=200, entry_price=195),  # +2.6% — ok
        FakePosition("TSLA", current_price=50, entry_price=60),    # -16.7% — stop
    ]
    result = scan_stop_losses(positions, FakeConfig())
    assert set(result) == {"NVDA", "TSLA"}


def test_zero_entry_price_skipped():
    pos = FakePosition("BAD", current_price=100, entry_price=0)
    assert scan_stop_losses([pos], FakeConfig()) == []
