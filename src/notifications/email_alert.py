"""
Email notifications via Gmail SMTP.

Two entry points:
  send_alert(subject, body, config)     — immediate alert (stop-loss, drawdown, order failure)
  send_daily_summary(data, config)      — end-of-day pipeline summary
"""

import logging
import smtplib
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def send_alert(subject: str, body: str, config: Any) -> None:
    """
    Send an immediate alert email. Used for stop-loss triggers, drawdown breaker,
    and order failures. Fails silently (logs error) so the pipeline keeps running.
    """
    if not config.email_enabled:
        logger.debug("Email disabled — skipping alert: %s", subject)
        return

    _send(
        to=config.email_to,
        from_addr=config.email_from,
        subject=f"[Auto-trader ALERT] {subject}",
        body=body,
        config=config,
    )


def send_daily_summary(
    portfolio: Any,
    stop_loss_sells: List[Dict],
    decisions: List[Dict],
    results: List[Dict],
    spy_comparison: Optional[float],
    errors: List[str],
    config: Any,
) -> None:
    """Send the end-of-day pipeline summary email."""
    if not config.email_enabled:
        logger.debug("Email disabled — skipping daily summary")
        return

    today = date.today().isoformat()
    subject = f"Auto-trader daily report — {today}"
    body = _build_daily_body(
        portfolio, stop_loss_sells, decisions, results, spy_comparison, errors
    )

    _send(
        to=config.email_to,
        from_addr=config.email_from,
        subject=subject,
        body=body,
        config=config,
    )


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _build_daily_body(
    portfolio: Any,
    stop_loss_sells: List[Dict],
    decisions: List[Dict],
    results: List[Dict],
    spy_comparison: Optional[float],
    errors: List[str],
) -> str:
    lines = []

    # Portfolio summary
    lines.append("=== PORTFOLIO SUMMARY ===")
    lines.append(f"Total equity : ${portfolio.total_equity:,.2f}")
    lines.append(f"Cash         : ${portfolio.cash:,.2f}")
    if spy_comparison is not None:
        sign = "+" if spy_comparison >= 0 else ""
        lines.append(f"vs SPY (inception): {sign}{spy_comparison:.2f}pp")
    lines.append("")

    # Current positions
    if portfolio.positions:
        lines.append("=== CURRENT POSITIONS ===")
        for pos in portfolio.positions:
            pl = getattr(pos, "unrealized_pl_pct", None)
            pl_str = f"  {pl:+.1%}" if pl is not None else ""
            lines.append(f"  {pos.ticker:<6}  ${getattr(pos, 'market_value', 0):>10,.2f}{pl_str}")
        lines.append("")

    # Stop-loss sells
    if stop_loss_sells:
        lines.append("=== STOP-LOSS SELLS ===")
        for t in stop_loss_sells:
            lines.append(f"  SOLD {t.get('ticker')}  (order: {t.get('order_id', 'n/a')})")
        lines.append("")

    # Today's decisions
    buys  = [d for d in decisions if d.get("action") == "buy"]
    sells = [d for d in decisions if d.get("action") == "sell"]
    holds = [d for d in decisions if d.get("action") == "hold"]

    if buys:
        lines.append("=== BUY DECISIONS ===")
        for d in buys:
            lines.append(f"  {d['ticker']:<6} conviction={d.get('conviction', 0):.2f}")
            if d.get("thesis"):
                lines.append(f"    Thesis: {d['thesis'][:120]}")
        lines.append("")

    if sells:
        lines.append("=== SELL DECISIONS (thesis invalidation) ===")
        for d in sells:
            lines.append(f"  {d['ticker']:<6} conviction={d.get('conviction', 0):.2f}")
        lines.append("")

    if holds:
        lines.append("=== HOLD DECISIONS ===")
        lines.append("  " + ", ".join(d["ticker"] for d in holds))
        lines.append("")

    # Orders placed
    if results:
        lines.append("=== ORDERS PLACED TODAY ===")
        for r in results:
            ticker = r.get("ticker", "?")
            action = r.get("action", "?").upper()
            amount = r.get("dollar_amount") or r.get("quantity", "")
            status = r.get("status", "?")
            lines.append(f"  {action} {ticker}  ${amount}  status={status}")
        lines.append("")

    # Errors
    if errors:
        lines.append("=== ERRORS ===")
        for e in errors:
            lines.append(f"  {e}")
        lines.append("")

    return "\n".join(lines)


def _send(to: str, from_addr: str, subject: str, body: str, config: Any) -> None:
    if not to or not from_addr:
        logger.warning("Email skipped — email_to or email_from not configured in config.yaml")
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to
    msg.attach(MIMEText(body, "plain"))

    try:
        with smtplib.SMTP(config.smtp_server, config.smtp_port) as server:
            server.ehlo()
            server.starttls()
            server.login(from_addr, config.email_app_password)
            server.sendmail(from_addr, [to], msg.as_string())
        logger.info("Email sent: %s", subject)
    except Exception as e:
        logger.error("Failed to send email '%s': %s", subject, e)
