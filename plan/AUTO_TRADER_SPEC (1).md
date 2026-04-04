# Auto-trader: architecture & implementation spec

> **Version:** 0.3 — March 26, 2026
> **Author:** Kevin
> **Purpose:** This document is the single source of truth for Claude Code to implement the auto-trader project. Every architecture decision, configuration default, file structure, and implementation detail is specified here. If something is not in this document, ask before assuming.

---

## 1. Project overview

An automated investment system that uses a team of LLM-powered agents to research, debate, and execute trades on US equities. The system runs daily, makes medium-frequency trades (1-month+ average holding period), and aims to beat the S&P 500 over a 1-year experiment.

### 1.1 Goals

- Beat S&P 500 total returns over 1 year
- Fully automated execution with no daily intervention
- Average holding period of 1 month+, with early exits on adverse scenarios
- Multi-agent research system mimicking a real trading desk
- Monthly LLM budget of $15–30

### 1.2 Constraints

- Starting capital: ~$10,000
- Asset universe: 15 US equities (top by market cap), configurable to expand later
- No crypto for v1 (insufficient data for sound agent-driven decisions)
- Max ~8–10 concurrent positions, equal-weighted with fractional shares
- All data API usage must fit within free tiers (aggressive caching)
- Runs on a dedicated DigitalOcean droplet (1GB RAM / 1vCPU, Ubuntu)
- Add 1GB swap file on the droplet as a memory safety buffer

---

## 2. System architecture

The system has six layers. Each layer has a single responsibility and communicates via Python function calls (no message queues or microservices).

```
┌─────────────────────────────────────────────────┐
│  Cron (4:30pm ET, Mon–Fri)                      │
│    │                                            │
│    ▼                                            │
│  1. Risk check                                  │
│    ├── Portfolio drawdown > limit? → email, exit │
│    └── Per-position stop-loss scan → sell, email │
│    │                                            │
│    ▼                                            │
│  2. Universe screener                           │
│    └── Rank 15 tickers, pick top N new opps     │
│    │                                            │
│    ▼                                            │
│  3. TradingAgents research (per ticker)         │
│    ├── Existing positions first (thesis check)  │
│    ├── Then new opportunities from screener     │
│    ├── 4 analysts (parallel reports)            │
│    ├── Bull vs Bear debate (1 round)            │
│    ├── Trader synthesis → decision JSON         │
│    └── Risk manager → approve/reject/resize     │
│    │                                            │
│    ▼                                            │
│  4. Portfolio optimizer                         │
│    └── Equal-weight + hard constraints          │
│    │                                            │
│    ▼                                            │
│  5. Execution (Alpaca API)                      │
│    └── Place orders (market or limit)           │
│    │                                            │
│    ▼                                            │
│  6. Logging + email summary                     │
└─────────────────────────────────────────────────┘
```

---

## 3. Detailed layer specifications

### 3.1 Layer 1: Risk check

**Runs first, before any research or trading.**

1. Read current portfolio from Alpaca API (positions, cash, total equity).
2. Compute portfolio drawdown: `(peak_equity - current_equity) / peak_equity`. Peak equity is tracked in SQLite and updated daily.
3. If drawdown exceeds `portfolio_drawdown_limit_pct` (configurable, default 0.15): send alert email, skip all trading for the day, exit.
4. For each open position, compare current price to entry price. If any position is down more than `stop_loss_pct` (configurable, default 0.08): place a sell order immediately, log the trade, send email alert.

**Important:** Stop-loss sells happen before any new research runs. This is a safety-first design.

**Cold start:** On the very first run (no data in SQLite), set `peak_equity` to the current account balance. No positions exist, so stop-loss scan is a no-op.

### 3.2 Layer 2: Universe screener

**Input:** The fixed ticker list from `config.yaml` (default 15 equities).
**Output:** A ranked list of the top `tickers_to_analyze_per_day` (configurable, default 8) NEW opportunity tickers (excluding tickers that already have open positions, since those are analyzed separately in Layer 3).

Ranking logic (simple for v1):
- Fetch daily price data for all tickers (1 bulk Alpha Vantage call).
- Score each ticker using a configurable composite formula:
  - 30-day momentum (price change %): weight `screener_momentum_weight` (default 0.6)
  - 10-day relative volume (vs 50-day average): weight `screener_volume_weight` (default 0.4)
- Sort by score descending, return top N.
- Exclude tickers that already have an open position (those go through thesis invalidation in Layer 3 instead).

### 3.3 Layer 3: TradingAgents research engine

**This is the core — uses the forked TradingAgents repo (built on LangGraph).**

**Research loop order:**
1. **Existing positions first (thesis invalidation).** For every ticker with an open position that was **bought more than 7 days ago**, run the full TradingAgents pipeline. The trader agent receives the stored thesis and invalidation conditions as additional context. If the agent returns `action: "sell"`, this is a thesis invalidation exit. Positions bought within the last 7 days are skipped — no immediate thesis review needed — freeing up LLM slots for new candidates.
2. **New opportunities second.** For each ticker selected by the screener (Layer 2), run the full TradingAgents pipeline.
3. **Hard cap.** The total number of TradingAgents runs per day must not exceed `max_daily_research_runs` (configurable, default 15). Existing eligible positions always take priority. If the cap is reached, remaining new opportunity tickers are skipped.

For each ticker, the pipeline calls `ta.propagate(ticker, date)`.

**Analyst team (4 agents, run in parallel within LangGraph):**

| Agent | Data source | What it produces |
|-------|-------------|------------------|
| Fundamentals analyst | Alpha Vantage (cached) | Signal + confidence + evidence on P/E, cash flow, earnings quality |
| Sentiment analyst | Finnhub alternative data | Sentiment score from social media, retail flow |
| News analyst | Finnhub news feed | Impact assessment of recent macro events, earnings |
| Technical analyst | Computed locally via pandas-ta | MACD, RSI, trend, volume signals |

**Researcher debate (1 round, configurable via `max_debate_rounds`):**
- Bull researcher reads all analyst reports, argues the case for buying.
- Bear researcher reads all analyst reports, argues the case for caution.
- They exchange one round of rebuttals. The debate transcript captures agreements, tensions, and key risks.

**Trader agent:**
- Receives: all 4 analyst reports + full debate transcript + current portfolio context (existing positions, cash available, recent trades).
- For existing positions: also receives the stored thesis and invalidation conditions from the original buy decision.
- Uses `trader_model` (Gemini 3 Flash) for higher reasoning quality.
- Outputs structured JSON:

```json
{
  "ticker": "NVDA",
  "action": "buy",
  "conviction": 0.78,
  "thesis": "Strong datacenter demand continues with no signs of slowdown...",
  "invalidation_conditions": [
    "Datacenter revenue growth drops below 20% YoY",
    "Major customer (e.g., Microsoft, Google) announces in-house chip shift"
  ],
  "reasoning": "Fundamentals strong (analyst 0.72 bullish), sentiment positive..."
}
```

- Only decisions with `conviction >= conviction_threshold` (configurable, default 0.6) proceed to the portfolio optimizer.

**Risk management + portfolio manager (within TradingAgents):**
- Evaluates the trader's proposal against current portfolio exposure.
- Can approve, reject, or resize the proposed position.
- Acts as the final gate before decisions leave the research engine.

**Custom modifications to the TradingAgents fork:**
1. Configure OpenRouter as the LLM provider with tiered models (see section 4 for exact model IDs).
2. Inject current portfolio context into the trader agent's prompt.
3. For existing positions: inject stored thesis + invalidation conditions into the trader agent's prompt.
4. Add thesis statement + invalidation conditions to the trader's output schema.
5. Reduce `max_debate_rounds` from 2 to 1.

### 3.4 Layer 4: Portfolio optimizer

**Input:** List of approved decisions from Layer 3 + current portfolio state from Alpaca.
**Output:** List of concrete orders: `{ticker, action, quantity_or_dollars}`.

Logic (rules-based, no optimization library):

1. Filter decisions to only those with `conviction >= conviction_threshold`.
2. Calculate target allocation:
   - Equal weight across all active positions (existing + new).
   - Target position size = `(total_equity * (1 - cash_reserve_pct)) / num_positions`.
   - No single-position cap — a stock can represent up to 85% of the portfolio.
3. Apply hard constraints (reject any allocation that violates):
   - Maintain at least `cash_reserve_pct` in cash (configurable, default 0.15).
   - Skip a buy if available cash is less than 50% of the target position size.
4. For "buy" decisions: calculate dollar amount to invest, submit as a notional (dollar-based) order. Alpaca handles fractional shares automatically.
5. For "sell" decisions: sell entire position (all shares).
6. For "hold" decisions: no action.

### 3.5 Layer 5: Execution

**Input:** List of orders from Layer 4.
**Output:** Placed orders on Alpaca.

Implementation:
- Use `alpaca-trade-api` Python SDK.
- Order type controlled by `order_type` config (default: `"market"`). Alternative: `"limit"` at last close price.
- For buys: submit notional (dollar-amount) order. Alpaca supports fractional shares via notional orders.
- For sells: submit market (or limit) order to sell entire position (qty-based).
- If `paper_trading` is `true` in config, use `https://paper-api.alpaca.markets` as the base URL. If `false`, use `https://api.alpaca.markets`.
- Log every order submission and its response (order ID, status, fill price) to SQLite.
- On order failure: log the error, send alert email, continue with remaining orders.

### 3.6 Layer 6: Logging + email summary

**Logging (SQLite with WAL mode enabled for safe concurrent reads):**
- `decisions` table: every agent decision (ticker, action, conviction, thesis, invalidation_conditions, full_reasoning, is_existing_position, timestamp).
- `trades` table: every order placed (ticker, action, quantity, dollar_amount, order_type, fill_price, order_id, status, timestamp).
- `portfolio_snapshots` table: daily snapshot (total_equity, cash, peak_equity, drawdown_pct, positions_json, spy_comparison_pct, timestamp).

**Cold start for SQLite:** On the first run, create all tables if they don't exist. Set initial `peak_equity` to account balance. `spy_comparison_pct` starts at 0.

**Email summary (sent at end of every pipeline run):**
- Use the SendGrid Python SDK (`sendgrid` package) to send email over HTTPS. API key stored in `.env` as `SENDGRID_API_KEY`.
- Subject: `"Auto-trader daily report — {date}"`.
- Body includes: new trades placed today, stop-loss sells triggered, existing position thesis checks (hold/sell), current positions with P&L, total portfolio value, portfolio vs SPY performance (simple % comparison since inception), any errors encountered during the run.
- Immediate alert emails (separate from the daily summary) for: stop-loss triggers, drawdown breaker activation, order failures.

---

## 4. Configuration

All tunable parameters live in `config.yaml` at the project root. The system reads this file at startup. **No hardcoded thresholds anywhere in the code.** Every number below must be read from config.

```yaml
# === Ticker universe ===
tickers:
  equities:
    - NVDA   # NVIDIA — Information Technology
    - AAPL   # Apple — Information Technology
    - GOOGL  # Alphabet — Communication Services
    - MSFT   # Microsoft — Information Technology
    - AMZN   # Amazon — Consumer Discretionary
    - AVGO   # Broadcom — Information Technology
    - TSM    # TSMC — Information Technology
    - TSLA   # Tesla — Consumer Discretionary
    - JPM    # JPMorgan — Financials
    - META   # Meta — Communication Services
    - BRK.B  # Berkshire Hathaway — Financials
    - LLY    # Eli Lilly — Healthcare
    - WMT    # Walmart — Consumer Staples
    - V      # Visa — Financials
    - UNH    # UnitedHealth — Healthcare
  tickers_to_analyze_per_day: 8    # New opportunities only (existing positions always analyzed)

# === Portfolio rules ===
conviction_threshold: 0.6         # Minimum conviction to enter a position
cash_reserve_pct: 0.15            # Always keep 15% cash (no single-position or sector caps)

# === Risk management ===
stop_loss_pct: 0.08               # Sell if position drops 8% from entry
portfolio_drawdown_limit_pct: 0.15 # Pause all trading if portfolio drops 15% from peak

# === Execution ===
order_type: "market"              # "market" or "limit" (limit uses last close price)
paper_trading: true               # true = paper trading, false = live trading

# === Research settings ===
max_debate_rounds: 1              # Number of bull/bear debate rounds
max_daily_research_runs: 15       # Hard cap on total TradingAgents runs per day
llm_provider: "openrouter"
analyst_model: "google/gemini-3.1-flash-lite-preview"   # Cheap model for analyst agents
trader_model: "google/gemini-3-flash-preview"            # Better model for debate + trader + risk

# === Screener settings ===
screener_momentum_weight: 0.6     # Weight for 30-day price momentum in ranking
screener_volume_weight: 0.4       # Weight for 10-day relative volume in ranking

# === Schedule ===
run_time_et: "16:30"              # 4:30pm ET, after market close

# === Notifications ===
email_enabled: true
email_to: ""                      # Recipient email address
email_from: ""                    # Verified sender in SendGrid
```

---

## 5. Environment variables

Stored in `.env` at the project root. Never committed to git. The `.env.example` file lists all required variables with empty values.

```env
# LLM
OPENROUTER_API_KEY=

# Brokerage
ALPACA_API_KEY=
ALPACA_SECRET_KEY=
ALPACA_BASE_URL=https://paper-api.alpaca.markets

# Market data
ALPHA_VANTAGE_API_KEY=
FINNHUB_API_KEY=

# Email (Gmail)
EMAIL_APP_PASSWORD=
```

---

## 6. Repository structure

The TradingAgents framework is forked directly into this repo under `tradingagents/`. Fork from the latest `main` commit at the time of implementation. All other code is custom.

```
auto-trader/
├── pyproject.toml                 # uv workspace + dependencies (source of truth for local dev)
├── uv.lock                        # Locked dependency versions
├── requirements.txt               # Legacy dep list used by VPS setup.sh (pip-based)
├── config.yaml                    # All tunable parameters (see section 4)
├── .env                           # API credentials (gitignored)
├── .env.example                   # Template with required variable names (committed)
├── README.md                      # Setup instructions + how to run
├── setup.sh                       # VPS one-command setup: venv, pip install, swap file
├── .gitignore                     # .env, data/cache/, logs/, venv/, __pycache__/
│
├── src/
│   ├── __init__.py
│   ├── main.py                    # Entry point: orchestrates the full daily pipeline
│   ├── config.py                  # Loads config.yaml + .env, exposes typed config object
│   │
│   ├── screener/
│   │   ├── __init__.py
│   │   └── universe.py            # Fixed ticker list, daily ranking logic
│   │
│   ├── research/
│   │   ├── __init__.py
│   │   └── runner.py              # Wraps TradingAgents propagate() call per ticker
│   │
│   ├── portfolio/
│   │   ├── __init__.py
│   │   └── optimizer.py           # Equal-weight allocation + hard constraints
│   │
│   ├── execution/
│   │   ├── __init__.py
│   │   └── alpaca_client.py       # Alpaca API: read positions, place orders
│   │
│   ├── risk/
│   │   ├── __init__.py
│   │   └── circuit_breaker.py     # Stop-loss scan + portfolio drawdown check
│   │
│   ├── notifications/
│   │   ├── __init__.py
│   │   └── email_alert.py         # Send emails via Gmail SMTP
│   │
│   ├── data/
│   │   ├── __init__.py
│   │   ├── alpha_vantage.py       # AV client: price + fundamentals, with caching
│   │   ├── finnhub_client.py      # Finnhub client: news + sentiment, with caching
│   │   └── cache.py               # File-based cache with TTL (see section 8)
│   │
│   └── db/
│       ├── __init__.py
│       ├── models.py              # SQLite table definitions (decisions, trades, portfolio_snapshots)
│       └── store.py               # Read/write functions for the database
│
├── tradingagents/                  # Forked TradingAgents code (uv workspace member)
│   ├── pyproject.toml             # TradingAgents package definition
│   ├── graph/
│   │   └── trading_graph.py       # Main LangGraph graph definition
│   ├── agents/                    # Agent definitions (analyst, researcher, trader, risk)
│   ├── default_config.py          # Default TradingAgents config (overridden by our config.yaml)
│   └── ...                        # Rest of TradingAgents source
│
├── data/
│   └── cache/                     # Cached API responses (gitignored)
│
├── eval_results/                   # TradingAgents per-ticker evaluation JSON output (gitignored)
│
├── logs/
│   ├── trades.db                  # SQLite database (gitignored)
│   ├── app.log                    # Application log (gitignored)
│   └── cron.log                   # Cron output log (gitignored)
│
├── cron/
│   └── run_daily.sh               # Cron wrapper: activates venv, runs main.py, with EDT/EST guard
│
└── tests/
    ├── test_screener.py
    ├── test_optimizer.py
    ├── test_circuit_breaker.py
    └── test_alpaca_client.py
```

---

## 7. Daily pipeline flow (main.py)

This is the exact sequence that `main.py` executes. Each step either succeeds and continues, or logs the error and sends an alert email. **This is pseudocode — Claude Code should implement the actual Python using these exact steps and error handling patterns.**

```python
def run_daily_pipeline():
    # 0. Load config + initialize clients
    config = load_config("config.yaml")   # Reads config.yaml + .env
    alpaca = AlpacaClient(config)
    db = Store("logs/trades.db")          # Creates tables on first run if they don't exist
    db.enable_wal_mode()                  # Enable WAL for safe concurrent reads

    # 1. Risk check
    portfolio = alpaca.get_portfolio()    # Returns: positions, cash, total_equity

    # Cold start: if no peak_equity in DB, set it to current total_equity
    peak_equity = db.get_peak_equity() or portfolio.total_equity
    if portfolio.total_equity > peak_equity:
        peak_equity = portfolio.total_equity
    db.update_peak_equity(peak_equity)

    drawdown = (peak_equity - portfolio.total_equity) / peak_equity
    if drawdown >= config.portfolio_drawdown_limit_pct:
        send_alert_email("DRAWDOWN BREAKER ACTIVATED",
            f"Drawdown: {drawdown:.1%}. Trading paused. Manual review required.")
        db.save_snapshot(portfolio, peak_equity, drawdown)
        return  # Exit early, no trading today

    # 2. Stop-loss scan
    stop_loss_sells = []
    for position in portfolio.positions:
        loss_pct = (position.current_price - position.entry_price) / position.entry_price
        if loss_pct <= -config.stop_loss_pct:
            result = alpaca.sell_position(position.ticker, config.order_type)
            db.log_trade(result)
            stop_loss_sells.append(result)
            send_alert_email(f"STOP-LOSS TRIGGERED: {position.ticker}",
                f"Loss: {loss_pct:.1%}. Position sold.")

    # Re-read portfolio after stop-loss sells
    if stop_loss_sells:
        portfolio = alpaca.get_portfolio()

    # 3. Determine research targets
    existing_tickers = [p.ticker for p in portfolio.positions]
    all_tickers = config.tickers.equities

    # Skip existing positions bought within the last 7 days — no thesis review needed yet
    cutoff = today - timedelta(days=7)
    existing_to_research = [
        t for t in existing_tickers
        if (last := db.get_last_buy_date(t)) is None or last <= cutoff.isoformat()
    ]
    skipped = len(existing_tickers) - len(existing_to_research)
    if skipped:
        log(f"{skipped} existing position(s) skipped (bought within 7 days)")

    # Screen for new opportunities (excludes existing positions)
    new_opportunity_tickers = rank_tickers(
        [t for t in all_tickers if t not in existing_tickers],
        config
    )[:config.tickers_to_analyze_per_day]

    # Research list: eligible existing positions first, then new opportunities
    research_list = existing_to_research + new_opportunity_tickers

    # Apply hard cap
    research_list = research_list[:config.max_daily_research_runs]

    # 4. Research loop (sequential, one ticker at a time)
    decisions = []
    for ticker in research_list:
        try:
            is_existing = ticker in existing_tickers

            # For existing positions, pass stored thesis + invalidation conditions
            existing_thesis = None
            if is_existing:
                existing_thesis = db.get_latest_thesis(ticker)

            decision = run_research(ticker, today, config,
                portfolio_context=portfolio,
                existing_thesis=existing_thesis)

            decision.is_existing_position = is_existing
            decisions.append(decision)
            db.log_decision(decision)
        except Exception as e:
            log_error(f"Research failed for {ticker}: {e}")
            continue  # Skip this ticker, proceed with others

    # 5. Portfolio optimization
    orders = optimize_portfolio(decisions, portfolio, config)

    # 6. Execution
    results = []
    for order in orders:
        try:
            result = alpaca.place_order(
                ticker=order.ticker,
                action=order.action,
                amount=order.dollar_amount,  # Notional for buys
                order_type=config.order_type
            )
            db.log_trade(result)
            results.append(result)
        except Exception as e:
            log_error(f"Order failed for {order.ticker}: {e}")
            send_alert_email(f"ORDER FAILED: {order.ticker}", str(e))

    # 7. Save daily snapshot
    final_portfolio = alpaca.get_portfolio()
    spy_return = get_spy_return_since_inception(db)  # Compare to SPY benchmark
    db.save_snapshot(final_portfolio, peak_equity, drawdown, spy_return)

    # 8. Daily email summary
    send_daily_summary(
        portfolio=final_portfolio,
        stop_loss_sells=stop_loss_sells,
        decisions=decisions,
        results=results,
        spy_comparison=spy_return,
        errors=get_todays_errors()
    )
```

**Error handling philosophy:** Never let one ticker's failure crash the whole pipeline. Log the error, alert via email, and continue with the remaining tickers. The pipeline should always complete and send the daily summary, even if every research run fails.

---

## 8. Data caching strategy

All API calls go through a caching layer to stay within free tier limits.

| Data type | Source | Cache TTL | Reason |
|-----------|--------|-----------|--------|
| Daily price (equities) | Alpha Vantage bulk quotes | 1 day | Changes daily |
| Fundamentals (income stmt, balance sheet) | Alpha Vantage | 90 days | Changes quarterly |
| Technical indicators | Computed locally (pandas-ta) | Not cached | Derived from price data |
| News articles | Finnhub | 4 hours | Updates frequently |
| Sentiment scores | Finnhub | 1 day | Aggregated daily |
| Insider transactions | Finnhub | 7 days | Updates weekly |

Cache implementation: file-based JSON cache in `data/cache/`, keyed by `{source}_{ticker}_{data_type}_{date}.json`. The `cache.py` module handles TTL checks and automatic refresh.

**Daily Alpha Vantage API usage: 1–3 calls** (well under the 25/day free tier limit).

---

## 9. Deployment (DigitalOcean)

### 9.1 Droplet setup

- Dedicated droplet: 1GB RAM / 1vCPU / Ubuntu 24.04
- Separate from the existing n8n droplet
- Add 1GB swap file as memory safety buffer:
  ```bash
  sudo fallocate -l 1G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
  ```
- Install Python 3.12+, pip, git
- Clone the repo, create venv, install requirements

### 9.2 Cron setup

Use two cron entries to handle EDT/EST automatically. The `run_daily.sh` script checks the current ET time and only runs if it's within the correct window, so both entries won't double-fire:

```bash
# EDT (March–November): 4:30pm ET = 20:30 UTC
30 20 * * 1-5 /home/kevin/auto-trader/cron/run_daily.sh >> /home/kevin/auto-trader/logs/cron.log 2>&1

# EST (November–March): 4:30pm ET = 21:30 UTC
30 21 * * 1-5 /home/kevin/auto-trader/cron/run_daily.sh >> /home/kevin/auto-trader/logs/cron.log 2>&1
```

`run_daily.sh` contents:
```bash
#!/bin/bash
set -e

# Only run if current ET hour is 16 (4pm). Prevents double-firing from dual cron entries.
ET_HOUR=$(TZ="America/New_York" date +%H)
if [ "$ET_HOUR" != "16" ]; then
    echo "$(date): Skipping — not 4pm ET (current ET hour: $ET_HOUR)"
    exit 0
fi

cd /home/kevin/auto-trader
source venv/bin/activate
python -m src.main 2>&1
```

### 9.3 Monitoring the droplet

- Cron output logs to `logs/cron.log`
- Application errors logged to `logs/app.log` (Python logging module, rotating file handler, max 10MB per file, 5 backups)
- Email alerts on any pipeline failure
- Alpaca's web dashboard (app.alpaca.markets) for portfolio monitoring — no custom dashboard for v1

---

## 10. Tech stack summary

| Component | Technology | Notes |
|-----------|-----------|-------|
| Language | Python 3.12+ | |
| Package manager | uv | Workspace with tradingagents as member; VPS uses pip + requirements.txt |
| Agent framework | LangGraph (via forked TradingAgents) | Fork from latest main commit |
| LLM provider | OpenRouter | Single API key |
| LLM models | Gemini 3.1 Flash Lite (analysts) + Gemini 3 Flash (debate/trader/risk) | ~$8–12/month estimated |
| Brokerage | Alpaca | Commission-free, paper + live, fractional shares |
| Market data (equities) | Alpha Vantage | Free tier, 25 req/day |
| News + sentiment | Finnhub | Free tier, 60 req/min |
| Technical indicators | pandas-ta | Computed locally, no API calls |
| Database | SQLite (WAL mode) | Decisions, trades, portfolio snapshots |
| Email | SendGrid | HTTP API via `sendgrid` Python SDK, free tier |
| Deployment | DigitalOcean droplet | 1GB/1vCPU + 1GB swap |
| Scheduling | Cron | Mon–Fri 4:30pm ET (dual entries for EDT/EST) |
| Portfolio monitoring | Alpaca web dashboard | No custom dashboard for v1 |

---

## 11. Key dependencies (pyproject.toml)

Managed via `uv`. The root `pyproject.toml` declares dependencies; `tradingagents/` is a uv workspace member with its own `pyproject.toml`.

```toml
dependencies = [
    "alpaca-trade-api>=3.0.0",
    "finnhub-python>=2.4.0",
    "pandas-ta>=0.3.14b",
    "python-dotenv>=1.0.0",
    "pyyaml>=6.0",
    "langchain>=0.3.0",
    "langchain-core>=0.3.81",
    "requests>=2.32.4",
    "tradingagents",           # workspace member
]

[tool.uv]
override-dependencies = ["websockets>=13.0"]   # resolves alpaca-trade-api conflict
```

`requirements.txt` exists as a legacy file for the VPS `setup.sh` (pip-based) but is not the source of truth.

---

## 12. Rollout plan

### Days 1–2: Foundation
- Fork TradingAgents repo (latest main commit) into `tradingagents/` directory as a uv workspace member
- Set up project structure (all files/folders listed in section 6)
- Configure `pyproject.toml` as uv workspace root; run `uv sync` to install all deps
- Implement `config.py` (load config.yaml + .env into a typed config object)
- Implement `data/cache.py` (file-based cache with configurable TTL)
- Implement `data/alpha_vantage.py` and `data/finnhub_client.py` with caching
- Implement `db/models.py` and `db/store.py` (SQLite schema with WAL mode + CRUD)
- Implement `notifications/email_alert.py` (SendGrid)

### Days 3–4: Core pipeline
- Implement `screener/universe.py` (ticker ranking with configurable weights)
- Implement `research/runner.py` (wrapper around TradingAgents propagate, handles existing vs new positions)
- Modify TradingAgents fork: configure OpenRouter with tiered models, inject portfolio context + thesis into trader prompt, add thesis/invalidation to output schema, set debate rounds to 1
- Implement `portfolio/optimizer.py` (equal-weight + cash reserve constraint; no position cap or sector limits)
- Implement `risk/circuit_breaker.py` (stop-loss + drawdown, with cold start handling)
- Implement `execution/alpaca_client.py` (notional buys, full-position sells, fractional shares)

### Days 5–6: Integration + deployment
- Implement `main.py` (full pipeline orchestration exactly per section 7)
- Write `setup.sh` (VPS: creates venv, pip-installs requirements.txt, creates swap file, sets up cron)
- Write `cron/run_daily.sh` (with EDT/EST guard)
- Write basic tests for screener, optimizer, circuit breaker, alpaca client
- Deploy to DigitalOcean droplet
- Run first paper trading execution end-to-end
- Verify email notifications work (daily summary + alert emails)

### Days 7+: Paper trading validation
- Run daily on paper trading for 1–2 weeks
- Monitor agent decision quality via SQLite logs
- Tune config parameters if needed (conviction threshold, stop-loss %, screener weights)
- Switch `paper_trading: false` when confident

---

## 13. Decision log

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Brokerage | Alpaca | API-first, commission-free, paper trading, FINRA registered, fractional shares |
| Research framework | TradingAgents (forked) | Pre-built multi-agent trading architecture on LangGraph, 29.9K stars, arXiv paper |
| Integration approach | Fork into repo | Enables customizing prompts, modifying output schema, configuring LLM provider directly |
| LLM provider | OpenRouter | Single API key for 300+ models, no vendor lock-in, auto-fallback |
| LLM models | Gemini 3.1 Flash Lite (analysts) + Gemini 3 Flash (debate/trader) | Tiered: cheap for data analysis, better for reasoning. No DeepSeek. ~$25-32/mo. |
| Portfolio optimization | Equal weight + rules | Simple for v1, no library dependency. Can add PyPortfolioOpt later. |
| Order type | Market (configurable) | Simpler for v1. Config supports switching to limit orders at last close. |
| Ticker universe | Top 15 US equities by market cap | Fixed list enables aggressive caching. Configurable to expand. |
| Crypto | Excluded from v1 | Insufficient data for sound agent-driven decisions. Add later with dedicated crypto analyst. |
| Thesis invalidation | Full TradingAgents re-run on existing positions | More thorough than a single LLM call. Cost managed via max_daily_research_runs cap. |
| Processing | Sequential (one ticker at a time) | Lower memory footprint for 1GB droplet. Can parallelize later. |
| Fractional shares | Yes | Enables clean equal-weight sizing with ~$1K-per-position amounts. |
| Notifications | SendGrid | Free tier (100 emails/day). Sends over HTTPS — works on DigitalOcean droplets that block SMTP. |
| Monitoring | Alpaca web dashboard | Built-in portfolio/P&L/trade history. No custom dashboard for v1. |
| Deployment | Dedicated DO droplet (1GB + 1GB swap) | Separate from n8n droplet. Swap as safety buffer. |
| Scheduling | Cron with EDT/EST guard | Dual cron entries + bash guard prevents double-firing and handles DST. |
| Database | SQLite with WAL mode | Lightweight, no server, safe for concurrent reads during monitoring. |

---

## 14. Open items (Kevin must complete before Claude Code runs)

- [ ] Create a SendGrid account (sendgrid.com), verify sender identity, and generate an API key
- [ ] Create Alpaca account and generate API keys (paper trading)
- [ ] Create OpenRouter account and add ~$10 in credits
- [ ] Get free API keys: Alpha Vantage, Finnhub
- [ ] Provision DigitalOcean droplet (1GB/1vCPU, Ubuntu 24.04)
- [ ] Fill in `email_to` and `email_from` in config.yaml
- [ ] Test that TradingAgents `propagate()` works with OpenRouter + Gemini Flash (may need to verify the exact OpenRouter model ID accepted by TradingAgents' LangGraph setup)
