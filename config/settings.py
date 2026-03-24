"""
Centralized Configuration for SMC AI Trading Bot
All settings in one place — loaded from environment variables with safe defaults.
"""

import os
import logging
import MetaTrader5 as mt5
from zoneinfo import ZoneInfo

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

# ──────────────────────────────────────────────
#  LOGGING
# ──────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
log = logging.getLogger("smc_bot")

# ──────────────────────────────────────────────
#  MT5 CREDENTIALS
# ──────────────────────────────────────────────
MT5_LOGIN_RAW = os.getenv("MT5_LOGIN", "").strip()
MT5_LOGIN     = int(MT5_LOGIN_RAW) if MT5_LOGIN_RAW.isdigit() else 0
MT5_PASSWORD  = os.getenv("MT5_PASSWORD", "").strip()
MT5_SERVER    = os.getenv("MT5_SERVER", "").strip()
MT5_TERMINAL_PATH = os.getenv("MT5_TERMINAL_PATH", "").strip()
MT5_INIT_RETRIES       = int(os.getenv("MT5_INIT_RETRIES", "5"))
MT5_INIT_RETRY_DELAY   = int(os.getenv("MT5_INIT_RETRY_DELAY_SEC", "3"))

# ──────────────────────────────────────────────
#  ANTHROPIC
# ──────────────────────────────────────────────
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()

# ──────────────────────────────────────────────
#  SYMBOLS & TIMEFRAMES
# ──────────────────────────────────────────────
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD"]

SYMBOL_ALIASES = {
    "XAUUSD": ["XAUUSD", "GOLD", "XAUG"],
}

TIMEFRAME_H4  = mt5.TIMEFRAME_H4
TIMEFRAME_H1  = mt5.TIMEFRAME_H1
TIMEFRAME_M15 = mt5.TIMEFRAME_M15
TIMEFRAME_M5  = mt5.TIMEFRAME_M5
CANDLES       = 100
CANDLES_HTF   = 200   # More candles for H4 trend detection
CANDLES_M5    = 30    # Last 30 M5 candles for entry confirmation

# Per-symbol pip definitions  (1 pip = this many price units)
PIP_VALUE = {
    "EURUSD": 0.0001,
    "GBPUSD": 0.0001,
    "USDJPY": 0.01,
    "XAUUSD": 0.01,
}

# ──────────────────────────────────────────────
#  RISK PARAMETERS
# ──────────────────────────────────────────────
RISK_PCT                      = 1.0
MAX_DAILY_LOSS_USD            = float(os.getenv("MAX_DAILY_LOSS_USD", "100"))
MAX_DAILY_LOSS_PCT            = 3.0
MAX_RISK_PER_TRADE_USD        = float(os.getenv("MAX_RISK_PER_TRADE_USD", "20"))
MAX_TRADE_LOSS_USD            = float(os.getenv("MAX_TRADE_LOSS_USD", "50"))  # Absolute max loss per trade (hard stop)
MAX_TRADES_DAY                = 10
MAX_TRADES_PER_SYMBOL_PER_DAY = int(os.getenv("MAX_TRADES_PER_SYMBOL_PER_DAY", "3"))
SYMBOL_COOLDOWN_MIN           = int(os.getenv("SYMBOL_COOLDOWN_MIN", "10"))
LOSS_COOLDOWN_MIN             = int(os.getenv("LOSS_COOLDOWN_MIN", "30"))
MAX_CONSECUTIVE_LOSSES        = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))

# Spread limits (in points)
MAX_SPREAD = {"EURUSD": 15, "GBPUSD": 20, "USDJPY": 15, "XAUUSD": 50}

MIN_CONFIDENCE = 75
MIN_RR_RATIO   = 2.0
SLEEP_SEC      = 60

# Lot size safety clamps per symbol
MIN_LOT = {"EURUSD": 0.01, "GBPUSD": 0.01, "USDJPY": 0.01, "XAUUSD": 0.01}
MAX_LOT = {"EURUSD": 5.00, "GBPUSD": 5.00, "USDJPY": 5.00, "XAUUSD": 2.00}

# ──────────────────────────────────────────────
#  POSITION MANAGEMENT (trailing stop / breakeven / partial TP)
# ──────────────────────────────────────────────
BREAKEVEN_TRIGGER_RR  = 1.0    # Move SL to breakeven when profit >= 1R
BREAKEVEN_BUFFER_PIPS = 2      # Buffer above entry for BE (covers spread)
TRAIL_TRIGGER_RR      = 1.5    # Start trailing after 1.5R profit
TRAIL_STEP_RATIO      = 0.5    # Trail SL at 50% of risk distance behind price

# Partial take-profit settings
PARTIAL_TP_ENABLED     = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_TRIGGER_RR  = float(os.getenv("PARTIAL_TP_TRIGGER_RR", "1.0"))
PARTIAL_TP_PERCENTAGE  = float(os.getenv("PARTIAL_TP_PERCENTAGE", "0.5"))
PARTIAL_TP_MIN_LOT     = float(os.getenv("PARTIAL_TP_MIN_LOT", "0.02"))

# Volume confirmation for breakeven
VOLUME_CONFIRMATION_ENABLED = os.getenv("VOLUME_CONFIRMATION_ENABLED", "true").lower() == "true"
VOLUME_CONFIRMATION_MIN_RATIO = float(os.getenv("VOLUME_CONFIRMATION_MIN_RATIO", "1.0"))  # Must be at least average volume

# ──────────────────────────────────────────────
#  INTRADAY / SCALPING MODE — disabled, trades run freely
# ──────────────────────────────────────────────
INTRADAY_MODE = False   # No forced session-end closes or stale-trade kills

# Maximum trade duration in minutes (for stale trade detection)
MAX_TRADE_DURATION_MIN = int(os.getenv("MAX_TRADE_DURATION_MIN", "480"))  # 8 hours default

# ──────────────────────────────────────────────
#  SESSION HOURS (UTC)
# ──────────────────────────────────────────────
def _parse_hour_range(env_key: str, default_start: int, default_end: int) -> tuple[int, int]:
    raw = os.getenv(env_key, "").strip()
    if not raw:
        return default_start, default_end
    try:
        start_s, end_s = raw.split("-", 1)
        s, e = int(start_s), int(end_s)
        if 0 <= s <= 23 and 1 <= e <= 24 and s < e:
            return s, e
    except Exception:
        pass
    log.warning(f"Invalid {env_key}: '{raw}'. Using default {default_start}-{default_end}.")
    return default_start, default_end


LONDON_START, LONDON_END = _parse_hour_range("LONDON_SESSION_UTC", 7, 11)
NY_START, NY_END         = _parse_hour_range("NY_SESSION_UTC", 12, 21)

# Per-symbol optimal session hours (UTC)
SYMBOL_SESSIONS = {
    "EURUSD": {"best_hours": list(range(7, 16)),  "avoid_hours": [22, 23, 0, 1, 2, 3]},
    "GBPUSD": {"best_hours": list(range(7, 16)),  "avoid_hours": [22, 23, 0, 1, 2, 3]},
    "USDJPY": {"best_hours": list(range(0, 9)) + list(range(13, 21)), "avoid_hours": [10, 11, 12]},
    "XAUUSD": {"best_hours": list(range(13, 21)), "avoid_hours": [0, 1, 2, 3, 4, 5]},
}

# ──────────────────────────────────────────────
#  ANALYSIS THRESHOLDS
# ──────────────────────────────────────────────
# Minimum FVG size in pips to filter out noise
FVG_MIN_SIZE_PIPS = {"EURUSD": 3, "GBPUSD": 3, "USDJPY": 3, "XAUUSD": 30}

# Point-of-Interest proximity buffer in pips (for pre-filter before Claude)
POI_BUFFER_PIPS = {"EURUSD": 5, "GBPUSD": 5, "USDJPY": 5, "XAUUSD": 50}

# Price entry validation: max distance from current price (fraction)
MAX_ENTRY_DEVIATION = 0.001   # 0.1% max divergence between entry and current price

# News filter
NEWS_FILTER_ENABLED   = os.getenv("NEWS_FILTER_ENABLED", "true").lower() == "true"
NEWS_BLACKOUT_MINUTES = int(os.getenv("NEWS_BLACKOUT_MINUTES", "30"))

# Correlated pairs — prevent double exposure
CORRELATED_PAIRS = {
    frozenset(("EURUSD", "GBPUSD")): 0.80,
}

# ──────────────────────────────────────────────
#  TIMEZONES
# ──────────────────────────────────────────────
GMT = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")

# ──────────────────────────────────────────────
#  BOT MAGIC NUMBER (identifies bot orders in MT5)
# ──────────────────────────────────────────────
BOT_MAGIC = 20250101
BOT_COMMENT = "SMC_AI_BOT"

# Resolved symbol mapping (populated at runtime)
RESOLVED_SYMBOL_TO_BASE: dict[str, str] = {}


def validate_required_env() -> bool:
    """Verify all required secrets are present."""
    required = {
        "MT5_LOGIN":       str(MT5_LOGIN),
        "MT5_PASSWORD":    MT5_PASSWORD,
        "MT5_SERVER":      MT5_SERVER,
        "ANTHROPIC_API_KEY": ANTHROPIC_API_KEY,
    }
    missing = [k for k, v in required.items() if not v or v == "0"]
    if missing:
        log.error(f"Missing required env vars: {', '.join(missing)}")
        return False
    return True
