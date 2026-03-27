"""Tests for src/portfolio/optimizer.py"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pytest

from src.portfolio.optimizer import optimize_portfolio, Order


# ---------------------------------------------------------------------------
# Minimal stubs
# ---------------------------------------------------------------------------

@dataclass
class FakePosition:
    ticker: str
    qty: float = 10.0
    market_value: float = 1000.0
    current_price: float = 100.0
    entry_price: float = 90.0
    unrealized_pl_pct: float = 0.11


@dataclass
class FakePortfolio:
    total_equity: float
    cash: float
    positions: List[FakePosition] = field(default_factory=list)


@dataclass
class FakeConfig:
    conviction_threshold: float = 0.6
    max_position_pct: float = 0.15
    cash_reserve_pct: float = 0.15
    max_sector_pct: float = 0.30
    sectors: Dict = field(default_factory=lambda: {
        "Information Technology": ["NVDA", "AAPL", "MSFT"],
        "Communication Services": ["GOOGL", "META"],
        "Consumer Discretionary": ["AMZN", "TSLA"],
    })

    def sector_for(self, ticker: str) -> str:
        for sector, tickers in self.sectors.items():
            if ticker in tickers:
                return sector
        return "Unknown"


def make_decision(ticker, action, conviction):
    return {"ticker": ticker, "action": action, "conviction": conviction}


# ---------------------------------------------------------------------------
# Basic filtering
# ---------------------------------------------------------------------------

def test_below_conviction_threshold_filtered_out():
    portfolio = FakePortfolio(total_equity=10_000, cash=10_000)
    decisions = [make_decision("NVDA", "buy", 0.4)]  # below 0.6
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    assert len(orders) == 0


def test_hold_decisions_produce_no_orders():
    portfolio = FakePortfolio(total_equity=10_000, cash=10_000)
    decisions = [make_decision("AAPL", "hold", 0.8)]
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    assert len(orders) == 0


# ---------------------------------------------------------------------------
# Sell decisions
# ---------------------------------------------------------------------------

def test_sell_decision_queued():
    pos = FakePosition("NVDA")
    portfolio = FakePortfolio(total_equity=10_000, cash=9_000, positions=[pos])
    decisions = [make_decision("NVDA", "sell", 0.9)]
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    sell_orders = [o for o in orders if o.action == "sell"]
    assert len(sell_orders) == 1
    assert sell_orders[0].ticker == "NVDA"
    assert sell_orders[0].dollar_amount is None  # full position, no dollar amount


# ---------------------------------------------------------------------------
# Buy decisions
# ---------------------------------------------------------------------------

def test_buy_new_ticker():
    portfolio = FakePortfolio(total_equity=10_000, cash=10_000)
    decisions = [make_decision("NVDA", "buy", 0.8)]
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    buy_orders = [o for o in orders if o.action == "buy" and o.ticker == "NVDA"]
    assert len(buy_orders) == 1
    assert buy_orders[0].dollar_amount > 0


def test_buy_already_owned_ticker_skipped():
    # Buying a ticker we already own — optimizer should not create a new order
    pos = FakePosition("NVDA", market_value=1500)
    portfolio = FakePortfolio(total_equity=10_000, cash=8_500, positions=[pos])
    decisions = [make_decision("NVDA", "buy", 0.8)]
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    buy_orders = [o for o in orders if o.action == "buy"]
    assert len(buy_orders) == 0


def test_target_size_respects_max_position_pct():
    # With 2 buy decisions and 10k equity, target = (10k * 0.85) / 2 = 4250
    # But max_position_pct = 15% → cap at 1500
    portfolio = FakePortfolio(total_equity=10_000, cash=10_000)
    decisions = [
        make_decision("NVDA", "buy", 0.9),
        make_decision("AAPL", "buy", 0.9),
    ]
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    buy_orders = [o for o in orders if o.action == "buy"]
    for o in buy_orders:
        assert o.dollar_amount <= 10_000 * 0.15 + 1e-6  # within max_position_pct


# ---------------------------------------------------------------------------
# Sector constraint
# ---------------------------------------------------------------------------

def test_sector_constraint_blocks_excess():
    # NVDA, AAPL, MSFT all in IT. With 10k equity, buying all 3 would put
    # IT sector at 3 * target_size. Check that the 3rd buy is blocked if it
    # would push IT over 30%.
    portfolio = FakePortfolio(total_equity=10_000, cash=10_000)
    decisions = [
        make_decision("NVDA", "buy", 0.9),
        make_decision("AAPL", "buy", 0.9),
        make_decision("MSFT", "buy", 0.9),
    ]
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    buy_orders = [o for o in orders if o.action == "buy"]
    total_it = sum(o.dollar_amount for o in buy_orders
                   if FakeConfig().sector_for(o.ticker) == "Information Technology")
    assert total_it / 10_000 <= 0.30 + 1e-6


# ---------------------------------------------------------------------------
# Cash guard
# ---------------------------------------------------------------------------

def test_insufficient_cash_blocks_buy():
    portfolio = FakePortfolio(total_equity=10_000, cash=0)  # no cash at all
    decisions = [make_decision("NVDA", "buy", 0.9)]
    orders = optimize_portfolio(decisions, portfolio, FakeConfig())
    buy_orders = [o for o in orders if o.action == "buy"]
    assert len(buy_orders) == 0
