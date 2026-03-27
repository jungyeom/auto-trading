---
name: Auto-trader project overview
description: Kevin's automated investment system using LLM agents — goals, constraints, tech stack
type: project
---

Automated investment system using LLM agents to research, debate, and execute trades on US equities.

**Why:** Beat S&P 500 over 1 year, fully automated, no daily intervention.

**How to apply:** Every implementation decision should match the spec at `plan/AUTO_TRADER_SPEC (1).md`. It is the single source of truth — ask before deviating.

Key facts:
- Starting capital ~$10,000, 15 US equities universe, max 8–10 positions, equal-weighted
- Runs on DigitalOcean 1GB/1vCPU droplet (+ 1GB swap) at 4:30pm ET Mon–Fri
- LLM: OpenRouter → Gemini 3.1 Flash Lite (analysts) + Gemini 3 Flash (trader/risk)
- Brokerage: Alpaca (paper first, then live). Data: Alpha Vantage + Finnhub (free tiers)
- Database: SQLite WAL mode. Notifications: Gmail SMTP
- Budget: $15–30/month LLM

Rollout layers (build and review one at a time):
1. Foundation (done): config, cache, data clients (AV + Finnhub), SQLite store, email alerts
2. Core pipeline: screener, research runner (TradingAgents fork), optimizer, risk, Alpaca client
3. Integration: main.py orchestration, setup.sh, cron script, tests, deployment
