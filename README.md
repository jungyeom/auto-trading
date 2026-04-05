# Auto-trader

An automated investment system that uses a team of LLM-powered agents to research, debate, and execute trades on US equities. Runs daily at 4:30pm ET after market close.

## Architecture

```
Cron (4:30pm ET, Mon–Fri)
  │
  ▼
1. Risk check           — drawdown breaker + per-position stop-loss
2. Universe screener    — rank 15 tickers, pick top 8 new opportunities
3. TradingAgents loop   — 4 analysts → bull/bear debate → trader → risk manager
4. Portfolio optimizer  — equal-weight + hard constraints
5. Execution            — notional buys, fVull-position sells via Alpaca
6. Logging + email      — SQLite + daily Brevo email summary
```

Full spec: [plan/AUTO_TRADER_SPEC.md](plan/AUTO_TRADER_SPEC%20(1).md)

## Setup

### Local development (uv)

```bash
# Install uv if you don't have it
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies
uv sync
```

### VPS deployment (DigitalOcean droplet)

```bash
git clone <repo> auto-trader && cd auto-trader
git submodule update --init --recursive
bash setup.sh
```

`setup.sh` will:
1. Install Python 3.12 and uv
2. Install all dependencies via `uv sync`
3. Create a 1GB swap file (memory safety buffer for 1GB droplet)
4. Print the two cron entries to add

## Configuration

### 1. API keys — `.env`

Copy `.env.example` to `.env` and fill in all values:

```env
OPENROUTER_API_KEY=...     # openrouter.ai — for LLM calls
ALPACA_API_KEY=...         # app.alpaca.markets
ALPACA_SECRET_KEY=...
ALPACA_BASE_URL=https://paper-api.alpaca.markets  # change to live when ready
ALPHA_VANTAGE_API_KEY=...  # alphavantage.co (free tier)
FINNHUB_API_KEY=...        # finnhub.io (free tier)
BREVO_API_KEY=...          # brevo.com — free tier (300 emails/day), verify sender first
```

### 2. Email + config — `config.yaml`

Edit `config.yaml` and fill in:

```yaml
email_to: "you@example.com"
email_from: "you@example.com"       # must be a verified sender in Brevo
```

All other parameters (thresholds, model names, ticker universe) are already set to spec defaults.

## Running

```bash
# Test run (paper trading mode) — local
uv run python -m src.main

# On VPS (after setup.sh)
uv run python -m src.main

# Check logs
tail -f logs/app.log
```

## Cron (auto-added by setup.sh instructions)

```cron
# EDT (Mar–Nov): 4:30pm ET = 20:30 UTC
30 20 * * 1-5 /home/kevin/auto-trader/cron/run_daily.sh >> /home/kevin/auto-trader/logs/cron.log 2>&1
# EST (Nov–Mar): 4:30pm ET = 21:30 UTC
30 21 * * 1-5 /home/kevin/auto-trader/cron/run_daily.sh >> /home/kevin/auto-trader/logs/cron.log 2>&1
```

The shell script has an ET hour guard — only one entry fires per day.

## Running tests

```bash
uv run pytest tests/ -v
```

## Switching to live trading

1. Change `paper_trading: true` → `false` in `config.yaml`
2. Change `ALPACA_BASE_URL` in `.env` → `https://api.alpaca.markets`
3. Verify at least one full paper-trading week looks correct
4. Run `python -m src.main` and watch `logs/app.log` closely on first live execution

## Project structure

```
auto-trader/
├── pyproject.toml         # uv workspace + dependencies (source of truth)
├── uv.lock                # Locked dependency versions
├── config.yaml            # All tunable parameters
├── .env                   # API credentials (gitignored)
├── setup.sh               # VPS one-command setup (uses pip + venv)
├── src/
│   ├── main.py            # Daily pipeline entry point
│   ├── config.py          # Typed config loader
│   ├── screener/          # Ticker ranking
│   ├── research/          # TradingAgents wrapper
│   ├── portfolio/         # Equal-weight optimizer
│   ├── execution/         # Alpaca client
│   ├── risk/              # Stop-loss + drawdown checks
│   ├── notifications/     # Brevo email alerts
│   ├── data/              # Alpha Vantage + Finnhub clients + cache
│   └── db/                # SQLite store
├── tradingagents/         # Forked TradingAgents (LangGraph, uv workspace member)
├── cron/run_daily.sh      # Cron wrapper with EDT/EST guard
├── tests/                 # pytest tests
├── eval_results/          # TradingAgents per-ticker evaluation output (gitignored)
└── logs/                  # app.log, cron.log, trades.db (gitignored)
```

## LLM cost estimate

- Analysts (Gemini 3.1 Flash Lite): ~$0.08/day
- Trader + risk manager (Gemini 3 Flash): ~$0.30/day
- **Total: ~$8–12/month**
