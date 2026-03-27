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
5. Execution            — notional buys, full-position sells via Alpaca
6. Logging + email      — SQLite + daily Gmail summary
```

Full spec: [plan/AUTO_TRADER_SPEC.md](plan/AUTO_TRADER_SPEC%20(1).md)

## Setup (DigitalOcean droplet)

```bash
git clone <repo> auto-trader && cd auto-trader
bash setup.sh
```

`setup.sh` will:
1. Install Python 3.12 and create a virtual environment
2. Install all dependencies (`requirements.txt`)
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
EMAIL_APP_PASSWORD=...     # Gmail app password (not your regular password)
```

### 2. Email + config — `config.yaml`

Edit `config.yaml` and fill in:

```yaml
email_to: "you@example.com"
email_from: "yourbot@gmail.com"
```

All other parameters (thresholds, model names, ticker universe) are already set to spec defaults.

## Running

```bash
# Test run (paper trading mode)
source venv/bin/activate
python -m src.main

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
source venv/bin/activate
pip install pytest
pytest tests/ -v
```

## Switching to live trading

1. Change `paper_trading: true` → `false` in `config.yaml`
2. Change `ALPACA_BASE_URL` in `.env` → `https://api.alpaca.markets`
3. Verify at least one full paper-trading week looks correct
4. Run `python -m src.main` and watch `logs/app.log` closely on first live execution

## Project structure

```
auto-trader/
├── config.yaml            # All tunable parameters
├── .env                   # API credentials (gitignored)
├── requirements.txt
├── setup.sh               # One-command setup
├── src/
│   ├── main.py            # Daily pipeline entry point
│   ├── config.py          # Typed config loader
│   ├── screener/          # Ticker ranking
│   ├── research/          # TradingAgents wrapper
│   ├── portfolio/         # Equal-weight optimizer
│   ├── execution/         # Alpaca client
│   ├── risk/              # Stop-loss + drawdown checks
│   ├── notifications/     # Gmail alerts
│   ├── data/              # Alpha Vantage + Finnhub clients + cache
│   └── db/                # SQLite store
├── tradingagents/         # Forked TradingAgents (LangGraph)
├── cron/run_daily.sh      # Cron wrapper with EDT/EST guard
├── tests/                 # pytest tests
└── logs/                  # app.log, cron.log, trades.db (gitignored)
```

## LLM cost estimate

- Analysts (Gemini 3.1 Flash Lite): ~$0.08/day
- Trader + risk manager (Gemini 3 Flash): ~$0.30/day
- **Total: ~$8–12/month** well within the $15–30 budget

## Open items (complete before first run)

- [ ] Gmail 2FA + app password
- [ ] Alpaca account + API keys (start with paper trading)
- [ ] OpenRouter account + ~$10 credits
- [ ] Alpha Vantage free API key
- [ ] Finnhub free API key
- [ ] DigitalOcean droplet provisioned (1GB/1vCPU Ubuntu 24.04)
- [ ] Fill in `email_to` / `email_from` in `config.yaml`
