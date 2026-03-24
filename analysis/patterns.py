"""
Chart Pattern Detection Module — Cleaned & Enhanced
Detects candlestick patterns and simple price structure patterns.
"""

import pandas as pd
from typing import List


def detect_chart_patterns(df: pd.DataFrame) -> List[str]:
    """
    Scan the last 3 candles for candlestick patterns and
    the last 20 candles for structure patterns (double top/bottom).
    Returns a list of pattern name strings.
    """
    patterns = []
    if len(df) < 10:
        return patterns

    df = df.reset_index(drop=True)
    n = len(df)

    c0 = df.iloc[n - 1]   # Current candle
    c1 = df.iloc[n - 2]   # Previous candle
    c2 = df.iloc[n - 3]   # Two candles ago

    def body(c):       return abs(c["close"] - c["open"])
    def upper_wick(c): return c["high"] - max(c["close"], c["open"])
    def lower_wick(c): return min(c["close"], c["open"]) - c["low"]
    def is_bullish(c): return c["close"] > c["open"]
    def is_bearish(c): return c["close"] < c["open"]
    def full_range(c): return c["high"] - c["low"]

    # ── 1. Bullish Engulfing ──
    if (is_bearish(c1) and is_bullish(c0)
            and c0["open"] <= c1["close"]
            and c0["close"] >= c1["open"]
            and body(c0) > body(c1)):
        patterns.append("Bullish Engulfing")

    # ── 2. Bearish Engulfing ──
    if (is_bullish(c1) and is_bearish(c0)
            and c0["open"] >= c1["close"]
            and c0["close"] <= c1["open"]
            and body(c0) > body(c1)):
        patterns.append("Bearish Engulfing")

    # ── 3. Bullish Pin Bar (Hammer) ──
    if (body(c0) > 0
            and lower_wick(c0) > body(c0) * 2
            and upper_wick(c0) < body(c0) * 0.5):
        patterns.append("Bullish Pin Bar (Hammer)")

    # ── 4. Bearish Pin Bar (Shooting Star) ──
    if (body(c0) > 0
            and upper_wick(c0) > body(c0) * 2
            and lower_wick(c0) < body(c0) * 0.5):
        patterns.append("Bearish Pin Bar (Shooting Star)")

    # ── 5. Doji ──
    rng = full_range(c0)
    if rng > 0 and body(c0) < rng * 0.1:
        patterns.append("Doji (indecision)")

    # ── 6. Morning Star ──
    if (is_bearish(c2)
            and body(c1) < body(c2) * 0.5
            and is_bullish(c0)
            and c0["close"] > (c2["open"] + c2["close"]) / 2):
        patterns.append("Morning Star (Bullish Reversal)")

    # ── 7. Evening Star ──
    if (is_bullish(c2)
            and body(c1) < body(c2) * 0.5
            and is_bearish(c0)
            and c0["close"] < (c2["open"] + c2["close"]) / 2):
        patterns.append("Evening Star (Bearish Reversal)")

    # ── 8. Three White Soldiers ──
    if (is_bullish(c2) and is_bullish(c1) and is_bullish(c0)
            and c1["open"] > c2["open"] and c0["open"] > c1["open"]
            and c1["close"] > c2["close"] and c0["close"] > c1["close"]):
        patterns.append("Three White Soldiers (Strong Bullish)")

    # ── 9. Three Black Crows ──
    if (is_bearish(c2) and is_bearish(c1) and is_bearish(c0)
            and c1["open"] < c2["open"] and c0["open"] < c1["open"]
            and c1["close"] < c2["close"] and c0["close"] < c1["close"]):
        patterns.append("Three Black Crows (Strong Bearish)")

    # ── 10. Double Top ──
    recent = df.tail(20)
    highs = recent["high"].values
    if len(highs) >= 10:
        peak1_idx = highs[:10].argmax()
        peak2_idx = highs[10:].argmax() + 10
        peak1 = highs[peak1_idx]
        peak2 = highs[peak2_idx]
        if abs(peak1 - peak2) / peak1 < 0.002 and abs(peak1_idx - peak2_idx) >= 3:
            patterns.append("Double Top (Bearish)")

    # ── 11. Double Bottom ──
    lows = recent["low"].values
    if len(lows) >= 10:
        bot1_idx = lows[:10].argmin()
        bot2_idx = lows[10:].argmin() + 10
        bot1 = lows[bot1_idx]
        bot2 = lows[bot2_idx]
        if abs(bot1 - bot2) / bot1 < 0.002 and abs(bot1_idx - bot2_idx) >= 3:
            patterns.append("Double Bottom (Bullish)")

    return patterns


def confirm_m5_entry(df_m5: "pd.DataFrame", direction: str) -> tuple[bool, str]:
    """
    M5 Entry Confirmation Gate — checks the last 3 M5 candles for a
    confirmation signal aligned with the intended trade direction.

    Works for ALL pairs: EURUSD, GBPUSD, USDJPY, XAUUSD.
    Returns (confirmed: bool, pattern_found: str).

    BUY confirmation requires: bullish engulfing, hammer/pin bar,
                               or 2 consecutive bullish closes.
    SELL confirmation requires: bearish engulfing, shooting star,
                                or 2 consecutive bearish closes.
    """
    if df_m5 is None or len(df_m5) < 4:
        return True, "M5 data unavailable — skipping confirmation"  # Don't block if no data

    df = df_m5.reset_index(drop=True)
    n = len(df)
    c0 = df.iloc[n - 1]  # Most recent closed M5 candle
    c1 = df.iloc[n - 2]
    c2 = df.iloc[n - 3]

    def body(c):       return abs(c["close"] - c["open"])
    def upper_wick(c): return c["high"] - max(c["close"], c["open"])
    def lower_wick(c): return min(c["close"], c["open"]) - c["low"]
    def is_bull(c):    return c["close"] > c["open"]
    def is_bear(c):    return c["close"] < c["open"]
    def full_range(c): return c["high"] - c["low"]

    if direction == "BUY":
        # 1. Bullish Engulfing on M5
        if (is_bear(c1) and is_bull(c0)
                and c0["open"] <= c1["close"]
                and c0["close"] >= c1["open"]
                and body(c0) > body(c1)):
            return True, "M5 Bullish Engulfing"

        # 2. Hammer / Bullish Pin Bar on M5
        if (body(c0) > 0
                and lower_wick(c0) > body(c0) * 1.5
                and upper_wick(c0) < body(c0) * 0.8
                and is_bull(c0)):
            return True, "M5 Bullish Hammer"

        # 3. Two consecutive bullish closes (momentum confirmation)
        if is_bull(c1) and is_bull(c0) and c0["close"] > c1["close"]:
            return True, "M5 Two Consecutive Bullish Candles"

        # 4. Strong bullish candle (body > 60% of range)
        if (is_bull(c0) and full_range(c0) > 0
                and body(c0) > full_range(c0) * 0.6):
            return True, "M5 Strong Bullish Close"

        return False, "No M5 bullish confirmation"

    elif direction == "SELL":
        # 1. Bearish Engulfing on M5
        if (is_bull(c1) and is_bear(c0)
                and c0["open"] >= c1["close"]
                and c0["close"] <= c1["open"]
                and body(c0) > body(c1)):
            return True, "M5 Bearish Engulfing"

        # 2. Shooting Star / Bearish Pin Bar on M5
        if (body(c0) > 0
                and upper_wick(c0) > body(c0) * 1.5
                and lower_wick(c0) < body(c0) * 0.8
                and is_bear(c0)):
            return True, "M5 Bearish Shooting Star"

        # 3. Two consecutive bearish closes (momentum confirmation)
        if is_bear(c1) and is_bear(c0) and c0["close"] < c1["close"]:
            return True, "M5 Two Consecutive Bearish Candles"

        # 4. Strong bearish candle (body > 60% of range)
        if (is_bear(c0) and full_range(c0) > 0
                and body(c0) > full_range(c0) * 0.6):
            return True, "M5 Strong Bearish Close"

        return False, "No M5 bearish confirmation"

    return True, "Direction unknown — skipping M5 check"

