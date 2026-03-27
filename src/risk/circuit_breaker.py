"""
Risk circuit breakers — runs first, before any research or trading.

Two checks:
  1. Portfolio drawdown breaker: if total portfolio is down >15% from peak, halt trading.
  2. Per-position stop-loss scan: if any position is down >8% from entry, sell immediately.

Both thresholds are read from config (never hardcoded here).
"""

import logging
from typing import List, Tuple

from src.config import Config

logger = logging.getLogger(__name__)


def check_portfolio_drawdown(
    total_equity: float,
    peak_equity: float,
    config: Config,
) -> Tuple[float, bool]:
    """
    Compute current drawdown and decide whether to halt trading.

    Args:
        total_equity: Current portfolio value from Alpaca.
        peak_equity:  Historical peak equity stored in SQLite.
        config:       Config object with portfolio_drawdown_limit_pct.

    Returns:
        (drawdown_pct, should_halt) where drawdown_pct is a positive fraction
        (0.05 = 5% drawdown) and should_halt is True if trading must stop.
    """
    if peak_equity <= 0:
        logger.warning("peak_equity is 0 or negative — skipping drawdown check")
        return 0.0, False

    drawdown = (peak_equity - total_equity) / peak_equity
    should_halt = drawdown >= config.portfolio_drawdown_limit_pct

    if should_halt:
        logger.warning(
            "DRAWDOWN BREAKER: %.1f%% drawdown exceeds limit of %.1f%%",
            drawdown * 100,
            config.portfolio_drawdown_limit_pct * 100,
        )
    else:
        logger.info("Drawdown check passed: %.2f%% (limit: %.1f%%)",
                    drawdown * 100, config.portfolio_drawdown_limit_pct * 100)

    return drawdown, should_halt


def scan_stop_losses(positions: List, config: Config) -> List[str]:
    """
    Return a list of ticker symbols that have breached the stop-loss threshold.

    A position breaches stop-loss when:
      (current_price - entry_price) / entry_price <= -stop_loss_pct

    Args:
        positions: List of Position objects (must have .ticker, .current_price, .entry_price).
        config:    Config object with stop_loss_pct.

    Returns:
        List of ticker symbols that should be sold immediately.
    """
    to_sell: List[str] = []

    for pos in positions:
        if pos.entry_price <= 0:
            logger.warning("Position %s has entry_price=0 — skipping stop-loss check", pos.ticker)
            continue

        loss_pct = (pos.current_price - pos.entry_price) / pos.entry_price

        if loss_pct <= -config.stop_loss_pct:
            logger.warning(
                "STOP-LOSS TRIGGERED: %s is down %.1f%% from entry (limit: -%.1f%%)",
                pos.ticker,
                loss_pct * 100,
                config.stop_loss_pct * 100,
            )
            to_sell.append(pos.ticker)
        else:
            logger.debug(
                "%s P&L from entry: %.2f%% (stop-loss at -%.1f%%)",
                pos.ticker,
                loss_pct * 100,
                config.stop_loss_pct * 100,
            )

    return to_sell
