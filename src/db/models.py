"""
SQLite table definitions.

Tables:
  decisions          — every agent decision (one row per ticker per run)
  trades             — every order placed or attempted
  portfolio_snapshots — daily portfolio state + benchmark comparison
"""

CREATE_DECISIONS = """
CREATE TABLE IF NOT EXISTS decisions (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker                TEXT    NOT NULL,
    action                TEXT    NOT NULL,   -- "buy", "sell", "hold"
    conviction            REAL,
    thesis                TEXT,
    invalidation_conditions TEXT,             -- JSON array stored as text
    full_reasoning        TEXT,
    is_existing_position  INTEGER NOT NULL DEFAULT 0,  -- 0=new, 1=existing
    timestamp             TEXT    NOT NULL
)
"""

CREATE_TRADES = """
CREATE TABLE IF NOT EXISTS trades (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker        TEXT    NOT NULL,
    action        TEXT    NOT NULL,   -- "buy", "sell"
    quantity      REAL,
    dollar_amount REAL,
    order_type    TEXT,               -- "market", "limit"
    fill_price    REAL,
    order_id      TEXT,
    status        TEXT,               -- "filled", "pending", "rejected", "error"
    error_message TEXT,
    timestamp     TEXT    NOT NULL
)
"""

CREATE_PORTFOLIO_SNAPSHOTS = """
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    total_equity      REAL    NOT NULL,
    cash              REAL    NOT NULL,
    peak_equity       REAL    NOT NULL,
    drawdown_pct      REAL    NOT NULL,
    positions_json    TEXT,             -- JSON: [{ticker, qty, market_value, unrealized_pl_pct}, ...]
    spy_comparison_pct REAL,            -- portfolio return vs SPY return since inception (pct points)
    timestamp         TEXT    NOT NULL
)
"""

ALL_TABLES = [CREATE_DECISIONS, CREATE_TRADES, CREATE_PORTFOLIO_SNAPSHOTS]
