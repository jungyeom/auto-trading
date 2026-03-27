"""
Alpaca brokerage client — read portfolio, place orders.

Paper trading vs live trading is controlled by config.paper_trading / ALPACA_BASE_URL.
All dollar-denominated buys use notional orders so Alpaca handles fractional shares.
All sells liquidate the entire position.
"""

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import alpaca_trade_api as tradeapi

from src.config import Config

logger = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    qty: float
    market_value: float
    current_price: float
    entry_price: float
    unrealized_pl_pct: float  # as a decimal, e.g. 0.05 = +5%


@dataclass
class Portfolio:
    total_equity: float
    cash: float
    positions: List[Position] = field(default_factory=list)


class AlpacaClient:
    def __init__(self, config: Config):
        self._api = tradeapi.REST(
            key_id=config.alpaca_api_key,
            secret_key=config.alpaca_secret_key,
            base_url=config.alpaca_base_url,
        )

    # ------------------------------------------------------------------
    # Portfolio reads
    # ------------------------------------------------------------------

    def get_portfolio(self) -> Portfolio:
        """
        Fetch current account state and open positions from Alpaca.
        Returns a Portfolio object with total_equity, cash, and a list of Positions.
        """
        account = self._api.get_account()
        total_equity = float(account.equity)
        cash = float(account.cash)

        positions = []
        for pos in self._api.list_positions():
            try:
                positions.append(
                    Position(
                        ticker=pos.symbol,
                        qty=float(pos.qty),
                        market_value=float(pos.market_value),
                        current_price=float(pos.current_price),
                        entry_price=float(pos.avg_entry_price),
                        unrealized_pl_pct=float(pos.unrealized_plpc),
                    )
                )
            except (ValueError, AttributeError) as e:
                logger.warning("Could not parse position for %s: %s", pos.symbol, e)

        logger.info(
            "Portfolio: equity=%.2f cash=%.2f positions=%d",
            total_equity,
            cash,
            len(positions),
        )
        return Portfolio(total_equity=total_equity, cash=cash, positions=positions)

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------

    def sell_position(self, ticker: str, order_type: str = "market") -> Dict:
        """
        Sell the entire open position for a ticker.
        Used for stop-loss sells and thesis-invalidation sells.

        Returns a trade result dict compatible with Store.log_trade().
        """
        try:
            qty = self._get_position_qty(ticker)
            if qty <= 0:
                logger.warning("sell_position: no open position found for %s", ticker)
                return _error_result(ticker, "sell", "No open position found")

            if order_type == "limit":
                limit_price = self._get_last_close(ticker)
                order = self._api.submit_order(
                    symbol=ticker,
                    qty=qty,
                    side="sell",
                    type="limit",
                    limit_price=limit_price,
                    time_in_force="day",
                )
            else:
                order = self._api.submit_order(
                    symbol=ticker,
                    qty=qty,
                    side="sell",
                    type="market",
                    time_in_force="day",
                )

            result = _order_to_dict(order, ticker, "sell")
            logger.info("Sell order placed for %s: id=%s status=%s", ticker, result["order_id"], result["status"])
            return result

        except Exception as e:
            logger.error("sell_position failed for %s: %s", ticker, e)
            return _error_result(ticker, "sell", str(e))

    def place_order(
        self,
        ticker: str,
        action: str,
        amount: float,
        order_type: str = "market",
    ) -> Dict:
        """
        Place a buy or sell order.

        For buys: amount is the notional dollar value; Alpaca handles fractional shares.
        For sells: amount is ignored; the entire position is liquidated.

        Returns a trade result dict compatible with Store.log_trade().
        """
        if action == "sell":
            return self.sell_position(ticker, order_type)

        if action != "buy":
            return _error_result(ticker, action, f"Unknown action: {action}")

        try:
            notional = round(amount, 2)

            if order_type == "limit":
                limit_price = self._get_last_close(ticker)
                order = self._api.submit_order(
                    symbol=ticker,
                    notional=notional,
                    side="buy",
                    type="limit",
                    limit_price=limit_price,
                    time_in_force="day",
                )
            else:
                order = self._api.submit_order(
                    symbol=ticker,
                    notional=notional,
                    side="buy",
                    type="market",
                    time_in_force="day",
                )

            result = _order_to_dict(order, ticker, "buy", dollar_amount=amount)
            logger.info(
                "Buy order placed for %s: $%.2f id=%s status=%s",
                ticker,
                amount,
                result["order_id"],
                result["status"],
            )
            return result

        except Exception as e:
            logger.error("place_order failed for %s: %s", ticker, e)
            return _error_result(ticker, "buy", str(e))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_position_qty(self, ticker: str) -> float:
        try:
            pos = self._api.get_position(ticker)
            return float(pos.qty)
        except Exception:
            return 0.0

    def _get_last_close(self, ticker: str) -> Optional[float]:
        """Fetch the most recent closing price for use in limit orders."""
        try:
            bars = self._api.get_bars(ticker, "1Day", limit=1).df
            if not bars.empty:
                return float(bars["close"].iloc[-1])
        except Exception as e:
            logger.warning("Could not fetch last close for %s: %s", ticker, e)
        return None


# ------------------------------------------------------------------
# Module-level helpers (no self)
# ------------------------------------------------------------------

def _order_to_dict(order, ticker: str, action: str, dollar_amount: Optional[float] = None) -> Dict:
    return {
        "ticker": ticker,
        "action": action,
        "quantity": float(order.qty) if order.qty else None,
        "dollar_amount": dollar_amount,
        "order_type": order.order_type,
        "fill_price": float(order.filled_avg_price) if order.filled_avg_price else None,
        "order_id": order.id,
        "status": order.status,
        "error_message": None,
    }


def _error_result(ticker: str, action: str, message: str) -> Dict:
    return {
        "ticker": ticker,
        "action": action,
        "quantity": None,
        "dollar_amount": None,
        "order_type": None,
        "fill_price": None,
        "order_id": None,
        "status": "error",
        "error_message": message,
    }
