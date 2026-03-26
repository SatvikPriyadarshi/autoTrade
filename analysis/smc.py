"""
SMC (Smart Money Concepts) detection.
Order blocks, FVGs (min size + fill tracking), BOS/CHOCH, mitigated-OB retests,
and ICT-style breaker blocks (swing swept then reclaimed).
"""

import pandas as pd
from typing import List, Dict
from config.settings import FVG_MIN_SIZE_PIPS, PIP_VALUE, log


# ──────────────────────────────────────────────
#  ORDER BLOCKS
# ──────────────────────────────────────────────
def detect_order_blocks(df: pd.DataFrame, lookback: int = 50, active_only: bool = True) -> List[Dict]:
    """
    Detect bullish/bearish order blocks.
    A bullish OB = bearish candle followed by a strong bullish impulse breaking its high.
    A bearish OB = bullish candle followed by a strong bearish impulse breaking its low.
    Returns active (un-mitigated) OBs sorted by strength by default; set active_only=False for all OBs.
    """
    obs = []
    df = df.tail(lookback).reset_index(drop=True)

    for i in range(2, len(df) - 3):
        candle = df.iloc[i]
        next_c = df.iloc[i + 1]

        body_size = abs(candle["close"] - candle["open"])
        if body_size == 0:
            continue

        # ── Bullish OB: bearish candle → strong bullish impulse ──
        if candle["close"] < candle["open"]:
            impulse = next_c["close"] - next_c["open"]
            if impulse > body_size * 1.5 and next_c["close"] > candle["high"]:
                strength = round(impulse / body_size, 2)
                obs.append({
                    "type":      "Bullish",
                    "high":      candle["open"],
                    "low":       candle["low"],
                    "time":      candle["time"],
                    "strength":  strength,
                    "mitigated": False,
                })

        # ── Bearish OB: bullish candle → strong bearish impulse ──
        if candle["close"] > candle["open"]:
            impulse = next_c["open"] - next_c["close"]
            if impulse > body_size * 1.5 and next_c["close"] < candle["low"]:
                strength = round(impulse / body_size, 2)
                obs.append({
                    "type":      "Bearish",
                    "high":      candle["high"],
                    "low":       candle["close"],
                    "time":      candle["time"],
                    "strength":  strength,
                    "mitigated": False,
                })

    # Mark mitigated OBs by checking if subsequent candles broke through
    current_price = df["close"].iloc[-1]
    for ob in obs:
        ob_idx = df[df["time"] == ob["time"]].index
        if len(ob_idx) == 0:
            continue
        start = ob_idx[0] + 2  # Start checking 2 candles after OB
        subsequent = df.iloc[start:]

        if ob["type"] == "Bullish":
            # Bullish OB mitigated only if price clearly breaks below its low
            low_val = float(ob["low"])
            if len(subsequent) > 0 and subsequent["low"].min() < (low_val - (low_val * 0.0001)):
                ob["mitigated"] = True
        elif ob["type"] == "Bearish":
            # Bearish OB mitigated only if price clearly breaks above its high
            high_val = float(ob["high"])
            if len(subsequent) > 0 and subsequent["high"].max() > (high_val + (high_val * 0.0001)):
                ob["mitigated"] = True

    if not active_only:
        obs.sort(key=lambda x: x["strength"], reverse=True)
        return obs
    active = [ob for ob in obs if not ob["mitigated"]]
    active.sort(key=lambda x: x["strength"], reverse=True)
    return active


# ──────────────────────────────────────────────
#  BREAKER / MITIGATED OB RETEST (SMC)
# ──────────────────────────────────────────────
def detect_breaker_retests(df: pd.DataFrame, lookback: int = 50) -> List[Dict]:
    """
    Mitigated order blocks that price has traded back into — common SMC "breaker" retest idea.
    Only zones where current close is inside the original OB range and the OB was mitigated.
    """
    all_obs = detect_order_blocks(df, lookback=lookback, active_only=False)
    current = float(df["close"].iloc[-1])
    out: List[Dict] = []
    for ob in all_obs:
        if not ob.get("mitigated"):
            continue
        lo, hi = float(ob["low"]), float(ob["high"])
        if lo <= current <= hi:
            out.append({
                "type": ob["type"],
                "high": hi,
                "low": lo,
                "time": ob["time"],
                "strength": ob.get("strength", 0),
                "kind": "BreakerRetest",
            })
    out.sort(key=lambda x: x["strength"], reverse=True)
    return out


# ──────────────────────────────────────────────
#  ICT-STYLE BREAKER BLOCKS (failed swing + reclaim)
# ──────────────────────────────────────────────
def detect_ict_breaker_blocks(
    df: pd.DataFrame,
    base_symbol: str = "EURUSD",
    lookback: int = 100,
) -> List[Dict]:
    """
    Stricter SMC/ICT breaker: liquidity run beyond a swing, then reclaim through the level.

    Bullish breaker: fractal swing low is swept (wick below the low), later session closes back above
    that low → former sell-side liquidity / failed breakdown; zone from swept low to sweep-candle high.

    Bearish breaker: swing high swept, close back below → zone from sweep-candle low to broken high.

    Only returns patterns that are still structurally valid: close must have reclaimed relative to the swing.
    """
    df = df.tail(lookback).reset_index(drop=True)
    n = len(df)
    if n < 15:
        return []

    pip = PIP_VALUE.get(base_symbol, 0.0001)
    tol = max(pip * 3, 1e-12)

    highs = df["high"].values.astype(float)
    lows = df["low"].values.astype(float)
    closes = df["close"].values.astype(float)

    swing_lows: List[tuple] = []
    swing_highs: List[tuple] = []
    for i in range(2, n - 2):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1] and lows[i] <= lows[i - 2] and lows[i] <= lows[i + 2]:
            swing_lows.append((i, float(lows[i])))
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1] and highs[i] >= highs[i - 2] and highs[i] >= highs[i + 2]:
            swing_highs.append((i, float(highs[i])))

    current = float(closes[-1])
    bullish_breakers: List[Dict] = []
    seen_sl = set()

    for idx, sl in swing_lows:
        if idx >= n - 3:
            continue
        sweep_j = None
        for j in range(idx + 1, n):
            if float(lows[j]) < sl - tol:
                sweep_j = j
                break
        if sweep_j is None:
            continue
        reclaimed = False
        for k in range(sweep_j + 1, n):
            if float(closes[k]) > sl + tol:
                reclaimed = True
                break
        if not reclaimed:
            continue
        if current <= sl - tol * 5:
            continue
        key = round(sl, 5)
        if key in seen_sl:
            continue
        seen_sl.add(key)
        hi_zone = max(float(highs[sweep_j]), sl + tol)
        lo_zone = sl - tol * 2
        bullish_breakers.append({
            "type": "Bullish",
            "low": lo_zone,
            "high": hi_zone,
            "time": df["time"].iloc[idx],
            "kind": "ICTBreaker",
            "swing_level": sl,
            "sweep_idx": int(sweep_j),
        })

    bearish_breakers: List[Dict] = []
    seen_sh = set()
    for idx, sh in swing_highs:
        if idx >= n - 3:
            continue
        sweep_j = None
        for j in range(idx + 1, n):
            if float(highs[j]) > sh + tol:
                sweep_j = j
                break
        if sweep_j is None:
            continue
        reclaimed = False
        for k in range(sweep_j + 1, n):
            if float(closes[k]) < sh - tol:
                reclaimed = True
                break
        if not reclaimed:
            continue
        if current >= sh + tol * 5:
            continue
        key = round(sh, 5)
        if key in seen_sh:
            continue
        seen_sh.add(key)
        lo_zone = min(float(lows[sweep_j]), sh - tol)
        hi_zone = sh + tol * 2
        bearish_breakers.append({
            "type": "Bearish",
            "low": lo_zone,
            "high": hi_zone,
            "time": df["time"].iloc[idx],
            "kind": "ICTBreaker",
            "swing_level": sh,
            "sweep_idx": int(sweep_j),
        })

    combined = bullish_breakers + bearish_breakers
    combined.sort(key=lambda x: int(x.get("sweep_idx", 0)), reverse=True)
    return combined[:16]


# ──────────────────────────────────────────────
#  FAIR VALUE GAPS (with min-size filter + proper fill tracking)
# ──────────────────────────────────────────────
def detect_fvg(
    df: pd.DataFrame,
    base_symbol: str = "EURUSD",
    lookback: int = 50,
) -> List[Dict]:
    """
    Detect bullish/bearish Fair Value Gaps.
    - Filters out tiny noise gaps using per-symbol minimum size.
    - Tracks fill status by checking if subsequent candles traded through the gap.
    """
    fvgs = []
    df = df.tail(lookback).reset_index(drop=True)

    pip = PIP_VALUE.get(base_symbol, 0.0001)
    min_size = FVG_MIN_SIZE_PIPS.get(base_symbol, 3) * pip

    for i in range(1, len(df) - 1):
        prev = df.iloc[i - 1]
        curr = df.iloc[i]
        nxt  = df.iloc[i + 1]

        # ── Bullish FVG: gap between candle 1 high and candle 3 low ──
        if prev["high"] < nxt["low"]:
            gap_size = nxt["low"] - prev["high"]
            if gap_size >= min_size:
                fvgs.append({
                    "type":     "Bullish",
                    "high":     nxt["low"],
                    "low":      prev["high"],
                    "size":     round(gap_size, 6),
                    "time":     curr["time"],
                    "candle_idx": i,
                    "filled":   False,
                })

        # ── Bearish FVG: gap between candle 1 low and candle 3 high ──
        if prev["low"] > nxt["high"]:
            gap_size = prev["low"] - nxt["high"]
            if gap_size >= min_size:
                fvgs.append({
                    "type":     "Bearish",
                    "high":     prev["low"],
                    "low":      nxt["high"],
                    "size":     round(gap_size, 6),
                    "time":     curr["time"],
                    "candle_idx": i,
                    "filled":   False,
                })

    # ── Check fill status using subsequent candle wicks ──
    for fvg in fvgs:
        idx = int(fvg.get("candle_idx", 0))
        start_idx = idx + 2  # Check candles after the FVG formed
        if start_idx >= len(df):
            continue
        subsequent = df.iloc[start_idx:]

        if fvg["type"] == "Bullish":
            # Bullish FVG filled only if price breaks COMPLETELY through the bottom
            # (Allows price to sit inside the gap for entry triggers)
            low_val = float(fvg["low"])
            if len(subsequent) > 0 and subsequent["low"].min() < (low_val - (low_val * 0.00005)):
                fvg["filled"] = True
        elif fvg["type"] == "Bearish":
            # Bearish FVG filled only if price rose COMPLETELY through the top
            high_val = float(fvg["high"])
            if len(subsequent) > 0 and subsequent["high"].max() > (high_val + (high_val * 0.00005)):
                fvg["filled"] = True

    # Clean up internal field and return only open FVGs
    for fvg in fvgs:
        fvg.pop("candle_idx", None)

    open_fvgs = [f for f in fvgs if not f["filled"]]
    open_fvgs.reverse()  # Most recent first
    return open_fvgs


# ──────────────────────────────────────────────
#  MARKET STRUCTURE: BOS / CHOCH
# ──────────────────────────────────────────────
def detect_bos_choch(df: pd.DataFrame, lookback: int = 50) -> str:
    """
    Classify market structure using swing highs/lows.
    Returns a text description: BOS Bullish/Bearish, CHOCH, HH+HL, LL+LH, Ranging.
    """
    df = df.tail(lookback).reset_index(drop=True)
    highs = df["high"].values
    lows  = df["low"].values
    n = len(df)

    if n < 10:
        return "Insufficient data"

    swing_highs = []
    swing_lows  = []

    for i in range(2, n - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1] and highs[i] > highs[i-2] and highs[i] > highs[i+2]:
            swing_highs.append((i, highs[i]))
        if lows[i] < lows[i-1] and lows[i] < lows[i+1] and lows[i] < lows[i-2] and lows[i] < lows[i+2]:
            swing_lows.append((i, lows[i]))

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return "Structure unclear"

    last_hh = swing_highs[-1][1]
    prev_hh = swing_highs[-2][1]
    last_ll = swing_lows[-1][1]
    prev_ll = swing_lows[-2][1]
    current_close = df["close"].iloc[-1]

    bullish = last_hh > prev_hh and last_ll > prev_ll
    bearish = last_hh < prev_hh and last_ll < prev_ll

    if bullish:
        if current_close > last_hh:
            return "BOS Bullish (price broke above last swing high)"
        return "Higher Highs + Higher Lows (Uptrend)"

    if bearish:
        if current_close < last_ll:
            return "BOS Bearish (price broke below last swing low)"
        return "Lower Highs + Lower Lows (Downtrend)"

    if last_hh < prev_hh and last_ll > prev_ll:
        return "CHOCH Bullish Reversal — possible uptrend"
    if last_hh > prev_hh and last_ll < prev_ll:
        return "CHOCH Bearish Reversal — possible downtrend"

    return "Ranging"


def get_market_bias(df: pd.DataFrame) -> Dict[str, str]:
    """Advanced Market Bias combining BOS and CHOCH."""
    structure = detect_bos_choch(df)
    bias = "NEUTRAL"
    if "BOS BULLISH" in structure.upper() or "UPTREND" in structure.upper() or "CHOCH BULLISH" in structure.upper():
        bias = "BULLISH"
    elif "BOS BEARISH" in structure.upper() or "DOWNTREND" in structure.upper() or "CHOCH BEARISH" in structure.upper():
        bias = "BEARISH"
    return {"bias": bias, "structure": structure}


def get_enriched_market_structure(df: pd.DataFrame) -> Dict:
    """
    Full H1 structure context for the algo: bias text + explicit BOS/CHOCH flags.
    """
    structure = detect_bos_choch(df)
    u = structure.upper()
    bias = "NEUTRAL"
    if "BOS BULLISH" in u or "UPTREND" in u or "CHOCH BULLISH" in u:
        bias = "BULLISH"
    elif "BOS BEARISH" in u or "DOWNTREND" in u or "CHOCH BEARISH" in u:
        bias = "BEARISH"
    return {
        "bias": bias,
        "structure": structure,
        "bos_bullish": "BOS BULLISH" in u,
        "bos_bearish": "BOS BEARISH" in u,
        "choch_bullish": "CHOCH BULLISH" in u,
        "choch_bearish": "CHOCH BEARISH" in u,
        "hh_hl_uptrend": "HIGHER HIGHS" in u and "HIGHER LOWS" in u,
        "lh_ll_downtrend": "LOWER HIGHS" in u and "LOWER LOWS" in u,
        "ranging": "RANGING" in u,
        "unclear": "UNCLEAR" in u or "INSUFFICIENT" in u,
    }


def get_structure_bias(structure_text: str) -> str:
    """Extract directional bias from structure text. Returns 'BULLISH', 'BEARISH', or 'NEUTRAL'."""
    text = structure_text.upper()
    if "BULLISH" in text or "UPTREND" in text or "HIGHER" in text:
        return "BULLISH"
    if "BEARISH" in text or "DOWNTREND" in text or "LOWER" in text:
        return "BEARISH"
    return "NEUTRAL"


def get_recent_swings(df: pd.DataFrame, lookback: int = 40) -> Dict[str, float]:
    """
    Finds the absolute most recent structural Swing High and Swing Low.
    Used for logical SL/TP placement.
    """
    df = df.tail(lookback).reset_index(drop=True)
    highs = [float(h) for h in df["high"].tolist()]
    lows  = [float(l) for l in df["low"].tolist()]
    n = len(highs)
    
    if n < 5:
        return {"high": float(df["high"].max()), "low": float(df["low"].min())}
        
    last_high = highs[0]
    last_low = lows[0]
    
    # Peak/Trough detection
    for i in range(2, n - 2):
        # Swing High
        if highs[i] > highs[i-1] and highs[i] > highs[i+1] and highs[i] > highs[i-2] and highs[i] > highs[i+2]:
            last_high = highs[i]
        # Swing Low
        if lows[i] < lows[i-1] and lows[i] < lows[i+1] and lows[i] < lows[i-2] and lows[i] < lows[i+2]:
            last_low = lows[i]
            
    return {"high": float(last_high), "low": float(last_low)}


def get_p_d_zones(current_price: float, swing_high: float, swing_low: float) -> str:
    """Classify if price is in Premium, Discount, or Equilibrium zone."""
    rng = swing_high - swing_low
    if rng <= 0: return "EQUILIBRIUM"
    
    eq = swing_low + (rng * 0.5)
    if current_price > (swing_low + (rng * 0.55)):
        return "PREMIUM"
    if current_price < (swing_low + (rng * 0.45)):
        return "DISCOUNT"
    return "EQUILIBRIUM"
