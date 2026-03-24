"""
Multi-Timeframe Confirmation Module
Provides M5 + M15 + H1 alignment analysis for higher probability entries.
"""

import pandas as pd
import MetaTrader5 as mt5
from typing import Dict, List, Tuple, Optional
from config.settings import log


def analyze_multi_timeframe_alignment(
    symbol: str,
    df_m5: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    trade_direction: str
) -> Dict:
    """
    Analyze alignment across M5, M15, and H1 timeframes.
    Returns comprehensive alignment analysis.
    """
    if df_m5.empty or df_m15.empty or df_h1.empty:
        return {
            "aligned": False,
            "score": 0,
            "confidence": 0,
            "timeframe_alignment": {},
            "conflicts": ["Insufficient data"],
            "recommendation": "WAIT"
        }
    
    # Analyze each timeframe
    m5_analysis = _analyze_timeframe(df_m5, "M5", trade_direction)
    m15_analysis = _analyze_timeframe(df_m15, "M15", trade_direction)
    h1_analysis = _analyze_timeframe(df_h1, "H1", trade_direction)
    
    # Calculate alignment score
    alignment_scores = {
        "M5": m5_analysis["score"],
        "M15": m15_analysis["score"],
        "H1": h1_analysis["score"]
    }
    
    total_score = sum(alignment_scores.values())
    max_score = 15  # 5 points per timeframe * 3 timeframes
    confidence = (total_score / max_score) * 100
    
    # Check for conflicts
    conflicts = []
    if m5_analysis["direction"] != trade_direction and m5_analysis["score"] < 3:
        conflicts.append(f"M5 shows {m5_analysis['direction']} bias")
    if m15_analysis["direction"] != trade_direction and m15_analysis["score"] < 3:
        conflicts.append(f"M15 shows {m15_analysis['direction']} bias")
    if h1_analysis["direction"] != trade_direction and h1_analysis["score"] < 3:
        conflicts.append(f"H1 shows {h1_analysis['direction']} bias")
    
    # Determine if aligned
    aligned = (
        m5_analysis["direction"] == trade_direction and m5_analysis["score"] >= 3 and
        m15_analysis["direction"] == trade_direction and m15_analysis["score"] >= 3 and
        h1_analysis["direction"] == trade_direction and h1_analysis["score"] >= 3
    )
    
    # Generate recommendation
    if aligned and confidence >= 80:
        recommendation = "STRONG_BUY" if trade_direction == "BUY" else "STRONG_SELL"
    elif aligned and confidence >= 60:
        recommendation = "BUY" if trade_direction == "BUY" else "SELL"
    elif total_score >= 8:  # At least moderate alignment
        recommendation = "WEAK_BUY" if trade_direction == "BUY" else "WEAK_SELL"
    else:
        recommendation = "WAIT"
    
    return {
        "aligned": aligned,
        "score": total_score,
        "confidence": round(confidence, 1),
        "timeframe_alignment": {
            "M5": m5_analysis,
            "M15": m15_analysis,
            "H1": h1_analysis
        },
        "conflicts": conflicts,
        "recommendation": recommendation,
        "summary": f"{trade_direction} alignment: M5={m5_analysis['score']}/5, "
                  f"M15={m15_analysis['score']}/5, H1={h1_analysis['score']}/5, "
                  f"Total={total_score}/15 ({confidence:.1f}%)"
    }


def _analyze_timeframe(df: pd.DataFrame, timeframe: str, target_direction: str) -> Dict:
    """
    Analyze a single timeframe for directional bias.
    Returns score (0-5) and analysis details.
    """
    if df.empty or len(df) < 10:
        return {
            "direction": "NEUTRAL",
            "score": 0,
            "details": ["Insufficient data"],
            "indicators": {}
        }
    
    # Get latest prices
    current_close = df["close"].iloc[-1]
    prev_close = df["close"].iloc[-2]
    
    # 1. Price momentum (1 point)
    price_momentum = "BULLISH" if current_close > prev_close else "BEARISH" if current_close < prev_close else "NEUTRAL"
    momentum_score = 1 if price_momentum == target_direction else 0
    
    # 2. EMA alignment (2 points)
    ema_score, ema_details = _analyze_emas(df, target_direction)
    
    # 3. Market structure (2 points)
    structure_score, structure_details = _analyze_market_structure(df, target_direction)
    
    # Total score
    total_score = momentum_score + ema_score + structure_score
    
    # Determine overall direction
    if total_score >= 3:
        direction = target_direction
    elif total_score <= 1:
        direction = "BEARISH" if target_direction == "BUY" else "BUY"
    else:
        direction = "NEUTRAL"
    
    return {
        "direction": direction,
        "score": total_score,
        "details": ema_details + structure_details,
        "indicators": {
            "price_momentum": price_momentum,
            "momentum_score": momentum_score,
            "ema_score": ema_score,
            "structure_score": structure_score
        }
    }


def _analyze_emas(df: pd.DataFrame, target_direction: str) -> Tuple[int, List[str]]:
    """
    Analyze EMA alignment for directional bias.
    Returns score (0-2) and details.
    """
    if len(df) < 50:
        return 0, ["Insufficient data for EMA analysis"]
    
    # Calculate EMAs
    ema_9 = df["close"].ewm(span=9, adjust=False).mean().iloc[-1]
    ema_21 = df["close"].ewm(span=21, adjust=False).mean().iloc[-1]
    current_price = df["close"].iloc[-1]
    
    details = []
    score = 0
    
    # Check EMA alignment
    if target_direction == "BUY":
        # Bullish alignment: price > EMA9 > EMA21
        if current_price > ema_9 and ema_9 > ema_21:
            score += 2
            details.append(f"Strong bullish EMA alignment (Price>{ema_9:.5f}>{ema_21:.5f})")
        elif current_price > ema_9:
            score += 1
            details.append(f"Moderate bullish EMA alignment (Price>{ema_9:.5f})")
        else:
            details.append(f"Bearish EMA alignment (Price<{ema_9:.5f})")
    else:  # SELL
        # Bearish alignment: price < EMA9 < EMA21
        if current_price < ema_9 and ema_9 < ema_21:
            score += 2
            details.append(f"Strong bearish EMA alignment (Price<{ema_9:.5f}<{ema_21:.5f})")
        elif current_price < ema_9:
            score += 1
            details.append(f"Moderate bearish EMA alignment (Price<{ema_9:.5f})")
        else:
            details.append(f"Bullish EMA alignment (Price>{ema_9:.5f})")
    
    return score, details


def _analyze_market_structure(df: pd.DataFrame, target_direction: str) -> Tuple[int, List[str]]:
    """
    Analyze market structure (Higher Highs/Lower Lows).
    Returns score (0-2) and details.
    """
    if len(df) < 20:
        return 0, ["Insufficient data for structure analysis"]
    
    # Analyze last 10 candles for structure
    recent = df.tail(10).reset_index(drop=True)
    highs = recent["high"].values
    lows = recent["low"].values
    
    # Find swing highs and lows
    swing_highs = []
    swing_lows = []
    
    for i in range(1, len(recent) - 1):
        if highs[i] > highs[i-1] and highs[i] > highs[i+1]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i-1] and lows[i] < lows[i+1]:
            swing_lows.append(lows[i])
    
    details = []
    score = 0
    
    if target_direction == "BUY":
        # Bullish structure: Higher Highs and Higher Lows
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            if swing_highs[-1] > swing_highs[-2] and swing_lows[-1] > swing_lows[-2]:
                score += 2
                details.append("Strong bullish structure (HH + HL)")
            elif swing_lows[-1] > swing_lows[-2]:
                score += 1
                details.append("Moderate bullish structure (HL)")
            else:
                details.append("Bearish structure (LH + LL)")
        elif len(swing_lows) >= 2 and swing_lows[-1] > swing_lows[-2]:
            score += 1
            details.append("Moderate bullish structure (HL)")
        else:
            details.append("No clear structure")
    else:  # SELL
        # Bearish structure: Lower Highs and Lower Lows
        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            if swing_highs[-1] < swing_highs[-2] and swing_lows[-1] < swing_lows[-2]:
                score += 2
                details.append("Strong bearish structure (LH + LL)")
            elif swing_highs[-1] < swing_highs[-2]:
                score += 1
                details.append("Moderate bearish structure (LH)")
            else:
                details.append("Bullish structure (HH + HL)")
        elif len(swing_highs) >= 2 and swing_highs[-1] < swing_highs[-2]:
            score += 1
            details.append("Moderate bearish structure (LH)")
        else:
            details.append("No clear structure")
    
    return score, details


def get_multi_timeframe_confirmation(
    symbol: str,
    trade_direction: str,
    df_m5: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame,
    min_confidence: float = 70.0
) -> Tuple[bool, str, Dict]:
    """
    Get multi-timeframe confirmation for a trade.
    Returns (is_confirmed, reason, analysis_details)
    """
    analysis = analyze_multi_timeframe_alignment(
        symbol, df_m5, df_m15, df_h1, trade_direction
    )
    
    if not analysis["aligned"]:
        reason = f"Multi-timeframe misalignment: {', '.join(analysis['conflicts'])}" if analysis["conflicts"] else "No multi-timeframe alignment"
        return False, reason, analysis
    
    if analysis["confidence"] < min_confidence:
        reason = f"Low confidence: {analysis['confidence']:.1f}% < {min_confidence}%"
        return False, reason, analysis
    
    return True, analysis["summary"], analysis


# Quick check functions for integration with existing bot
def is_multi_timeframe_aligned(
    symbol: str,
    trade_direction: str,
    df_m5: pd.DataFrame,
    df_m15: pd.DataFrame,
    df_h1: pd.DataFrame
) -> bool:
    """Quick check for multi-timeframe alignment."""
    aligned, _, _ = get_multi_timeframe_confirmation(
        symbol, trade_direction, df_m5, df_m15, df_h1, min_confidence=60.0
    )
    return aligned


def format_multi_timeframe_for_prompt(analysis: Dict) -> str:
    """Format multi-timeframe analysis for Claude prompt."""
    if not analysis:
        return "Multi-timeframe: No data"
    
    tf_analysis = analysis.get("timeframe_alignment", {})
    
    lines = [
        f"Multi-timeframe Alignment: {analysis.get('recommendation', 'WAIT')}",
        f"Confidence: {analysis.get('confidence', 0):.1f}% | Score: {analysis.get('score', 0)}/15"
    ]
    
    for tf, data in tf_analysis.items():
        lines.append(f"  {tf}: {data.get('direction', 'NEUTRAL')} ({data.get('score', 0)}/5)")
        for detail in data.get("details", [])[:2]:  # Include top 2 details
            lines.append(f"    - {detail}")
    
    if analysis.get("conflicts"):
        lines.append("Conflicts:")
        for conflict in analysis["conflicts"][:3]:
            lines.append(f"  - {conflict}")
    
    return "\n".join(lines)