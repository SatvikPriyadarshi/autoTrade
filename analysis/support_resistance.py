"""
Support & Resistance Detection Module
Finds key price levels from swing highs/lows, clusters nearby levels,
and returns the strongest S/R zones.
"""

import pandas as pd
import numpy as np
from typing import List, Dict


def _find_swing_points(df: pd.DataFrame, window: int = 3) -> tuple[list, list]:
    """Find swing highs and swing lows using a rolling window."""
    highs_list = []
    lows_list = []
    highs = df["high"].values
    lows = df["low"].values

    for i in range(window, len(df) - window):
        # Swing high: highest point in window on both sides
        if highs[i] == max(highs[i - window: i + window + 1]):
            highs_list.append({"price": highs[i], "index": i, "time": df["time"].iloc[i]})
        # Swing low: lowest point in window on both sides
        if lows[i] == min(lows[i - window: i + window + 1]):
            lows_list.append({"price": lows[i], "index": i, "time": df["time"].iloc[i]})

    return highs_list, lows_list


def _cluster_levels(levels: list[float], tolerance: float) -> List[Dict]:
    """
    Cluster nearby price levels together.
    Returns clusters as {price, touches} sorted by touch count.
    """
    if not levels:
        return []

    levels_sorted = sorted(levels)
    clusters = []
    current_cluster = [levels_sorted[0]]

    for lvl in levels_sorted[1:]:
        if abs(lvl - current_cluster[-1]) <= tolerance:
            current_cluster.append(lvl)
        else:
            clusters.append({
                "price":   round(np.mean(current_cluster), 5),
                "touches": len(current_cluster),
            })
            current_cluster = [lvl]

    # Don't forget the last cluster
    clusters.append({
        "price":   round(np.mean(current_cluster), 5),
        "touches": len(current_cluster),
    })

    clusters.sort(key=lambda c: c["touches"], reverse=True)
    return clusters


def detect_support_resistance(
    df: pd.DataFrame,
    base_symbol: str = "EURUSD",
    lookback: int = 100,
    max_levels: int = 6,
) -> Dict[str, List[Dict]]:
    """
    Detect key support and resistance levels.
    Returns:
        {
            "resistance": [{"price": ..., "touches": ...}, ...],
            "support":    [{"price": ..., "touches": ...}, ...],
        }
    """
    from config.settings import PIP_VALUE

    df = df.tail(lookback).reset_index(drop=True)
    if len(df) < 20:
        return {"resistance": [], "support": []}

    current_price = df["close"].iloc[-1]
    pip = PIP_VALUE.get(base_symbol, 0.0001)
    tolerance = pip * 10  # Cluster levels within 10 pips

    swing_highs, swing_lows = _find_swing_points(df, window=3)

    # Combine all swing levels
    resistance_prices = [h["price"] for h in swing_highs if h["price"] > current_price]
    support_prices    = [l["price"] for l in swing_lows  if l["price"] < current_price]

    resistance = _cluster_levels(resistance_prices, tolerance)[:max_levels]
    support    = _cluster_levels(support_prices, tolerance)[:max_levels]

    # Sort by proximity to current price
    resistance.sort(key=lambda r: r["price"])
    support.sort(key=lambda s: s["price"], reverse=True)

    return {"resistance": resistance, "support": support}


def format_sr_for_prompt(sr_data: dict, base_symbol: str = "EURUSD") -> str:
    """Format S/R levels as text for the Claude prompt."""
    from config.settings import PIP_VALUE
    decimals = 2 if base_symbol == "XAUUSD" else 5

    lines = []
    if sr_data["resistance"]:
        lines.append("Resistance levels:")
        for r in sr_data["resistance"][:3]:
            lines.append(f"  - {r['price']:.{decimals}f} ({r['touches']} touches)")
    else:
        lines.append("Resistance: None detected nearby")

    if sr_data["support"]:
        lines.append("Support levels:")
        for s in sr_data["support"][:3]:
            lines.append(f"  - {s['price']:.{decimals}f} ({s['touches']} touches)")
    else:
        lines.append("Support: None detected nearby")

    return "\n".join(lines)
