"""
Loads config.yaml + .env and exposes a single typed Config object.
All other modules import Config from here — no direct yaml/env reads elsewhere.
"""

import os
from dataclasses import dataclass
from typing import List

import yaml
from dotenv import load_dotenv


@dataclass
class TickerConfig:
    equities: List[str]
    tickers_to_analyze_per_day: int


@dataclass
class Config:
    # --- Ticker universe ---
    tickers: TickerConfig

    # --- Portfolio rules ---
    conviction_threshold: float
    cash_reserve_pct: float

    # --- Risk management ---
    stop_loss_pct: float
    portfolio_drawdown_limit_pct: float

    # --- Execution ---
    order_type: str
    paper_trading: bool

    # --- Research settings ---
    max_debate_rounds: int
    max_daily_research_runs: int
    llm_provider: str
    analyst_model: str
    trader_model: str

    # --- Screener settings ---
    screener_momentum_weight: float
    screener_volume_weight: float

    # --- Schedule ---
    run_time_et: str

    # --- Notifications ---
    email_enabled: bool
    email_to: str
    email_from: str
    # --- From .env ---
    openrouter_api_key: str
    alpaca_api_key: str
    alpaca_secret_key: str
    alpaca_base_url: str
    alpha_vantage_api_key: str
    finnhub_api_key: str
    brevo_api_key: str

def load_config(config_path: str = "config.yaml") -> Config:
    """Load config.yaml and .env, return a validated Config object."""
    load_dotenv()

    with open(config_path, "r") as f:
        raw = yaml.safe_load(f)

    tickers_raw = raw["tickers"]
    tickers = TickerConfig(
        equities=tickers_raw["equities"],
        tickers_to_analyze_per_day=tickers_raw["tickers_to_analyze_per_day"],
    )

    def _require_env(key: str) -> str:
        val = os.getenv(key, "")
        if not val:
            raise EnvironmentError(
                f"Required environment variable '{key}' is not set. "
                f"Copy .env.example to .env and fill in the values."
            )
        return val

    return Config(
        tickers=tickers,
        conviction_threshold=raw["conviction_threshold"],
        cash_reserve_pct=raw["cash_reserve_pct"],
        stop_loss_pct=raw["stop_loss_pct"],
        portfolio_drawdown_limit_pct=raw["portfolio_drawdown_limit_pct"],
        order_type=raw["order_type"],
        paper_trading=raw["paper_trading"],
        max_debate_rounds=raw["max_debate_rounds"],
        max_daily_research_runs=raw["max_daily_research_runs"],
        llm_provider=raw["llm_provider"],
        analyst_model=raw["analyst_model"],
        trader_model=raw["trader_model"],
        screener_momentum_weight=raw["screener_momentum_weight"],
        screener_volume_weight=raw["screener_volume_weight"],
        run_time_et=raw["run_time_et"],
        email_enabled=raw["email_enabled"],
        email_to=raw.get("email_to", ""),
        email_from=raw.get("email_from", ""),
        openrouter_api_key=_require_env("OPENROUTER_API_KEY"),
        alpaca_api_key=_require_env("ALPACA_API_KEY"),
        alpaca_secret_key=_require_env("ALPACA_SECRET_KEY"),
        alpaca_base_url=os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        alpha_vantage_api_key=_require_env("ALPHA_VANTAGE_API_KEY"),
        finnhub_api_key=_require_env("FINNHUB_API_KEY"),
        brevo_api_key=os.getenv("BREVO_API_KEY", ""),
    )
