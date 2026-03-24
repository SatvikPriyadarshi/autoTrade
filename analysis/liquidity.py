"""
Liquidity Sweep Detection Module
Detects stop hunts / liquidity grabs — a core SMC concept.
A sweep occurs when price briefly breaks a key level then reverses back,
indicating smart money grabbed liquidity.
"""

import pandas as pd
from typing import List, Dict


def detect_liquidity_sweeps(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    """
    Detect recent liquidity sweeps (stop hunts).

    Bullish sweep: Price wicks BELOW a previous swing low, then closes back above.
    → Smart money grabbed sell-side liquidity → expect price to rise.

    Bearish sweep: Price wicks ABOVE a previous swing high, then closes back below.
    → Smart money grabbed buy-side liquidity → expect price to fall.
    """
    sweeps = []
    df = df.tail(lookback).reset_index(drop=True)
    n = len(df)

    if n < 10:
        return sweeps

    # Build swing highs and lows over a rolling window
    swing_window = 5

    for i in range(swing_window + 1, n):
        candle = df.iloc[i]

        # Look at the swing highs/lows in the window before this candle
        window_df = df.iloc[max(0, i - swing_window * 2): i]
        if len(window_df) < swing_window:
            continue

        window_highs = window_df["high"].values
        window_lows  = window_df["low"].values
        prev_swing_high = window_highs.max()
        prev_swing_low  = window_lows.min()

        # ── Bearish Sweep: wick above swing high, close below ──
        if candle["high"] > prev_swing_high and candle["close"] < prev_swing_high:
            # Confirm the sweep with the close being in the lower half of the candle
            candle_range = candle["high"] - candle["low"]
            if candle_range > 0:
                close_position = (candle["close"] - candle["low"]) / candle_range
                if close_position < 0.6:  # Close in lower 60% = bearish rejection
                    sweeps.append({
                        "type":       "Bearish Sweep",
                        "level":      round(prev_swing_high, 5),
                        "sweep_high": round(candle["high"], 5),
                        "time":       candle["time"],
                        "strength":   round((candle["high"] - prev_swing_high) / candle_range, 3),
                    })

        # ── Bullish Sweep: wick below swing low, close above ──
        if candle["low"] < prev_swing_low and candle["close"] > prev_swing_low:
            candle_range = candle["high"] - candle["low"]
            if candle_range > 0:
                close_position = (candle["close"] - candle["low"]) / candle_range
                if close_position > 0.4:  # Close in upper 60% = bullish rejection
                    sweeps.append({
                        "type":       "Bullish Sweep",
                        "level":      round(prev_swing_low, 5),
                        "sweep_low":  round(candle["low"], 5),
                        "time":       candle["time"],
                        "strength":   round((prev_swing_low - candle["low"]) / candle_range, 3),
                    })

    # Return only the most recent sweeps (last 5)
    return sweeps[-5:]


def detect_equal_highs_lows(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    """
    Detect equal highs / equal lows — strong liquidity pools.
    Two or more swing points at nearly the same price level.
    """
    pools = []
    df = df.tail(lookback).reset_index(drop=True)
    n = len(df)

    if n < 15:
        return pools

    # Find swing highs and lows
    swing_highs = []
    swing_lows  = []

    for i in range(2, n - 2):
        if (df["high"].iloc[i] >= df["high"].iloc[i-1]
                and df["high"].iloc[i] >= df["high"].iloc[i+1]
                and df["high"].iloc[i] >= df["high"].iloc[i-2]
                and df["high"].iloc[i] >= df["high"].iloc[i+2]):
            swing_highs.append({"price": df["high"].iloc[i], "idx": i})

        if (df["low"].iloc[i] <= df["low"].iloc[i-1]
                and df["low"].iloc[i] <= df["low"].iloc[i+1]
                and df["low"].iloc[i] <= df["low"].iloc[i-2]
                and df["low"].iloc[i] <= df["low"].iloc[i+2]):
            swing_lows.append({"price": df["low"].iloc[i], "idx": i})

    # Check for equal highs (buy-side liquidity above)
    for i in range(len(swing_highs)):
        for j in range(i + 1, len(swing_highs)):
            h1 = swing_highs[i]["price"]
            h2 = swing_highs[j]["price"]
            avg = (h1 + h2) / 2
            if avg > 0 and abs(h1 - h2) / avg < 0.001:  # Within 0.1%
                pools.append({
                    "type":  "Equal Highs (Buy-side liquidity)",
                    "price": round(avg, 5),
                })

    # Check for equal lows (sell-side liquidity below)
    for i in range(len(swing_lows)):
        for j in range(i + 1, len(swing_lows)):
            l1 = swing_lows[i]["price"]
            l2 = swing_lows[j]["price"]
            avg = (l1 + l2) / 2
            if avg > 0 and abs(l1 - l2) / avg < 0.001:
                pools.append({
                    "type":  "Equal Lows (Sell-side liquidity)",
                    "price": round(avg, 5),
                })

    return pools


def format_liquidity_for_prompt(sweeps: list, pools: list) -> str:
    """Format liquidity data as text for the Claude prompt."""
    lines = []

    if sweeps:
        lines.append("Recent Liquidity Sweeps:")
        for s in sweeps[-3:]:
            lines.append(f"  - {s['type']} at {s['level']}")
    else:
        lines.append("Recent Liquidity Sweeps: None detected")

    if pools:
        lines.append("Liquidity Pools:")
        for p in pools[-3:]:
            lines.append(f"  - {p['type']} at {p['price']}")

    return "\n".join(lines)
