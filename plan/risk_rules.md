# Risk Rules Reference

Last updated: 2026-03-27

---

## Active Rules

### 1. Portfolio Drawdown Breaker
**File:** `src/risk/circuit_breaker.py` — `check_portfolio_drawdown()`
**Config:** `portfolio_drawdown_limit_pct: 0.15`

If total equity drops **15% from its all-time peak**, the entire pipeline halts immediately.
No trades execute. An alert email is sent. Requires manual review to resume.

Formula: `drawdown = (peak_equity - total_equity) / peak_equity`

---

### 2. Per-Position Stop-Loss
**File:** `src/risk/circuit_breaker.py` — `scan_stop_losses()`
**Config:** `stop_loss_pct: 0.08`

If any open position drops **8% from its entry price**, it is sold immediately at market before any research or new orders run that day.

Formula: `loss_pct = (current_price - entry_price) / entry_price`

---

### 3. Conviction Threshold
**File:** `src/portfolio/optimizer.py`
**Config:** `conviction_threshold: 0.6`

The AI trader scores each decision 0.0–1.0. Only decisions with conviction **≥ 0.60** proceed to order generation. Lower-confidence calls are dropped.

If the Portfolio Manager's signal contradicts the Trader's recommendation, conviction is forced to 0.0 to guarantee rejection.

---

### 4. Cash Reserve
**File:** `src/portfolio/optimizer.py`
**Config:** `cash_reserve_pct: 0.15`

**15% of total equity** is always held as cash and excluded from the investable pool.

Formula: `investable = total_equity * (1 - 0.15)`

---

### 5. Minimum Available Cash Check
**File:** `src/portfolio/optimizer.py`

Before placing each buy, available cash (portfolio cash minus already-committed orders) must be at least **50% of the target position size**. Otherwise the buy is skipped.

---

### 6. Recently-Bought Research Skip
**File:** `src/main.py`, `src/db/store.py`

Existing positions that were bought **within the last 7 days** are excluded from the daily research list. They do not need immediate thesis review — this frees up research slots for new ticker candidates.

Positions bought more than 7 days ago are included in the research list as usual for ongoing thesis validation.

---

### 7. Daily Research Cap
**File:** `src/main.py`
**Config:** `max_daily_research_runs: 15`

Hard limit of **15 TradingAgents runs per day** total. Existing positions (eligible for research) are always prioritized first; new opportunity candidates fill remaining slots.

---

### 8. New Opportunities Per Day
**File:** `src/main.py`
**Config:** `tickers_to_analyze_per_day: 8`

At most **8 new ticker candidates** are screened and researched per day (after existing positions fill their slots). Remaining candidates are deferred to the next run.

---

### 9. Screener Ranking Weights
**File:** `src/screener/universe.py`
**Config:** `screener_momentum_weight: 0.6`, `screener_volume_weight: 0.4`

New candidates are ranked before research:
- **60%** — 30-day price momentum (normalized)
- **40%** — relative volume: 10-day avg / 50-day avg (normalized)

Higher score = analyzed first.

---

### 10. AI Debate Rounds
**File:** `tradingagents/tradingagents/graph/conditional_logic.py`
**Config:** `max_debate_rounds: 1`

- Bull/Bear researcher debate: **1 round**
- Conservative/Aggressive/Neutral risk analyst debate: **1 round** (hardcoded)

Controls LLM inference cost and latency per ticker.

---

## Removed Rules

| Rule | Was | Removed | Reason |
|---|---|---|---|
| Max position size | 15% of equity per stock | 2026-03-27 | AI given full flexibility; only cash reserve limits exposure |
| Sector concentration | 30% per GICS sector | 2026-03-27 | AI given full flexibility across sectors |

---

## Summary Table

| Rule | Threshold | Trigger | Action |
|---|---|---|---|
| Portfolio drawdown | 15% from peak | Equity drop | Halt all trading |
| Stop-loss | -8% from entry | Position loss | Liquidate position |
| Conviction minimum | ≥ 0.60 | Agent score | Reject trade |
| Cash reserve | 15% of equity | Always | Reduce investable pool |
| Min cash check | 50% of target size | Per buy | Skip buy |
| Recently bought skip | < 7 days since buy | Existing position | Skip research |
| Daily research cap | 15 runs/day | Total runs | Skip excess tickers |
| New opportunities | 8 tickers/day | New candidates | Defer to next day |
| Momentum weight | 60% | Screener | Rank candidates |
| Volume weight | 40% | Screener | Rank candidates |
| Debate rounds | 1 round each | Agent graph | End debate |
