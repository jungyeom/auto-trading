"""
Portfolio optimizer — equal-weight allocation with hard constraints.

Input : list of approved agent decisions + current portfolio state + config.
Output: list of concrete orders: [{ticker, action, dollar_amount}]

Rules (applied in order):
  1. Filter to decisions with conviction >= conviction_threshold.
  2. Process all "sell" decisions first (existing positions only).
  3. For "buy" decisions, compute target allocation:
       target_size = (total_equity * (1 - cash_reserve_pct)) / num_total_active_positions
  4. Cap each position at max_position_pct.
  5. Reject any buy that would push a GICS sector over max_sector_pct of total_equity.
  6. For buys: place notional (dollar-based) order for the gap between target and current value.
     If the position doesn't exist yet, the full target_size is the order amount.
"""

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.config import Config
from src.execution.alpaca_client import Portfolio

logger = logging.getLogger(__name__)


@dataclass
class Order:
    ticker: str
    action: str          # "buy" | "sell"
    dollar_amount: Optional[float]  # None for sells (full position liquidation)


def optimize_portfolio(
    decisions: List[Dict],
    portfolio: Portfolio,
    config: Config,
) -> List[Order]:
    """
    Translate agent decisions into executable orders, respecting all hard constraints.

    Args:
        decisions: List of decision dicts with keys: ticker, action, conviction.
        portfolio: Current portfolio from Alpaca.
        config:    Config with all allocation / constraint params.

    Returns:
        List of Order objects ready for execution.
    """
    orders: List[Order] = []

    # Step 1: Filter to decisions that meet conviction threshold
    actionable = [
        d for d in decisions
        if d.get("conviction", 0.0) >= config.conviction_threshold
        and d.get("action") in ("buy", "sell")
    ]

    sell_tickers = {d["ticker"] for d in actionable if d["action"] == "sell"}
    buy_tickers  = [d["ticker"] for d in actionable if d["action"] == "buy"]

    # Step 2: Queue all sell orders (thesis invalidation exits)
    for ticker in sell_tickers:
        orders.append(Order(ticker=ticker, action="sell", dollar_amount=None))
        logger.info("SELL queued: %s (thesis invalidation)", ticker)

    # Step 3: Determine the full active universe after sells execute
    existing_tickers = {p.ticker for p in portfolio.positions}
    remaining_existing = existing_tickers - sell_tickers  # positions we're keeping

    # Deduplicate buy list: skip tickers we already own (hold decisions cover those)
    new_buys = [t for t in buy_tickers if t not in existing_tickers]

    all_active = remaining_existing | set(new_buys)
    num_positions = len(all_active)

    if num_positions == 0 or not new_buys:
        return orders

    # Step 4: Compute target per-position size
    investable = portfolio.total_equity * (1 - config.cash_reserve_pct)
    target_size = investable / num_positions
    target_size = min(target_size, portfolio.total_equity * config.max_position_pct)

    logger.info(
        "Optimizer: %d active positions, target_size=%.2f, investable=%.2f",
        num_positions,
        target_size,
        investable,
    )

    # Step 5: Sector constraint check — compute current sector exposure (post-sells)
    sector_exposure: Dict[str, float] = {}
    for pos in portfolio.positions:
        if pos.ticker in sell_tickers:
            continue
        sector = config.sector_for(pos.ticker)
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + pos.market_value

    # Step 6: Process each new buy, applying sector constraint
    for ticker in new_buys:
        sector = config.sector_for(ticker)
        projected_sector = sector_exposure.get(sector, 0.0) + target_size
        sector_pct = projected_sector / portfolio.total_equity

        if sector_pct > config.max_sector_pct:
            logger.warning(
                "SECTOR LIMIT: skipping buy of %s — %s sector would be %.1f%% (limit %.1f%%)",
                ticker,
                sector,
                sector_pct * 100,
                config.max_sector_pct * 100,
            )
            continue

        # Check available cash
        already_committed = sum(o.dollar_amount or 0 for o in orders if o.action == "buy")
        available_cash = portfolio.cash - already_committed
        if available_cash < target_size * 0.5:
            logger.warning(
                "Insufficient cash for %s: need %.2f, have %.2f available",
                ticker,
                target_size,
                available_cash,
            )
            continue

        orders.append(Order(ticker=ticker, action="buy", dollar_amount=target_size))
        sector_exposure[sector] = sector_exposure.get(sector, 0.0) + target_size
        logger.info("BUY queued: %s $%.2f", ticker, target_size)

    return orders
