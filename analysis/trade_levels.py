from __future__ import annotations

from typing import Tuple

from config.settings import (
    ALGO_MAX_SL_ATR,
    ALGO_MIN_SL_ATR,
    ALGO_MAX_SL_PIPS,
    ALGO_MIN_RR_FLOOR,
    ALGO_FIB_OTE_REQUIRED,
    ALGO_FIB_OTE_LOW,
    ALGO_FIB_OTE_HIGH,
)


def _fibo_retracement(entry: float, swing_low: float, swing_high: float, direction: str) -> float:
    rng = max(abs(swing_high - swing_low), 1e-9)
    if direction == "BUY":
        return (swing_high - entry) / rng
    return (entry - swing_low) / rng


def validate_trade_plan(
    direction: str,
    entry: float,
    sl: float,
    tp: float,
    rr: float,
    atr: float,
    pip: float,
    swing_low: float,
    swing_high: float,
) -> Tuple[bool, str]:
    sl_distance = abs(entry - sl)
    if sl_distance <= 0:
        return False, "SL_ZERO_DISTANCE"

    if atr > 0:
        sl_atr = sl_distance / atr
        if sl_atr > ALGO_MAX_SL_ATR:
            return False, f"SL_TOO_WIDE_ATR({sl_atr:.2f})"
        if sl_atr < ALGO_MIN_SL_ATR:
            return False, f"SL_TOO_TIGHT_ATR({sl_atr:.2f})"

    sl_pips = sl_distance / max(pip, 1e-9)
    if sl_pips > ALGO_MAX_SL_PIPS:
        return False, f"SL_TOO_WIDE_PIPS({sl_pips:.1f})"

    if rr < ALGO_MIN_RR_FLOOR:
        return False, f"RR_TOO_LOW({rr:.2f})"

    if direction == "BUY" and not (sl < entry < tp):
        return False, "LEVEL_ORDER_INVALID_BUY"
    if direction == "SELL" and not (tp < entry < sl):
        return False, "LEVEL_ORDER_INVALID_SELL"

    if ALGO_FIB_OTE_REQUIRED:
        retr = _fibo_retracement(entry, swing_low, swing_high, direction)
        if not (ALGO_FIB_OTE_LOW <= retr <= ALGO_FIB_OTE_HIGH):
            return False, f"NOT_IN_OTE({retr:.2f})"

    return True, "OK"
