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
SYMBOLS = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "USDCAD", "AUDUSD", "NZDUSD", "USDCHF", "EURGBP", "EURJPY", "GBPJPY", "AUDJPY"]

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
    "EURUSD": 0.0001, "GBPUSD": 0.0001, "USDJPY": 0.01, "XAUUSD": 0.01,
    "USDCAD": 0.0001, "AUDUSD": 0.0001, "NZDUSD": 0.0001, "USDCHF": 0.0001,
    "EURGBP": 0.0001, "EURJPY": 0.01, "GBPJPY": 0.01, "AUDJPY": 0.01
}

# ──────────────────────────────────────────────
#  RISK PARAMETERS
# ──────────────────────────────────────────────
RISK_PCT                      = 2.0  # Increased to aim for $20 risk on $1000 accounts
MAX_DAILY_LOSS_USD            = float(os.getenv("MAX_DAILY_LOSS_USD", "100"))
MAX_DAILY_LOSS_PCT            = 3.0
MAX_RISK_PER_TRADE_USD        = 20.0  # Physical cap at exactly $20
MAX_TRADE_LOSS_USD            = 20.0  # Hard stop at $20 as well
MAX_TRADES_DAY                = 10
MAX_TRADES_PER_SYMBOL_PER_DAY = int(os.getenv("MAX_TRADES_PER_SYMBOL_PER_DAY", "3"))
SYMBOL_COOLDOWN_MIN           = int(os.getenv("SYMBOL_COOLDOWN_MIN", "10"))
LOSS_COOLDOWN_MIN             = int(os.getenv("LOSS_COOLDOWN_MIN", "30"))
MAX_CONSECUTIVE_LOSSES        = int(os.getenv("MAX_CONSECUTIVE_LOSSES", "3"))

# Spread limits (in points)
MAX_SPREAD = {
    "EURUSD": 15, "GBPUSD": 20, "USDJPY": 15, "XAUUSD": 50,
    "USDCAD": 25, "AUDUSD": 20, "NZDUSD": 25, "USDCHF": 25,
    "EURGBP": 25, "EURJPY": 30, "GBPJPY": 35, "AUDJPY": 30
}

MIN_CONFIDENCE = 75
MIN_RR_RATIO   = 2.0
SLEEP_SEC      = 60

# Pure algorithmic mode: do not require Anthropic API key at startup
PURE_ALGO_MODE = os.getenv("PURE_ALGO_MODE", "true").lower() == "true"

# Algorithmic entry tuning (more POI reach + sweep/BOS paths → more trades, still gated by H4/RR)
ALGO_VOLUME_MIN_RATIO = float(os.getenv("ALGO_VOLUME_MIN_RATIO", "0.5"))
ALGO_POI_ATR_MULT = float(os.getenv("ALGO_POI_ATR_MULT", "0.35"))
ALGO_POI_EXTRA_PIPS = int(os.getenv("ALGO_POI_EXTRA_PIPS", "3"))

# Structure (BOS/CHOCH/bias) = H1; POI (OB/FVG/breakers) = M15 — see main.build_smc_context
# Extra confirmations (no AI): M5 pattern + multi-timeframe + POI confluence
ALGO_REQUIRE_M5_CONFIRM = os.getenv("ALGO_REQUIRE_M5_CONFIRM", "true").lower() == "true"
ALGO_REQUIRE_MTF_CONFIRM = os.getenv("ALGO_REQUIRE_MTF_CONFIRM", "true").lower() == "true"
ALGO_MTF_MIN_CONFIDENCE = float(os.getenv("ALGO_MTF_MIN_CONFIDENCE", "70"))
ALGO_MIN_POI_CONFLUENCE = int(os.getenv("ALGO_MIN_POI_CONFLUENCE", "2"))
ALGO_LIQUIDITY_SHORTCUT = os.getenv("ALGO_LIQUIDITY_SHORTCUT", "false").lower() == "true"
ALGO_REQUIRE_M15_EMA_ALIGN = os.getenv("ALGO_REQUIRE_M15_EMA_ALIGN", "true").lower() == "true"

# Lot size safety clamps per symbol
MIN_LOT = {s: 0.01 for s in SYMBOLS}
MAX_LOT = {s: 5.00 for s in SYMBOLS}
MAX_LOT["XAUUSD"] = 2.00

# ──────────────────────────────────────────────
#  POSITION MANAGEMENT (trailing stop / breakeven / partial TP)
# ──────────────────────────────────────────────
BREAKEVEN_TRIGGER_RR  = 1.0    # Move SL to breakeven when profit >= 1R
BREAKEVEN_BUFFER_PIPS = 2      # Buffer above entry for BE (covers spread)
TRAIL_TRIGGER_RR      = 1.5    # Start trailing after 1.5R profit
TRAIL_STEP_RATIO      = 0.5    # Trail SL at 50% of risk distance behind price

# Partial take-profit settings (default: bank partial at 2R; BE still at 1R, trail from 1.5R)
PARTIAL_TP_ENABLED     = os.getenv("PARTIAL_TP_ENABLED", "true").lower() == "true"
PARTIAL_TP_TRIGGER_RR  = float(os.getenv("PARTIAL_TP_TRIGGER_RR", "2.0"))
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

# Per-symbol optimal session hours (UTC) updated to include Asian Sessions 24/5
SYMBOL_SESSIONS = {
    "EURUSD": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "GBPUSD": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "USDJPY": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "USDCAD": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "AUDUSD": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "NZDUSD": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "USDCHF": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "EURGBP": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "EURJPY": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "GBPJPY": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "AUDJPY": {"best_hours": list(range(0, 24)), "avoid_hours": []},
    "XAUUSD": {"best_hours": list(range(0, 24)), "avoid_hours": []},
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
        "MT5_LOGIN":    str(MT5_LOGIN),
        "MT5_PASSWORD": MT5_PASSWORD,
        "MT5_SERVER":   MT5_SERVER,
    }
    if not PURE_ALGO_MODE:
        required["ANTHROPIC_API_KEY"] = ANTHROPIC_API_KEY
    missing = [k for k, v in required.items() if not v or v == "0"]
    if missing:
        log.error(f"Missing required env vars: {', '.join(missing)}")
        return False
    return True
