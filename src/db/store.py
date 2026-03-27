"""
SQLite read/write functions.

All public methods accept plain Python types (dicts, dataclasses) and handle
JSON serialization internally. No SQLite details leak to callers.
"""

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.db.models import ALL_TABLES

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, db_path: str = "logs/trades.db"):
        self._path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def enable_wal_mode(self) -> None:
        """Enable WAL journal mode for safe concurrent reads."""
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.commit()

    def _init_schema(self) -> None:
        """Create all tables if they don't exist."""
        cur = self._conn.cursor()
        for ddl in ALL_TABLES:
            cur.execute(ddl)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Decisions
    # ------------------------------------------------------------------

    def log_decision(self, decision: Dict[str, Any]) -> None:
        """
        Persist an agent decision.
        Expected keys: ticker, action, conviction, thesis, invalidation_conditions,
                       full_reasoning, is_existing_position.
        """
        invalidation_json = json.dumps(decision.get("invalidation_conditions") or [])
        self._conn.execute(
            """
            INSERT INTO decisions
                (ticker, action, conviction, thesis, invalidation_conditions,
                 full_reasoning, is_existing_position, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                decision["ticker"],
                decision["action"],
                decision.get("conviction"),
                decision.get("thesis"),
                invalidation_json,
                decision.get("reasoning") or decision.get("full_reasoning"),
                1 if decision.get("is_existing_position") else 0,
                _now_iso(),
            ),
        )
        self._conn.commit()

    def get_latest_thesis(self, ticker: str) -> Optional[Dict[str, Any]]:
        """
        Return the most recent buy decision for a ticker, including its thesis
        and invalidation conditions. Returns None if no prior buy decision exists.
        """
        row = self._conn.execute(
            """
            SELECT thesis, invalidation_conditions, conviction, timestamp
            FROM decisions
            WHERE ticker = ? AND action = 'buy'
            ORDER BY timestamp DESC
            LIMIT 1
            """,
            (ticker,),
        ).fetchone()

        if row is None:
            return None

        return {
            "thesis": row["thesis"],
            "invalidation_conditions": json.loads(row["invalidation_conditions"] or "[]"),
            "conviction": row["conviction"],
            "timestamp": row["timestamp"],
        }

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    def log_trade(self, trade: Dict[str, Any]) -> None:
        """
        Persist an executed (or failed) order.
        Expected keys: ticker, action, quantity, dollar_amount, order_type,
                       fill_price, order_id, status, error_message (optional).
        """
        self._conn.execute(
            """
            INSERT INTO trades
                (ticker, action, quantity, dollar_amount, order_type,
                 fill_price, order_id, status, error_message, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                trade["ticker"],
                trade["action"],
                trade.get("quantity"),
                trade.get("dollar_amount"),
                trade.get("order_type"),
                trade.get("fill_price"),
                trade.get("order_id"),
                trade.get("status"),
                trade.get("error_message"),
                _now_iso(),
            ),
        )
        self._conn.commit()

    def get_todays_trades(self) -> List[Dict]:
        """Return all trade records logged today (UTC date)."""
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self._conn.execute(
            "SELECT * FROM trades WHERE timestamp LIKE ? ORDER BY timestamp",
            (f"{today}%",),
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Portfolio snapshots + peak equity
    # ------------------------------------------------------------------

    def get_peak_equity(self) -> Optional[float]:
        """Return the most recently stored peak equity value, or None on first run."""
        row = self._conn.execute(
            "SELECT peak_equity FROM portfolio_snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        return row["peak_equity"] if row else None

    def save_snapshot(
        self,
        portfolio: Any,
        peak_equity: float,
        drawdown_pct: float,
        spy_comparison_pct: Optional[float] = None,
    ) -> None:
        """
        Persist a daily portfolio snapshot.
        portfolio is expected to have: total_equity, cash, positions attributes.
        """
        positions = []
        for pos in getattr(portfolio, "positions", []):
            positions.append({
                "ticker": getattr(pos, "ticker", None),
                "qty": getattr(pos, "qty", None),
                "market_value": getattr(pos, "market_value", None),
                "unrealized_pl_pct": getattr(pos, "unrealized_pl_pct", None),
            })

        self._conn.execute(
            """
            INSERT INTO portfolio_snapshots
                (total_equity, cash, peak_equity, drawdown_pct,
                 positions_json, spy_comparison_pct, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                portfolio.total_equity,
                portfolio.cash,
                peak_equity,
                drawdown_pct,
                json.dumps(positions),
                spy_comparison_pct,
                _now_iso(),
            ),
        )
        self._conn.commit()

    def get_inception_snapshot(self) -> Optional[Dict]:
        """Return the very first portfolio snapshot (used for SPY benchmark calculation)."""
        row = self._conn.execute(
            "SELECT * FROM portfolio_snapshots ORDER BY timestamp ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def get_todays_errors(self) -> List[str]:
        """Return error messages from trades that failed today."""
        today = datetime.now(timezone.utc).date().isoformat()
        rows = self._conn.execute(
            """
            SELECT error_message FROM trades
            WHERE timestamp LIKE ? AND status = 'error' AND error_message IS NOT NULL
            """,
            (f"{today}%",),
        ).fetchall()
        return [r["error_message"] for r in rows]

    def close(self) -> None:
        self._conn.close()
