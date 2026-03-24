"""
Claude AI Analyst — Trade Decision Engine
Builds a comprehensive prompt, calls Claude, validates the response,
and ensures SL/TP/Entry are sane before returning a decision.
"""

import re
import json
import anthropic
from config.settings import (
    ANTHROPIC_API_KEY, MIN_RR_RATIO, MAX_ENTRY_DEVIATION, log,
)


# ──────────────────────────────────────────────
#  RESPONSE PARSING
# ──────────────────────────────────────────────
def _parse_claude_json(raw: str) -> dict:
    """
    Robustly extract JSON from Claude's response.
    Handles: raw JSON, markdown-wrapped JSON, JSON embedded in text.
    """
    raw = raw.strip()

    # Try 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Try 2: extract from markdown code block
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(1))
        except json.JSONDecodeError:
            pass

    # Try 3: find first {...} object in text
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", raw, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    log.warning(f"Could not parse Claude JSON: {raw[:200]}")
    return {"action": "HOLD", "reason": "Failed to parse AI response", "confidence": 0}


# ──────────────────────────────────────────────
#  TRADE VALIDATION
# ──────────────────────────────────────────────
def validate_trade_decision(
    decision: dict,
    symbol: str,
    current_price: float,
) -> tuple[bool, str]:
    """
    Validate Claude's decision against reality.
    Checks:
      - SL is on the correct side of entry
      - TP is on the correct side of entry
      - Entry is close to current market price
      - Actual R:R >= minimum threshold
    Returns (is_valid, rejection_reason).
    """
    action = decision.get("action", "HOLD")
    if action == "HOLD":
        return True, ""

    entry = decision.get("entry", 0)
    sl    = decision.get("sl", 0)
    tp    = decision.get("tp", 0)

    if not entry or not sl or not tp:
        return False, "Missing entry, SL, or TP value"

    # ── Entry must be near current price ──
    if current_price > 0 and abs(entry - current_price) / current_price > MAX_ENTRY_DEVIATION:
        return False, (
            f"Entry {entry:.5f} too far from current price {current_price:.5f} "
            f"(>{MAX_ENTRY_DEVIATION*100:.1f}% deviation)"
        )

    # ── SL must be on correct side ──
    if action == "BUY":
        if sl >= entry:
            return False, f"BUY but SL ({sl:.5f}) >= Entry ({entry:.5f}) — SL on wrong side"
        if tp <= entry:
            return False, f"BUY but TP ({tp:.5f}) <= Entry ({entry:.5f}) — TP on wrong side"
    elif action == "SELL":
        if sl <= entry:
            return False, f"SELL but SL ({sl:.5f}) <= Entry ({entry:.5f}) — SL on wrong side"
        if tp >= entry:
            return False, f"SELL but TP ({tp:.5f}) >= Entry ({entry:.5f}) — TP on wrong side"

    # ── Verify actual R:R ratio ──
    risk   = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        return False, "Risk distance is zero"

    actual_rr = reward / risk
    if actual_rr < MIN_RR_RATIO * 0.95:  # 5% tolerance for floating point
        return False, f"Actual R:R {actual_rr:.2f} < minimum {MIN_RR_RATIO}"

    return True, ""


def validate_sl_tp_with_atr(
    decision: dict,
    atr: float,
    base_symbol: str,
) -> tuple[bool, str]:
    """
    Validate SL/TP distances against ATR to prevent unreasonable levels.
    SL should be within 0.5–3.0x ATR.
    TP should be within 1.0–6.0x ATR.
    """
    if atr <= 0:
        return True, ""  # Can't validate without ATR

    entry = decision.get("entry", 0)
    sl    = decision.get("sl", 0)
    tp    = decision.get("tp", 0)
    if not all([entry, sl, tp]):
        return True, ""

    sl_dist = abs(entry - sl)
    tp_dist = abs(tp - entry)

    sl_atr_ratio = sl_dist / atr
    tp_atr_ratio = tp_dist / atr

    if sl_atr_ratio > 3.0:
        return False, f"SL too wide: {sl_atr_ratio:.1f}x ATR (max 3.0x)"
    if sl_atr_ratio < 0.3:
        return False, f"SL too tight: {sl_atr_ratio:.1f}x ATR (min 0.3x)"
    if tp_atr_ratio > 8.0:
        return False, f"TP unrealistically far: {tp_atr_ratio:.1f}x ATR (max 8.0x)"

    return True, ""


# ──────────────────────────────────────────────
#  PROMPT BUILDER
# ──────────────────────────────────────────────
def _build_prompt(
    symbol: str,
    current_price: float,
    df_m5_recent: str,
    df_h1_recent: str,
    smc_data: dict,
    patterns: list,
    sr_data: dict,
    liquidity_text: str,
    htf_trend: dict,
    volume_info: dict,
    atr_m15: float,
    atr_h1: float,
    recent_performance: dict | None = None,
) -> str:
    """Build the comprehensive analysis prompt for Claude."""

    obs  = smc_data.get("order_blocks", [])
    fvgs = smc_data.get("fvgs", [])
    bos  = smc_data.get("bos_choch", "Unknown")

    # Format decimals based on symbol
    d = 2 if "XAU" in symbol.upper() or "GOLD" in symbol.upper() else 5

    ob_text = "\n".join(
        [f"  - {o['type']} OB: {o['low']:.{d}f} – {o['high']:.{d}f} (strength: {o['strength']})"
         for o in obs[:5]]
    ) or "  None detected"

    fvg_text = "\n".join(
        [f"  - {f['type']} FVG: {f['low']:.{d}f} – {f['high']:.{d}f} (size: {f.get('size', 0):.{d}f})"
         for f in fvgs[:5]]
    ) or "  None detected"

    pat_text = ", ".join(patterns) if patterns else "None detected"

    # S/R levels
    sr_lines = []
    for r in sr_data.get("resistance", [])[:3]:
        sr_lines.append(f"  - Resistance: {r['price']:.{d}f} ({r['touches']} touches)")
    for s in sr_data.get("support", [])[:3]:
        sr_lines.append(f"  - Support: {s['price']:.{d}f} ({s['touches']} touches)")
    sr_text = "\n".join(sr_lines) if sr_lines else "  None detected"

    # Volume info
    vol_ratio = volume_info.get("ratio", 0)
    vol_trend = "Rising" if volume_info.get("rising") else "Falling"
    vol_vs_avg = "Above" if volume_info.get("above_avg") else "Below"

    # Performance context
    perf_text = ""
    if recent_performance and recent_performance.get("total", 0) > 0:
        w = recent_performance.get("wins", 0)
        l = recent_performance.get("losses", 0)
        wr = recent_performance.get("win_rate", 0)
        perf_text = f"\n=== YOUR RECENT TRACK RECORD ON {symbol} ===\n"
        perf_text += f"Last {recent_performance['total']} trades: {w}W / {l}L ({wr:.0f}% win rate)\n"
        if wr < 50:
            perf_text += "⚠ Win rate is low — be MORE conservative and only trade A+ setups.\n"

    prompt = f"""You are an expert Smart Money Concepts (SMC) and Price Action intraday trader.
Analyze {symbol} and decide whether to take a trade.

CURRENT PRICE: {current_price:.{d}f}
TIMEFRAME: M15 (POIs/Liquidity) + M5 (Execution/Candles) + H1/H4 (Trend)

=== H4 TREND DIRECTION ===
{htf_trend.get('description', 'Unknown')}
Trend: {htf_trend.get('direction', 'NEUTRAL')} ({htf_trend.get('strength', 'UNKNOWN')})

=== M15/H1 MARKET STRUCTURE ===
{bos}

=== ORDER BLOCKS (H1 — nearest active) ===
{ob_text}

=== FAIR VALUE GAPS (M15 — open, min-size filtered) ===
{fvg_text}

=== SUPPORT & RESISTANCE LEVELS ===
{sr_text}

=== LIQUIDITY ANALYSIS (M15) ===
{liquidity_text}

=== CHART PATTERNS (M15) ===
{pat_text}

=== VOLUME ANALYSIS ===
Current volume: {vol_ratio}x average ({vol_vs_avg} avg, {vol_trend})

=== ATR ANALYSIS ===
M15 ATR(14): {atr_m15:.{d}f}
H1 ATR(14): {atr_h1:.{d}f}
Suggested SL range: {atr_m15 * 1.0:.{d}f} to {atr_m15 * 2.0:.{d}f}
Suggested TP range: {atr_m15 * 2.0:.{d}f} to {atr_m15 * 4.0:.{d}f}

=== RECENT 5M CANDLES (last 20) ===
{df_m5_recent}

=== RECENT H1 CANDLES (last 10) ===
{df_h1_recent}
{perf_text}
=== TRADE RULES (STRICT — VIOLATING ANY RULE MEANS action=HOLD) ===
1.  Only trade if price is AT or INSIDE an Order Block, FVG, or key S/R zone
2.  Trade direction MUST ALIGN with H4 trend (no counter-trend entries against STRONG trend)
3.  Require at least 2 confluences: (OB/FVG zone + BOS confirmation + candlestick pattern OR S/R alignment)
4.  If a liquidity sweep just occurred, prefer trading in the sweep direction (e.g., bullish sweep → look for BUY)
5.  Stop Loss MUST be BELOW entry for BUY, ABOVE entry for SELL — placed beyond OB/FVG invalidation
6.  SL distance must be 1.0-2.0x the M15 ATR — not too tight, not too wide
7.  Take Profit targets next FVG, S/R level, or structure high/low
8.  Minimum Risk:Reward = 1:2
9.  If volume is below average and no strong pattern → HOLD
10. Entry price MUST equal the CURRENT PRICE (not a hypothetical future price)
11. If ANY rule fails → action MUST be HOLD
12. Confidence below 75 → action MUST be HOLD

Respond ONLY with valid JSON, no markdown, no extra text:
{{
  "action": "BUY" | "SELL" | "HOLD",
  "reason": "SETUP: [zone type + price] | TRIGGER: [what confirmed entry] | TARGET: [TP target + why]",
  "confidence": 0-100,
  "entry": {current_price:.{d}f},
  "sl": float,
  "tp": float,
  "rr_ratio": float,
  "key_level": "zone type + exact price, e.g. Bullish OB 1.08200-1.08350",
  "confluences": ["H4 Bullish", "BOS confirmed", "Bullish OB at 1.0820", "Hammer pattern"]
}}

CRITICAL FORMATTING RULES FOR "reason":
- Use EXACTLY this format: SETUP: ... | TRIGGER: ... | TARGET: ...
- SETUP = the zone/level (e.g. "Price in Bullish OB at 1.0820")
- TRIGGER = what confirmed entry (e.g. "BOS up on H1 + Bullish Engulfing")
- TARGET = TP rationale (e.g. "Next resistance at 1.0890")
- Max 1 sentence per section. NO generic text like "market looks bullish"
- For HOLD: reason should state the SPECIFIC missing condition (e.g. "No OB/FVG nearby, price in no-man's land")"""

    return prompt


# ──────────────────────────────────────────────
#  MAIN ANALYSIS FUNCTION
# ──────────────────────────────────────────────
def analyse_with_claude(
    symbol: str,
    current_price: float,
    df_m5: "pd.DataFrame",
    df_h1: "pd.DataFrame",
    smc_data: dict,
    patterns: list,
    sr_data: dict,
    liquidity_text: str,
    htf_trend: dict,
    volume_info: dict,
    atr_m5: float,
    atr_h1: float,
    recent_performance: dict | None = None,
) -> dict:
    """
    Send analysis to Claude and return a validated trade decision.
    The returned dict always has at least: action, reason, confidence.
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    m5_recent = df_m5.tail(20).to_string(index=False)
    h1_recent = df_h1.tail(10).to_string(index=False)

    prompt = _build_prompt(
        symbol=symbol,
        current_price=current_price,
        df_m5_recent=m5_recent,
        df_h1_recent=h1_recent,
        smc_data=smc_data,
        patterns=patterns,
        sr_data=sr_data,
        liquidity_text=liquidity_text,
        htf_trend=htf_trend,
        volume_info=volume_info,
        atr_m15=atr_m15,
        atr_h1=atr_h1,
        recent_performance=recent_performance,
    )

    try:
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = message.content[0].text.strip()
        decision = _parse_claude_json(raw)

        # Force entry to current price (Claude sometimes hallucinates old prices)
        if decision.get("action") in ("BUY", "SELL"):
            decision["entry"] = current_price

        log.info(
            f"[{symbol}] Claude: {decision['action']} | "
            f"Conf: {decision.get('confidence', 0)}% | "
            f"{decision.get('reason', '')}"
        )
        return decision

    except Exception as e:
        log.error(f"Claude API error [{symbol}]: {e}")
        return {"action": "HOLD", "reason": f"API error: {e}", "confidence": 0}
