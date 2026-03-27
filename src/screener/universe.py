"""
Universe screener — ranks tickers by momentum + relative volume.

Scoring formula (configurable weights):
  score = momentum_weight * 30d_momentum_pct + volume_weight * relative_volume_ratio

  30d momentum      = (close_today - close_30d_ago) / close_30d_ago
  relative_volume   = avg_10d_volume / avg_50d_volume  (ratio vs 50-day baseline)

Both components are min-max normalised to [0, 1] before weighting so that
one dimension can't dominate just because of scale differences.

Returns tickers sorted by score descending (highest score = most attractive).
"""

import logging
from typing import Dict, List, Optional

from src.config import Config
from src.data.alpha_vantage import AlphaVantageClient

logger = logging.getLogger(__name__)


def rank_tickers(tickers: List[str], config: Config, av_client: AlphaVantageClient) -> List[str]:
    """
    Rank a list of tickers by composite momentum + volume score.

    Args:
        tickers: Candidate tickers to rank (already excludes existing positions).
        config: Loaded Config with screener weights.
        av_client: AlphaVantageClient for fetching daily price history.

    Returns:
        Tickers sorted by score descending. Tickers with missing data are appended
        at the end in their original order (so the pipeline doesn't silently drop them).
    """
    scores: Dict[str, float] = {}
    no_data: List[str] = []

    for ticker in tickers:
        series = av_client.get_time_series_daily(ticker, outputsize="compact")
        if not series:
            logger.warning("No price data for %s — skipping screener scoring", ticker)
            no_data.append(ticker)
            continue

        momentum = _compute_momentum(series)
        rel_volume = _compute_relative_volume(series)

        if momentum is None and rel_volume is None:
            no_data.append(ticker)
            continue

        scores[ticker] = (momentum or 0.0, rel_volume or 0.0)

    if not scores:
        logger.warning("Screener: no data available for any ticker — returning original order")
        return tickers

    # Normalise each dimension separately then compute weighted score
    ranked = _normalise_and_rank(scores, config)
    return ranked + no_data


# ------------------------------------------------------------------
# Private helpers
# ------------------------------------------------------------------

def _compute_momentum(series: Dict) -> Optional[float]:
    """
    30-day price momentum: (price_today - price_30d_ago) / price_30d_ago.
    Returns None if insufficient history.
    """
    dates = sorted(series.keys(), reverse=True)  # most recent first
    if len(dates) < 31:
        return None

    try:
        close_today = float(series[dates[0]]["4. close"])
        close_30d = float(series[dates[30]]["4. close"])
        return (close_today - close_30d) / close_30d
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _compute_relative_volume(series: Dict) -> Optional[float]:
    """
    Relative volume: avg_10d_volume / avg_50d_volume.
    Returns None if insufficient history.
    """
    dates = sorted(series.keys(), reverse=True)
    if len(dates) < 51:
        return None

    try:
        volumes_10 = [float(series[dates[i]]["5. volume"]) for i in range(10)]
        volumes_50 = [float(series[dates[i]]["5. volume"]) for i in range(50)]
        avg_10 = sum(volumes_10) / 10
        avg_50 = sum(volumes_50) / 50
        if avg_50 == 0:
            return None
        return avg_10 / avg_50
    except (KeyError, ValueError, ZeroDivisionError):
        return None


def _normalise_and_rank(
    raw_scores: Dict[str, tuple],  # {ticker: (momentum, rel_volume)}
    config: Config,
) -> List[str]:
    """Min-max normalise each dimension, then compute weighted score and sort."""
    tickers = list(raw_scores.keys())
    momentums = [raw_scores[t][0] for t in tickers]
    volumes = [raw_scores[t][1] for t in tickers]

    def minmax(values):
        mn, mx = min(values), max(values)
        if mx == mn:
            return [0.5] * len(values)
        return [(v - mn) / (mx - mn) for v in values]

    norm_m = minmax(momentums)
    norm_v = minmax(volumes)

    combined = {
        tickers[i]: (
            config.screener_momentum_weight * norm_m[i]
            + config.screener_volume_weight * norm_v[i]
        )
        for i in range(len(tickers))
    }

    return sorted(tickers, key=lambda t: combined[t], reverse=True)
