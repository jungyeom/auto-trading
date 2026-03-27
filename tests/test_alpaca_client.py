"""Tests for src/execution/alpaca_client.py"""
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from dataclasses import dataclass
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from src.execution.alpaca_client import AlpacaClient, Portfolio, Position, _error_result, _order_to_dict


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------

@dataclass
class FakeConfig:
    alpaca_api_key: str = "test_key"
    alpaca_secret_key: str = "test_secret"
    alpaca_base_url: str = "https://paper-api.alpaca.markets"
    order_type: str = "market"


def _make_fake_position(symbol="NVDA", qty="10", market_value="1500",
                         current_price="150", avg_entry_price="130",
                         unrealized_plpc="0.1538"):
    pos = MagicMock()
    pos.symbol = symbol
    pos.qty = qty
    pos.market_value = market_value
    pos.current_price = current_price
    pos.avg_entry_price = avg_entry_price
    pos.unrealized_plpc = unrealized_plpc
    return pos


def _make_fake_account(equity="10000", cash="3000"):
    acc = MagicMock()
    acc.equity = equity
    acc.cash = cash
    return acc


def _make_fake_order(ticker="NVDA", qty="10", order_type="market",
                     filled_avg_price="150", id="ord-123", status="accepted"):
    order = MagicMock()
    order.qty = qty
    order.order_type = order_type
    order.filled_avg_price = filled_avg_price
    order.id = id
    order.status = status
    return order


# ---------------------------------------------------------------------------
# get_portfolio
# ---------------------------------------------------------------------------

@patch("alpaca_trade_api.REST")
def test_get_portfolio_returns_portfolio(MockREST):
    mock_api = MagicMock()
    MockREST.return_value = mock_api
    mock_api.get_account.return_value = _make_fake_account()
    mock_api.list_positions.return_value = [_make_fake_position()]

    client = AlpacaClient(FakeConfig())
    portfolio = client.get_portfolio()

    assert isinstance(portfolio, Portfolio)
    assert portfolio.total_equity == 10_000.0
    assert portfolio.cash == 3_000.0
    assert len(portfolio.positions) == 1
    assert portfolio.positions[0].ticker == "NVDA"
    assert portfolio.positions[0].entry_price == 130.0


@patch("alpaca_trade_api.REST")
def test_get_portfolio_no_positions(MockREST):
    mock_api = MagicMock()
    MockREST.return_value = mock_api
    mock_api.get_account.return_value = _make_fake_account()
    mock_api.list_positions.return_value = []

    client = AlpacaClient(FakeConfig())
    portfolio = client.get_portfolio()
    assert portfolio.positions == []


# ---------------------------------------------------------------------------
# sell_position
# ---------------------------------------------------------------------------

@patch("alpaca_trade_api.REST")
def test_sell_position_market(MockREST):
    mock_api = MagicMock()
    MockREST.return_value = mock_api
    mock_api.get_position.return_value.qty = "10"
    mock_api.submit_order.return_value = _make_fake_order(status="accepted")

    client = AlpacaClient(FakeConfig())
    result = client.sell_position("NVDA", order_type="market")

    assert result["ticker"] == "NVDA"
    assert result["action"] == "sell"
    assert result["status"] == "accepted"
    assert result["order_id"] == "ord-123"
    mock_api.submit_order.assert_called_once()
    call_kwargs = mock_api.submit_order.call_args.kwargs
    assert call_kwargs["side"] == "sell"
    assert call_kwargs["type"] == "market"


@patch("alpaca_trade_api.REST")
def test_sell_position_no_open_position_returns_error(MockREST):
    mock_api = MagicMock()
    MockREST.return_value = mock_api
    mock_api.get_position.return_value.qty = "0"

    client = AlpacaClient(FakeConfig())
    result = client.sell_position("AAPL")
    assert result["status"] == "error"


@patch("alpaca_trade_api.REST")
def test_sell_position_api_exception_returns_error(MockREST):
    mock_api = MagicMock()
    MockREST.return_value = mock_api
    mock_api.get_position.return_value.qty = "5"
    mock_api.submit_order.side_effect = Exception("API timeout")

    client = AlpacaClient(FakeConfig())
    result = client.sell_position("MSFT")
    assert result["status"] == "error"
    assert "API timeout" in result["error_message"]


# ---------------------------------------------------------------------------
# place_order (buy)
# ---------------------------------------------------------------------------

@patch("alpaca_trade_api.REST")
def test_place_buy_order(MockREST):
    mock_api = MagicMock()
    MockREST.return_value = mock_api
    mock_api.submit_order.return_value = _make_fake_order(status="accepted")

    client = AlpacaClient(FakeConfig())
    result = client.place_order("NVDA", "buy", amount=1500.0, order_type="market")

    assert result["action"] == "buy"
    assert result["dollar_amount"] == 1500.0
    assert result["status"] == "accepted"
    call_kwargs = mock_api.submit_order.call_args.kwargs
    assert call_kwargs["notional"] == 1500.0
    assert call_kwargs["side"] == "buy"


@patch("alpaca_trade_api.REST")
def test_place_order_unknown_action_returns_error(MockREST):
    mock_api = MagicMock()
    MockREST.return_value = mock_api

    client = AlpacaClient(FakeConfig())
    result = client.place_order("NVDA", "short", amount=500.0)
    assert result["status"] == "error"


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def test_error_result_structure():
    r = _error_result("AAPL", "buy", "Test error")
    assert r["status"] == "error"
    assert r["error_message"] == "Test error"
    assert r["ticker"] == "AAPL"
    assert r["action"] == "buy"
