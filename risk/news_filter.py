"""
News Event Filter — Avoids trading during high-impact economic events.
Uses the free ForexFactory calendar API (faireconomy.media).
Caches results to avoid excessive API calls.
"""

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import List, Dict

log = logging.getLogger("smc_bot")

# In-memory cache
_news_cache: list = []
_news_cache_ts: float = 0.0
_CACHE_TTL_SEC = 3600  # Refresh every hour


def _currency_affects_symbol(currency: str, symbol: str) -> bool:
    """Check if a news currency affects a trading symbol."""
    currency = currency.upper()
    symbol = symbol.upper()

    mapping = {
        "USD": ["EURUSD", "GBPUSD", "XAUUSD", "GOLD"],
        "EUR": ["EURUSD"],
        "GBP": ["GBPUSD"],
    }

    affected_symbols = mapping.get(currency, [])
    return any(s in symbol for s in affected_symbols)


def fetch_news_calendar() -> List[Dict]:
    """
    Fetch this week's economic calendar from ForexFactory (via faireconomy.media).
    Returns a list of event dicts with: title, country, date, impact.
    Falls back gracefully if the API is unavailable.
    """
    global _news_cache, _news_cache_ts

    # Return cached data if fresh enough
    if _news_cache and (time.time() - _news_cache_ts) < _CACHE_TTL_SEC:
        return _news_cache

    try:
        import requests
        response = requests.get(
            "https://nfs.faireconomy.media/ff_calendar_thisweek.json",
            timeout=8,
        )
        response.raise_for_status()
        events = response.json()

        parsed = []
        for event in events:
            parsed.append({
                "title":   event.get("title", ""),
                "country": event.get("country", ""),
                "date":    event.get("date", ""),
                "impact":  event.get("impact", "").lower(),
            })

        _news_cache = parsed
        _news_cache_ts = time.time()
        log.info(f"[NewsFilter] Fetched {len(parsed)} events from calendar.")
        return parsed

    except ImportError:
        log.warning("[NewsFilter] 'requests' not installed. News filter disabled.")
        return []
    except Exception as e:
        log.warning(f"[NewsFilter] Failed to fetch calendar: {e}")
        return _news_cache  # Return stale cache if available


def has_upcoming_high_impact_news(
    symbol: str,
    blackout_minutes: int = 30,
) -> tuple[bool, str]:
    """
    Check if there's a high-impact news event within the blackout window
    for the given symbol's currencies.

    Returns:
        (should_avoid: bool, event_description: str)
    """
    events = fetch_news_calendar()
    if not events:
        return False, ""

    now = datetime.now(timezone.utc)

    for event in events:
        if event["impact"] not in ("high", "medium"):
            continue

        # Parse event time
        try:
            event_time = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
            if event_time.tzinfo is None:
                event_time = event_time.replace(tzinfo=timezone.utc)
        except Exception:
            continue

        # Check if event is within the blackout window (before or after)
        delta = (event_time - now).total_seconds()
        within_pre_window = 0 <= delta <= blackout_minutes * 60
        within_post_window = -10 * 60 <= delta < 0  # 10 min after event too

        if not (within_pre_window or within_post_window):
            continue

        # Check if this event's currency affects our symbol
        if _currency_affects_symbol(event["country"], symbol):
            desc = f"{event['impact'].upper()} impact: {event['title']} ({event['country']})"
            log.info(f"[NewsFilter] [{symbol}] Avoiding trade — {desc}")
            return True, desc

    return False, ""
