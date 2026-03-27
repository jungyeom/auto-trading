"""
TradingAgents research runner — wraps TradingAgentsGraph.propagate().

For each ticker:
  1. Builds the TradingAgentsGraph with our OpenRouter config.
  2. Formats portfolio_context and existing_thesis strings.
  3. Calls propagate(ticker, date, portfolio_context, existing_thesis).
  4. Parses the trader's structured JSON from trader_investment_plan.
  5. Uses the portfolio manager's final_trade_decision as the authoritative action
     (it may approve, reject, or downgrade the trader's recommendation).
  6. Returns a unified decision dict.

The TradingAgentsGraph instance is re-created per ticker to avoid memory bleed
between runs on the constrained 1GB droplet.
"""

import json
import logging
import os
import re
import sys
from datetime import date
from typing import Any, Dict, List, Optional

from src.config import Config
from src.execution.alpaca_client import Portfolio

logger = logging.getLogger(__name__)

# Ensure the tradingagents package (our fork) is importable
_TA_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../tradingagents"))
if _TA_PATH not in sys.path:
    sys.path.insert(0, _TA_PATH)


def run_research(
    ticker: str,
    trade_date: date,
    config: Config,
    portfolio: Portfolio,
    existing_thesis: Optional[Dict] = None,
) -> Dict[str, Any]:
    """
    Run the full TradingAgents pipeline for one ticker and return a decision dict.

    Args:
        ticker:          Ticker symbol (e.g. "NVDA").
        trade_date:      The date for which we are making the decision.
        config:          Loaded Config object.
        portfolio:       Current portfolio (used to build portfolio_context string).
        existing_thesis: Dict with 'thesis' and 'invalidation_conditions' keys,
                         or None for new opportunities.

    Returns:
        Decision dict: {
            ticker, action, conviction, thesis, invalidation_conditions, reasoning,
            is_existing_position, raw_trader_plan, raw_final_decision
        }
    """
    from tradingagents.graph.trading_graph import TradingAgentsGraph  # deferred import

    ta_config = _build_ta_config(config)
    portfolio_context_str = _format_portfolio_context(portfolio)
    existing_thesis_str = _format_existing_thesis(existing_thesis)

    logger.info("Running TradingAgents for %s on %s", ticker, trade_date)
    graph = TradingAgentsGraph(
        selected_analysts=["market", "social", "news", "fundamentals"],
        debug=False,
        config=ta_config,
    )

    final_state, processed_signal = graph.propagate(
        company_name=ticker,
        trade_date=str(trade_date),
        portfolio_context=portfolio_context_str,
        existing_thesis=existing_thesis_str,
    )

    trader_plan_raw = final_state.get("trader_investment_plan", "")
    final_decision_raw = final_state.get("final_trade_decision", "")

    # Parse trader's structured JSON output
    trader_json = _parse_trader_json(trader_plan_raw)

    # Reconcile with portfolio manager's final decision
    decision = _reconcile_decision(ticker, trader_json, processed_signal, final_decision_raw)
    decision["is_existing_position"] = existing_thesis is not None
    decision["raw_trader_plan"] = trader_plan_raw
    decision["raw_final_decision"] = final_decision_raw

    logger.info(
        "Decision for %s: action=%s conviction=%.2f",
        ticker,
        decision["action"],
        decision.get("conviction", 0.0),
    )
    return decision


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _build_ta_config(config: Config) -> Dict:
    """Build the TradingAgents config dict from our typed Config."""
    import os
    return {
        "project_dir": os.path.abspath(os.path.join(
            os.path.dirname(__file__), "../../tradingagents/tradingagents"
        )),
        "results_dir": "./logs/ta_results",
        "data_cache_dir": os.path.abspath("data/cache/ta"),
        # LLM: OpenRouter with tiered models
        "llm_provider": "openrouter",
        "deep_think_llm": config.trader_model,    # Gemini 3 Flash — trader + risk manager
        "quick_think_llm": config.analyst_model,  # Gemini 3.1 Flash Lite — analysts
        # Debate settings
        "max_debate_rounds": config.max_debate_rounds,
        "max_risk_discuss_rounds": 1,
        "max_recur_limit": 100,
        # Data vendors: use yfinance (built into TradingAgents, no extra API key)
        "data_vendors": {
            "core_stock_apis": "yfinance",
            "technical_indicators": "yfinance",
            "fundamental_data": "yfinance",
            "news_data": "yfinance",
        },
    }


def _format_portfolio_context(portfolio: Portfolio) -> str:
    """Format current portfolio into a concise string for the trader prompt."""
    lines = [
        f"Total equity: ${portfolio.total_equity:,.2f}",
        f"Cash available: ${portfolio.cash:,.2f}",
    ]
    if portfolio.positions:
        lines.append("Current positions:")
        for pos in portfolio.positions:
            lines.append(
                f"  {pos.ticker}: ${pos.market_value:,.2f} "
                f"({pos.unrealized_pl_pct:+.1%} unrealized P&L)"
            )
    else:
        lines.append("No current positions.")
    return "\n".join(lines)


def _format_existing_thesis(existing_thesis: Optional[Dict]) -> str:
    """Format the stored thesis dict into a string for the trader prompt."""
    if not existing_thesis:
        return ""
    parts = [f"Thesis: {existing_thesis.get('thesis', 'N/A')}"]
    conditions = existing_thesis.get("invalidation_conditions", [])
    if conditions:
        parts.append("Invalidation conditions:")
        for c in conditions:
            parts.append(f"  - {c}")
    return "\n".join(parts)


def _parse_trader_json(trader_plan: str) -> Optional[Dict]:
    """
    Extract the JSON block from the trader's output.
    Looks for ```json ... ``` first, then falls back to raw JSON detection.
    """
    # Try ```json ... ``` block first
    match = re.search(r"```json\s*(\{.*?\})\s*```", trader_plan, re.DOTALL | re.IGNORECASE)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Fallback: find first { ... } block
    match = re.search(r"\{[^{}]*\"action\"[^{}]*\}", trader_plan, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse trader JSON from output: %.200s", trader_plan)
    return None


def _reconcile_decision(
    ticker: str,
    trader_json: Optional[Dict],
    processed_signal: str,
    final_decision_raw: str,
) -> Dict[str, Any]:
    """
    Combine the trader's structured JSON with the portfolio manager's final decision.

    The portfolio manager's rating is authoritative for the action.
    The trader's JSON provides conviction, thesis, and invalidation_conditions.
    """
    # Map portfolio manager rating words → canonical actions
    _PM_SIGNAL_MAP = {
        "buy": "buy",
        "overweight": "buy",
        "hold": "hold",
        "underweight": "sell",
        "sell": "sell",
    }

    # Determine action from portfolio manager signal
    signal_lower = (processed_signal or "hold").lower()
    action = _PM_SIGNAL_MAP.get(signal_lower, "hold")

    if trader_json:
        conviction = float(trader_json.get("conviction", 0.5))
        thesis = trader_json.get("thesis", "")
        invalidation_conditions = trader_json.get("invalidation_conditions", [])
        reasoning = trader_json.get("reasoning", "")

        # If portfolio manager contradicts trader (e.g. PM says sell but trader said buy),
        # keep PM action but reduce conviction to 0 so it clears the threshold filter.
        trader_action = trader_json.get("action", "hold").lower()
        if action != trader_action and trader_action != "hold":
            logger.info(
                "%s: PM action '%s' overrides trader action '%s' — setting conviction=0",
                ticker,
                action,
                trader_action,
            )
            conviction = 0.0
    else:
        # No JSON parsed — use signal only, assign moderate conviction
        conviction = 0.5 if action == "buy" else 0.0
        thesis = ""
        invalidation_conditions = []
        reasoning = final_decision_raw[:500] if final_decision_raw else ""

    return {
        "ticker": ticker,
        "action": action,
        "conviction": conviction,
        "thesis": thesis,
        "invalidation_conditions": invalidation_conditions,
        "reasoning": reasoning,
    }
