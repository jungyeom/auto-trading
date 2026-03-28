"""
Auto-trader daily pipeline entry point.

Executed by cron at 4:30pm ET Mon–Fri via: python -m src.main

Pipeline sequence (matches spec §7 exactly):
  0. Load config + initialise clients
  1. Risk check: drawdown breaker → halt or continue
  2. Stop-loss scan → immediate sells
  3. Universe screening → rank new opportunity tickers
  4. Research loop: existing positions first, then new opportunities
  5. Portfolio optimization → order list
  6. Execution → place orders on Alpaca
  7. Save daily portfolio snapshot
  8. Send daily email summary
"""

import logging
import logging.handlers
import os
import sys
from datetime import date, timedelta

# Allow running as `python -m src.main` from project root
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import load_config
from src.data.alpha_vantage import AlphaVantageClient
from src.db.store import Store
from src.execution.alpaca_client import AlpacaClient
from src.notifications.email_alert import send_alert, send_daily_summary
from src.portfolio.optimizer import optimize_portfolio
from src.research.runner import run_research
from src.risk.circuit_breaker import check_portfolio_drawdown, scan_stop_losses
from src.screener.universe import rank_tickers

_LOG_DIR = "logs"
_DB_PATH = "logs/trades.db"


def _setup_logging() -> None:
    os.makedirs(_LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    # Rotating file handler: max 10 MB, 5 backups
    fh = logging.handlers.RotatingFileHandler(
        os.path.join(_LOG_DIR, "app.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
    )
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # Console output (also captured by cron into cron.log)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)


logger = logging.getLogger(__name__)


def run_daily_pipeline() -> None:
    """Execute the full daily pipeline. All errors are caught per-step."""

    # ------------------------------------------------------------------
    # 0. Load config + initialise clients
    # ------------------------------------------------------------------
    config = load_config("config.yaml")
    alpaca = AlpacaClient(config)
    av_client = AlphaVantageClient(config.alpha_vantage_api_key)
    db = Store(_DB_PATH)
    db.enable_wal_mode()

    today = date.today()
    logger.info("=== Auto-trader daily run: %s ===", today)

    # ------------------------------------------------------------------
    # 1. Read portfolio and update peak equity
    # ------------------------------------------------------------------
    portfolio = alpaca.get_portfolio()

    peak_equity = db.get_peak_equity() or portfolio.total_equity
    if portfolio.total_equity > peak_equity:
        peak_equity = portfolio.total_equity

    # ------------------------------------------------------------------
    # 2. Drawdown breaker check — halt immediately if limit breached
    # ------------------------------------------------------------------
    drawdown, should_halt = check_portfolio_drawdown(
        portfolio.total_equity, peak_equity, config
    )

    if should_halt:
        send_alert(
            "DRAWDOWN BREAKER ACTIVATED",
            f"Portfolio drawdown: {drawdown:.1%}. Limit: {config.portfolio_drawdown_limit_pct:.1%}. "
            f"All trading paused. Manual review required.",
            config,
        )
        db.save_snapshot(portfolio, peak_equity, drawdown)
        logger.warning("Drawdown breaker activated — exiting pipeline")
        return

    # ------------------------------------------------------------------
    # 3. Stop-loss scan — sell breached positions immediately
    # ------------------------------------------------------------------
    stop_loss_tickers = scan_stop_losses(portfolio.positions, config)
    stop_loss_sells = []

    for ticker in stop_loss_tickers:
        logger.info("Executing stop-loss sell: %s", ticker)
        result = alpaca.sell_position(ticker, config.order_type)
        db.log_trade(result)
        stop_loss_sells.append(result)

        loss_pct = next(
            (p.unrealized_pl_pct for p in portfolio.positions if p.ticker == ticker),
            0.0,
        )
        send_alert(
            f"STOP-LOSS TRIGGERED: {ticker}",
            f"Position down {loss_pct:.1%} from entry. Sell order placed. "
            f"Order ID: {result.get('order_id', 'n/a')}",
            config,
        )

    # Re-read portfolio after any stop-loss sells
    if stop_loss_sells:
        portfolio = alpaca.get_portfolio()

    # ------------------------------------------------------------------
    # 4. Determine research targets
    # ------------------------------------------------------------------
    existing_tickers = [p.ticker for p in portfolio.positions]
    all_tickers = config.tickers.equities
    new_candidates = [t for t in all_tickers if t not in existing_tickers]

    # Skip existing positions bought within the last 7 days — no thesis review needed yet
    cutoff = today - timedelta(days=7)
    existing_to_research = [
        t for t in existing_tickers
        if (last := db.get_last_buy_date(t)) is None or last <= cutoff.isoformat()
    ]
    skipped_recent = len(existing_tickers) - len(existing_to_research)
    if skipped_recent:
        logger.info("%d existing position(s) skipped (bought within 7 days)", skipped_recent)

    # Rank new candidates by momentum + volume
    try:
        ranked_new = rank_tickers(new_candidates, config, av_client)
    except Exception as e:
        logger.error("Screener failed: %s — using unranked order", e)
        ranked_new = new_candidates

    new_opportunity_tickers = ranked_new[: config.tickers.tickers_to_analyze_per_day]

    # Research list: eligible existing positions first (thesis check), then new opps
    research_list = existing_to_research + new_opportunity_tickers
    research_list = research_list[: config.max_daily_research_runs]  # hard cap

    logger.info(
        "Research targets: %d existing + %d new = %d total (cap: %d)",
        len(existing_to_research),
        len(new_opportunity_tickers),
        len(research_list),
        config.max_daily_research_runs,
    )

    # ------------------------------------------------------------------
    # 5. Research loop (sequential — lower memory footprint)
    # ------------------------------------------------------------------
    decisions = []

    for ticker in research_list:
        try:
            is_existing = ticker in existing_tickers
            existing_thesis = db.get_latest_thesis(ticker) if is_existing else None

            decision = run_research(
                ticker=ticker,
                trade_date=today,
                config=config,
                portfolio=portfolio,
                existing_thesis=existing_thesis,
            )

            decision["is_existing_position"] = is_existing
            decisions.append(decision)
            db.log_decision(decision)

        except Exception as e:
            logger.error("Research failed for %s: %s", ticker, e)
            # Never let one ticker crash the whole pipeline — continue

    # ------------------------------------------------------------------
    # 6. Portfolio optimization
    # ------------------------------------------------------------------
    orders = optimize_portfolio(decisions, portfolio, config)

    # ------------------------------------------------------------------
    # 7. Execution
    # ------------------------------------------------------------------
    results = []

    for order in orders:
        try:
            result = alpaca.place_order(
                ticker=order.ticker,
                action=order.action,
                amount=order.dollar_amount or 0.0,
                order_type=config.order_type,
            )
            db.log_trade(result)
            results.append(result)

            if result.get("status") == "error":
                send_alert(
                    f"ORDER FAILED: {order.ticker}",
                    f"Action: {order.action}  Amount: ${order.dollar_amount}\n"
                    f"Error: {result.get('error_message')}",
                    config,
                )

        except Exception as e:
            logger.error("Order failed for %s: %s", order.ticker, e)
            send_alert(f"ORDER FAILED: {order.ticker}", str(e), config)

    # ------------------------------------------------------------------
    # 8. Save daily snapshot
    # ------------------------------------------------------------------
    final_portfolio = alpaca.get_portfolio()
    spy_comparison = _get_spy_comparison(db, av_client, final_portfolio.total_equity)
    db.save_snapshot(final_portfolio, peak_equity, drawdown, spy_comparison)

    # ------------------------------------------------------------------
    # 9. Send daily email summary
    # ------------------------------------------------------------------
    errors = db.get_todays_errors()
    send_daily_summary(
        portfolio=final_portfolio,
        stop_loss_sells=stop_loss_sells,
        decisions=decisions,
        results=results,
        spy_comparison=spy_comparison,
        errors=errors,
        config=config,
    )

    logger.info("=== Daily pipeline complete ===")


def _get_spy_comparison(db: Store, av_client: AlphaVantageClient, current_equity: float) -> float:
    """
    Portfolio return vs SPY return since inception, in percentage points.
    e.g. +3.5 means portfolio beat SPY by 3.5pp since first run.
    Returns 0.0 on first run or if data is unavailable.
    """
    inception = db.get_inception_snapshot()
    if not inception:
        return 0.0

    try:
        inception_equity = float(inception["total_equity"])
        if inception_equity == 0:
            return 0.0

        portfolio_return_pct = (current_equity - inception_equity) / inception_equity * 100

        spy_series = av_client.get_time_series_daily("SPY", outputsize="full")
        if not spy_series:
            return 0.0

        inception_date = inception["timestamp"][:10]  # "YYYY-MM-DD"
        today_str = date.today().isoformat()
        dates = sorted(spy_series.keys())

        spy_dates_from = [d for d in dates if d >= inception_date]
        spy_dates_to   = [d for d in dates if d <= today_str]
        if not spy_dates_from or not spy_dates_to:
            return 0.0

        spy_start = float(spy_series[spy_dates_from[0]]["4. close"])
        spy_end   = float(spy_series[spy_dates_to[-1]]["4. close"])
        if spy_start == 0:
            return 0.0

        spy_return_pct = (spy_end - spy_start) / spy_start * 100
        return round(portfolio_return_pct - spy_return_pct, 2)

    except Exception as e:
        logger.warning("Could not compute SPY comparison: %s", e)
        return 0.0


if __name__ == "__main__":
    _setup_logging()
    run_daily_pipeline()
