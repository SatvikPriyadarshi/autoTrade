"""
Higher-Timeframe Trend Filter
Uses H4 data to determine the dominant trend direction.
Trades should only be taken in alignment with this trend.
"""

import pandas as pd
from config.settings import log


def detect_htf_trend(df_h4: pd.DataFrame, lookback: int = 80) -> dict:
    """
    Analyze H4 candles to determine the dominant trend.
    Uses multiple methods for confluence:
      1. EMA crossover (20 EMA vs 50 EMA)
      2. Higher-highs/lower-lows structure
      3. Price position relative to EMA

    Returns:
        {
            "direction": "BULLISH" | "BEARISH" | "NEUTRAL",
            "strength":  "STRONG" | "MODERATE" | "WEAK",
            "ema_20":    float,
            "ema_50":    float,
            "description": str,
        }
    """
    df = df_h4.tail(lookback).reset_index(drop=True)
    if len(df) < 55:
        return {
            "direction": "NEUTRAL",
            "strength":  "WEAK",
            "ema_20": 0, "ema_50": 0,
            "description": "Insufficient H4 data for trend analysis",
        }

    close = df["close"]
    current_price = close.iloc[-1]

    # ── EMA calculation ──
    ema_20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
    ema_50 = close.ewm(span=50, adjust=False).mean().iloc[-1]

    # ── EMA-based trend signal ──
    ema_bullish = ema_20 > ema_50 and current_price > ema_20
    ema_bearish = ema_20 < ema_50 and current_price < ema_20

    # ── Swing structure analysis (last 30 candles) ──
    recent = df.tail(30).reset_index(drop=True)
    highs = recent["high"].values
    lows  = recent["low"].values
    n = len(recent)

    swing_highs = []
    swing_lows  = []
    for i in range(2, n - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            swing_lows.append(lows[i])

    structure_bullish = False
    structure_bearish = False
    if len(swing_highs) >= 2 and len(swing_lows) >= 2:
        structure_bullish = swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2]
        structure_bearish = swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2]

    # ── Combine signals ──
    bull_score = int(ema_bullish) + int(structure_bullish) + int(current_price > ema_50)
    bear_score = int(ema_bearish) + int(structure_bearish) + int(current_price < ema_50)

    if bull_score >= 2:
        direction = "BULLISH"
        strength = "STRONG" if bull_score == 3 else "MODERATE"
        desc = f"H4 Uptrend (EMA20={ema_20:.5f} > EMA50={ema_50:.5f}, HH+HL structure)"
    elif bear_score >= 2:
        direction = "BEARISH"
        strength = "STRONG" if bear_score == 3 else "MODERATE"
        desc = f"H4 Downtrend (EMA20={ema_20:.5f} < EMA50={ema_50:.5f}, LH+LL structure)"
    else:
        direction = "NEUTRAL"
        strength = "WEAK"
        desc = f"H4 No clear trend (EMA20={ema_20:.5f}, EMA50={ema_50:.5f})"

    return {
        "direction":   direction,
        "strength":    strength,
        "ema_20":      round(ema_20, 5),
        "ema_50":      round(ema_50, 5),
        "description": desc,
    }


def is_trade_aligned_with_trend(trade_direction: str, htf_trend: dict) -> bool:
    """
    Check if a proposed trade direction aligns with the HTF trend.
    Returns True if aligned or trend is neutral (allow both directions).
    """
    trend_dir = htf_trend["direction"]

    # Neutral trend: allow both directions (but with reduced confidence)
    if trend_dir == "NEUTRAL":
        return True

    # Strong trend mismatch: block the trade
    if trade_direction == "BUY" and trend_dir == "BEARISH":
        if htf_trend["strength"] == "STRONG":
            log.info(f"Trade blocked: BUY against STRONG BEARISH H4 trend")
            return False
        log.warning(f"Trade warning: BUY against MODERATE BEARISH H4 trend")
        return False

    if trade_direction == "SELL" and trend_dir == "BULLISH":
        if htf_trend["strength"] == "STRONG":
            log.info(f"Trade blocked: SELL against STRONG BULLISH H4 trend")
            return False
        log.warning(f"Trade warning: SELL against MODERATE BULLISH H4 trend")
        return False

    return True
