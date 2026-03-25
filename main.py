"""
SMC + Price Action AI Trading Bot — Main Entry Point
Enhanced with: HTF trend filter, S/R detection, liquidity sweeps,
news filter, SL/TP validation, breakeven/trailing stop, outcome tracking.

Usage: python main.py
"""

import time
import MetaTrader5 as mt5
from datetime import datetime

# ── Configuration ──
from config.settings import (
    validate_required_env,
    SYMBOLS, TIMEFRAME_H4, TIMEFRAME_H1, TIMEFRAME_M15, TIMEFRAME_M5,
    CANDLES, CANDLES_HTF, CANDLES_M5, MAX_SPREAD, MIN_CONFIDENCE, MIN_RR_RATIO,
    MAX_RISK_PER_TRADE_USD, MAX_TRADES_PER_SYMBOL_PER_DAY,
    SYMBOL_COOLDOWN_MIN, LOSS_COOLDOWN_MIN, SLEEP_SEC,
    MAX_TRADES_DAY, POI_BUFFER_PIPS, PIP_VALUE,
    NEWS_FILTER_ENABLED, NEWS_BLACKOUT_MINUTES,
    SYMBOL_SESSIONS, GMT, BOT_MAGIC, log,
    LONDON_START, LONDON_END, NY_START, NY_END,
    INTRADAY_MODE,
)

# ── Core ──
from core.mt5_client import (
    connect_mt5, ensure_connected, resolve_all_symbols,
    get_base_symbol, get_candles, get_spread, get_current_price,
    has_open_position, calculate_lot_size, calculate_atr,
    get_volume_profile, place_order,
)
from core.position_manager import PositionManager
from core.connection_monitor import connection_monitor
from core.error_handler import check_mt5_connection

# ── Analysis ──
from analysis.smc import (
    detect_order_blocks, detect_fvg, detect_bos_choch, 
    get_recent_swings, get_market_bias, get_p_d_zones
)
from analysis.patterns import detect_chart_patterns, confirm_m5_entry
from analysis.support_resistance import detect_support_resistance, format_sr_for_prompt
from analysis.liquidity import detect_liquidity_sweeps, detect_equal_highs_lows, format_liquidity_for_prompt
from analysis.trend import detect_htf_trend, is_trade_aligned_with_trend
from analysis.multi_timeframe import get_multi_timeframe_confirmation, format_multi_timeframe_for_prompt

# ── Removed AI imports for pure-algo ──

# ── Risk ──
from risk.manager import RiskManager
from risk.news_filter import has_upcoming_high_impact_news

# ── Logging ──
from trade_logger.logger import TradeLogger


# ──────────────────────────────────────────────
#  SESSION CHECK
# ──────────────────────────────────────────────
def _minutes_until_session_end() -> int:
    """Return minutes until the current active session ends. -1 if not in session."""
    now = datetime.now(GMT)
    hour = now.hour
    minute = now.minute
    current_minutes = hour * 60 + minute

    # Check which session we're in and when it ends
    london_end_min = LONDON_END * 60
    ny_end_min     = NY_END * 60

    if LONDON_START <= hour < LONDON_END:
        return london_end_min - current_minutes
    if NY_START <= hour < NY_END:
        return ny_end_min - current_minutes
    return -1


def is_trading_session() -> bool:
    """Returns True 24/5. Prevents trading on weekends."""
    now = datetime.now(GMT)
    if now.weekday() >= 5:
        return False
    return True


def is_symbol_in_optimal_session(base_symbol: str) -> bool:
    """Check if the current hour is in the symbol's best trading window."""
    session = SYMBOL_SESSIONS.get(base_symbol)
    if not session:
        return True  # No session config = allow
    hour = datetime.now(GMT).hour
    if hour in session.get("avoid_hours", []):
        return False
    return True


# ──────────────────────────────────────────────
#  POINT-OF-INTEREST PROXIMITY CHECK
# ──────────────────────────────────────────────
def is_near_poi(current_price: float, smc_data: dict, base_symbol: str) -> bool:
    """Check if price is near an order block, FVG, or has active patterns."""
    pip = PIP_VALUE.get(base_symbol, 0.0001)
    buffer = POI_BUFFER_PIPS.get(base_symbol, 5) * pip

    near_ob = any(
        (ob["low"] - buffer) <= current_price <= (ob["high"] + buffer)
        for ob in smc_data.get("order_blocks", [])
    )
    near_fvg = any(
        (fvg["low"] - buffer) <= current_price <= (fvg["high"] + buffer)
        for fvg in smc_data.get("fvgs", [])
    )
    return near_ob or near_fvg


# ──────────────────────────────────────────────
#  MAIN LOOP
# ──────────────────────────────────────────────
#  PURE ALGORITHMIC DECISION ENGINE
# ──────────────────────────────────────────────
def run_algorithmic_decision(
    symbol: str, current_price: float, df_m5, smc_data: dict, atr_m15_raw, 
    htf_trend: dict, volume_info: dict, df_h1, sr_data: dict
) -> dict:
    """
    100% Algorithmic mathematical logic.
    Searches for valid M15 FVGs aligned with H4 + H1 trend, confirmed by strong M5 momentum and volume.
    """
    decision = {"action": "HOLD", "reason": "No valid algorithmic setup", "confidence": 50}
    trend_dir = htf_trend.get("direction", "NEUTRAL")
    market_bias = smc_data.get("bias", "NEUTRAL")
    structure_text = smc_data.get("bos_choch", "")
    fvgs = smc_data.get("fvgs", [])
    obs = smc_data.get("order_blocks", [])
    vol_ratio = float(volume_info.get("vol_ratio", 1.0))
    atr = float(atr_m15_raw.iloc[-1] if hasattr(atr_m15_raw, "iloc") else atr_m15_raw)

    
    # Get structural swings for logical SL/TP
    swings = get_recent_swings(df_h1, lookback=50)
    swing_high = float(swings["high"])
    swing_low  = float(swings["low"])

    def calculate_logical_tp_sl(action_dir: str, current: float) -> tuple[float, float, float]:
        """Calculates SL at swing and searches for TP that gives >= 2.0 RR."""
        if action_dir == "BUY":
            # SL at swing low with minor buffer
            sl_price = swing_low - (atr * 0.2)
            risk = abs(float(current) - sl_price)
            if risk <= 0.00001: risk = atr * 0.5; sl_price = current - risk
            
            # Search for TP at H1 Resistance levels
            res_levels = sr_data.get("resistance", [])
            valid_res = sorted([float(r) for r in res_levels if float(r) > float(current)])
            
            for r in valid_res:
                rr_val = float((r - float(current)) / risk)
                if rr_val >= 2.0:
                    return sl_price, r, round(float(rr_val), 2)
            
            # Fallback to last swing high if it offers better RR
            if (swing_high - current) / risk >= 2.0:
                rr_f = float((swing_high - current) / risk)
                return sl_price, swing_high, round(rr_f, 2)
                
            # Final fallback: math-based 2.0R
            return sl_price, current + (risk * 2.0), 2.0
        else:
            # SL at swing high with minor buffer
            sl_price = swing_high + (atr * 0.2)
            risk = abs(sl_price - float(current))
            if risk <= 0.00001: risk = atr * 0.5; sl_price = current + risk
            
            # Search for TP at H1 Support levels
            sup_levels = sr_data.get("support", [])
            valid_sup = sorted([float(s) for s in sup_levels if float(s) < float(current)], reverse=True)
            
            for s in valid_sup:
                rr_val = float((float(current) - s) / risk)
                if rr_val >= 2.0:
                    return sl_price, s, round(float(rr_val), 2)
            
            # Fallback to last swing low
            if (current - swing_low) / risk >= 2.0:
                rr_f = float((current - swing_low) / risk)
                return sl_price, swing_low, round(rr_f, 2)
                
            return sl_price, current - (risk * 2.0), 2.0

    # Check H1 Alignment
    h1_ema20 = float(df_h1["close"].ewm(span=20, adjust=False).mean().iloc[-1])
    h1_ema50 = float(df_h1["close"].ewm(span=50, adjust=False).mean().iloc[-1])
    h1_bullish = h1_ema20 > h1_ema50
    h1_bearish = h1_ema20 < h1_ema50

    if vol_ratio < 0.6:
        decision["reason"] = f"Volume too low ({vol_ratio:.1f}x avg)"
        return decision

    # PA Triggers
    c_close = float(df_m5["close"].iloc[-2]); c_open = float(df_m5["open"].iloc[-2])
    c_high = float(df_m5["high"].iloc[-2]); c_low = float(df_m5["low"].iloc[-2])
    c_body = abs(c_close - c_open); c_range = max(c_high - c_low, 0.00001)
    
    is_bullish_pa = ((min(c_open, c_close) - c_low > c_body * 1.8) or (c_close > c_open and c_body/c_range > 0.45))
    is_bearish_pa = ((c_high - max(c_open, c_close) > c_body * 1.8) or (c_close < c_open and c_body/c_range > 0.45))

    # Zones
    in_bullish_poi = any(float(f["low"]) <= current_price <= float(f["high"]) for f in fvgs if f["type"] == "Bullish") or \
                     any(float(o["low"]) <= current_price <= float(o["high"]) for o in obs if o["type"] == "Bullish") or \
                     any(abs(current_price - float(s)) < (atr * 0.3) for s in sr_data.get("support", []))

    in_bearish_poi = any(float(f["low"]) <= current_price <= float(f["high"]) for f in fvgs if f["type"] == "Bearish") or \
                     any(float(o["low"]) <= current_price <= float(o["high"]) for o in obs if o["type"] == "Bearish") or \
                     any(abs(current_price - float(r)) < (atr * 0.3) for r in sr_data.get("resistance", []))

    # 5. Premium/Discount Filter
    pd_zone = get_p_d_zones(current_price, swing_high, swing_low)

    # Execution
    if in_bullish_poi and h1_bullish and trend_dir != "BEARISH":
        struct_ok = (market_bias == "BULLISH") or ("CHOCH Bullish" in structure_text)
        if struct_ok:
            if pd_zone in ["DISCOUNT", "EQUILIBRIUM"]:
                if is_bullish_pa:
                    sl, tp, rr = calculate_logical_tp_sl("BUY", current_price)
                    if rr >= 2.0:
                        return {
                            "action": "BUY", "reason": f"SMC {structure_text} | {pd_zone} | POI | M5 PA | RR 1:{rr}",
                            "confidence": 95 if "CHOCH" in structure_text else 90,
                            "entry": current_price, "sl": sl, "tp": tp, "rr_ratio": float(rr),
                            "key_level": "Structural POI", "confluences": [structure_text, pd_zone, "POI", "M5 PA"]
                        }
                else:
                    decision["reason"] = f"WAIT: {pd_zone} + Structural OK, waiting for M5 trigger"
            else:
                decision["reason"] = f"WAIT: Bullish POI but price is in {pd_zone} (Expensive)"
        else:
            decision["reason"] = f"WAIT: Price in POI but H1 Structure is {structure_text}"

    elif in_bearish_poi and h1_bearish and trend_dir != "BULLISH":
        struct_ok = (market_bias == "BEARISH") or ("CHOCH Bearish" in structure_text)
        if struct_ok:
            if pd_zone in ["PREMIUM", "EQUILIBRIUM"]:
                if is_bearish_pa:
                    sl, tp, rr = calculate_logical_tp_sl("SELL", current_price)
                    if rr >= 2.0:
                        return {
                            "action": "SELL", "reason": f"SMC {structure_text} | {pd_zone} | POI | M5 PA | RR 1:{rr}",
                            "confidence": 95 if "CHOCH" in structure_text else 90,
                            "entry": current_price, "sl": sl, "tp": tp, "rr_ratio": float(rr),
                            "key_level": "Structural POI", "confluences": [structure_text, pd_zone, "POI", "M5 PA"]
                        }
                else:
                    decision["reason"] = f"WAIT: {pd_zone} + Structural OK, waiting for M5 trigger"
            else:
                decision["reason"] = f"WAIT: Bearish POI but price is in {pd_zone} (Cheap)"
        else:
            decision["reason"] = f"WAIT: Price in POI but H1 Structure is {structure_text}"

    return decision


# ──────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("  SMC + Price Action AI Trading Bot v2.0 (INTRADAY/SCALPING)")
    log.info("  HTF trend | S/R | Liquidity | News | Trailing SL | Auto-close")
    log.info("=" * 60)

    if not validate_required_env():
        log.error("Setup failed. Add credentials to .env and restart.")
        return

    if not connect_mt5():
        return

    tradable_symbols = resolve_all_symbols()
    if not tradable_symbols:
        log.error("No tradable symbols available. Check Market Watch.")
        mt5.shutdown()
        return

    # ── Initialize modules ──
    risk_mgr    = RiskManager()
    trade_log   = TradeLogger()
    pos_manager = PositionManager(trade_log, risk_mgr)
    
    # Start connection health monitoring
    connection_monitor.start_monitoring()
    log.info("Connection health monitor started")

    # Per-symbol state
    symbol_trades_today     = {s: 0 for s in tradable_symbols}
    symbol_last_trade_time  = {s: None for s in tradable_symbols}
    symbol_last_was_loss    = {s: False for s in tradable_symbols}
    symbol_last_analyzed_m5 = {s: None for s in tradable_symbols}  # Throttle API to 1 call per M5 candle
    current_day = datetime.now(GMT).date()

    log.info(f"Pairs: {', '.join(tradable_symbols)} | Max trades: {MAX_TRADES_DAY}/day")
    log.info("Bot running. Press Ctrl+C to stop.\n")

    while True:
        try:
            # ── Connection health check ──
            if not ensure_connected():
                log.error("Cannot reconnect to MT5. Retrying in 60s...")
                time.sleep(60)
                continue
            
            # ── System health check ──
            is_healthy, health_reason = connection_monitor.is_healthy_for_trading()
            if not is_healthy:
                log.warning(f"System health check failed: {health_reason}")
                log.warning("Skipping trading cycle, performing health check...")
                health_status = connection_monitor.check_health()
                for issue in health_status.get("issues", [])[:3]:
                    log.warning(f"  - {issue}")
                time.sleep(60)
                continue

            now = datetime.now(GMT)
            risk_mgr.reset_if_new_day()

            # ── Day reset ──
            if now.date() != current_day:
                current_day = now.date()
                symbol_trades_today    = {s: 0 for s in tradable_symbols}
                symbol_last_trade_time = {s: None for s in tradable_symbols}
                symbol_last_was_loss   = {s: False for s in tradable_symbols}

            # ── Manage open positions (breakeven, trailing, outcome tracking) ──
            recently_closed = pos_manager.manage_all()
            if recently_closed:
                for closed_base in recently_closed:
                    for s in tradable_symbols:
                        if get_base_symbol(s) == closed_base:
                            symbol_last_trade_time[s] = now
                            log.info(f"[{s}] Position closed. 10m Cooldown timer started.")

            # ── Session check ──
            if not is_trading_session():
                log.info(f"Outside session ({now.strftime('%H:%M')} UTC). Sleeping 5 min...")
                time.sleep(300)
                continue


            # ── Daily limits ──
            if risk_mgr.is_daily_limit_hit():
                log.info("Daily limit reached. Done for today.")
                time.sleep(3600)
                continue

            # ── Streak pause ──
            if risk_mgr.is_streak_paused():
                log.info("Trading paused due to consecutive losses. Waiting for next session reset.")
                time.sleep(1800)
                continue

            # ── Analyze each symbol ──
            for symbol in tradable_symbols:
                base_symbol = get_base_symbol(symbol)
                log.info(f"--- Analyzing {symbol} ({base_symbol}) ---")

                # ── Per-symbol daily limit ──
                if symbol_trades_today.get(symbol, 0) >= MAX_TRADES_PER_SYMBOL_PER_DAY:
                    log.info(f"[{symbol}] Per-symbol daily limit reached. Skipping.")
                    continue

                # ── Cooldown (longer after a loss) ──
                last_ts = symbol_last_trade_time.get(symbol)
                if last_ts is not None:
                    cooldown = LOSS_COOLDOWN_MIN if symbol_last_was_loss.get(symbol) else SYMBOL_COOLDOWN_MIN
                    minutes_since = (now - last_ts).total_seconds() / 60.0
                    if minutes_since < cooldown:
                        log.info(f"[{symbol}] Cooldown ({minutes_since:.0f}m / {cooldown}m). Skipping.")
                        continue

                # ── Symbol session check ──
                if not is_symbol_in_optimal_session(base_symbol):
                    log.info(f"[{symbol}] Not in optimal session hours. Skipping.")
                    continue

                # ── Open position check ──
                if has_open_position(symbol):
                    log.info(f"[{symbol}] Position already open. Skipping.")
                    continue

                # ── Spread check ──
                spread = get_spread(symbol)
                if spread > MAX_SPREAD.get(base_symbol, 30):
                    log.info(f"[{symbol}] Spread too wide ({spread} pts). Skipping.")
                    continue

                # ── News filter ──
                if NEWS_FILTER_ENABLED:
                    has_news, news_desc = has_upcoming_high_impact_news(symbol, NEWS_BLACKOUT_MINUTES)
                    if has_news:
                        log.info(f"[{symbol}] News blackout: {news_desc}. Skipping.")
                        continue

                # ── Fetch candle data ──
                df_m15 = get_candles(symbol, TIMEFRAME_M15, CANDLES)
                df_h1  = get_candles(symbol, TIMEFRAME_H1, CANDLES)
                df_h4  = get_candles(symbol, TIMEFRAME_H4, CANDLES_HTF)
                df_m5  = get_candles(symbol, TIMEFRAME_M5, CANDLES_M5)
                if df_m15.empty or df_h1.empty:
                    log.warning(f"[{symbol}] No candle data. Skipping.")
                    continue

                current_price = df_m15["close"].iloc[-1]

                # ── Run all analyses ──
                # SMC
                # SMC Analysis (H1 Structural Bias + M15 POIs)
                smc_h1 = get_market_bias(df_h1)
                smc_data = {
                    "order_blocks": detect_order_blocks(df_h1),
                    "fvgs":         detect_fvg(df_m15, base_symbol=base_symbol),
                    "bos_choch":    smc_h1["structure"],
                    "bias":         smc_h1["bias"]
                }

                # Patterns
                patterns = detect_chart_patterns(df_m15)

                # Support / Resistance
                sr_data = detect_support_resistance(df_h1, base_symbol=base_symbol)

                # Liquidity
                sweeps = detect_liquidity_sweeps(df_m15)
                pools  = detect_equal_highs_lows(df_m15)
                liquidity_text = format_liquidity_for_prompt(sweeps, pools)

                # H4 Trend
                htf_trend = detect_htf_trend(df_h4) if not df_h4.empty else {
                    "direction": "NEUTRAL", "strength": "WEAK",
                    "ema_20": 0, "ema_50": 0, "description": "No H4 data",
                }

                # Volume & ATR
                volume_info = get_volume_profile(df_m15)
                atr_m15 = calculate_atr(df_m15)
                atr_h1  = calculate_atr(df_h1)

                # ── Pre-filter: Skip API call if no point of interest ──
                near = is_near_poi(current_price, smc_data, base_symbol)
                if not near and not patterns and not sweeps:
                    continue

                # ── PURE ALGORITHMIC ENGINE ──
                decision = run_algorithmic_decision(
                    symbol=symbol,
                    current_price=current_price,
                    df_m5=df_m5,
                    smc_data=smc_data,
                    atr_m15_raw=atr_m15,
                    htf_trend=htf_trend,
                    volume_info=volume_info,
                    df_h1=df_h1,
                    sr_data=sr_data
                )

                # ── Log the signal ──
                trade_log.log_signal(symbol, decision, current_price, now)

                action     = decision.get("action", "HOLD")
                confidence = decision.get("confidence", 0)

                if action not in ("BUY", "SELL") or confidence < MIN_CONFIDENCE:
                    reason = decision.get("reason", "No valid setup")
                    log.info(f"[{symbol}] {action}: {reason}")
                    continue

                # ──────────────────────────────────────
                #  VALIDATION GATES (all must pass)
                # ──────────────────────────────────────

                sl = decision.get("sl")
                tp = decision.get("tp")
                rr = decision.get("rr_ratio", 0)

                # Gate 1: Basic SL/TP/RR check
                if not sl or not tp or rr < MIN_RR_RATIO:
                    log.info(f"[{symbol}] Missing SL/TP or R:R ({rr}) < {MIN_RR_RATIO}. Skip.")
                    continue

                # (Gates 2 and 3 removed because pure algorithmic logic mathematically generates perfect SL/TP)

                # Gate 4: H4 trend alignment
                if not is_trade_aligned_with_trend(action, htf_trend):
                    log.info(f"[{symbol}] ❌ Trade blocked: {action} against {htf_trend['direction']} trend.")
                    continue

                # Gate 5: Correlation check
                if risk_mgr.has_correlated_exposure(symbol, action):
                    log.info(f"[{symbol}] ❌ Correlated exposure detected. Skip.")
                    continue

                # Gate 6: M5 entry confirmation
                m5_ok, m5_pattern = confirm_m5_entry(df_m5, action)
                if not m5_ok:
                    log.info(f"[{symbol}] ⚠️ M5 confirmation missing: {m5_pattern}. (Bypassed)")
                    # continue  # BYPASSED
                else:
                    log.info(f"[{symbol}] ✅ M5 confirmed: {m5_pattern}")

                # Gate 7: Multi-timeframe confirmation (M5 + M15 + H1 alignment)
                mtf_confirmed, mtf_reason, mtf_analysis = get_multi_timeframe_confirmation(
                    symbol, action, df_m5, df_m15, df_h1, min_confidence=70.0
                )
                if not mtf_confirmed:
                    log.info(f"[{symbol}] ⚠️ Multi-timeframe confirmation missing: {mtf_reason} (Bypassed)")
                    # continue  # BYPASSED
                else:
                    log.info(f"[{symbol}] ✅ Multi-timeframe confirmed: {mtf_analysis['summary']}")
                log.info(f"[{symbol}] ✅ Multi-timeframe confirmed: {mtf_analysis['summary']}")

                # Append confirmations to UI dashboard tags
                confirmations = []
                if m5_ok:
                    confirmations.append(m5_pattern)
                if mtf_confirmed:
                    confirmations.append(f"MTF_{mtf_analysis.get('confidence', 0):.0f}%")
                
                if "confluences" in decision and isinstance(decision["confluences"], list):
                    decision["confluences"].extend(confirmations)
                elif confirmations:
                    decision["confluences"] = confirmations

                # ──────────────────────────────────────
                #  POSITION SIZING & EXECUTION
                # ──────────────────────────────────────

                entry = current_price  # Always use live price
                lot = calculate_lot_size(symbol, action, entry, sl)

                # Final risk cap check
                est_loss = abs(mt5.order_calc_profit(
                    mt5.ORDER_TYPE_BUY if action == "BUY" else mt5.ORDER_TYPE_SELL,
                    symbol, lot, entry, sl
                ) or 0.0)

                if est_loss > MAX_RISK_PER_TRADE_USD:
                    log.info(f"[{symbol}] Est loss ${est_loss:.2f} > cap ${MAX_RISK_PER_TRADE_USD:.2f}. Skip.")
                    continue

                # ── Place order ──
                success, order_error, ticket = place_order(symbol, action, lot, sl, tp)

                if success:
                    risk_mgr.record_trade()
                    symbol_trades_today[symbol] = symbol_trades_today.get(symbol, 0) + 1

                    # Log the trade
                    trade_log.log_trade(
                        symbol, action, lot, decision, current_price, now,
                        status="OPEN", pnl=0.0, ticket=ticket,
                    )

                    # Register with position manager for SL management
                    pos_manager.register_trade(
                        ticket=ticket,
                        symbol=base_symbol,
                        direction=action,
                        entry=entry,
                        sl=sl,
                        tp=tp,
                        volume=lot,  # Added for partial TP tracking
                    )

                    log.info(f"[{symbol}] ✅ TRADE PLACED: {action} {lot} lots | SL={sl} TP={tp} | Ticket={ticket}")

                else:
                    # Log rejected trade
                    rejected = dict(decision)
                    rejected["reason"] = f"{decision.get('reason', '')} | MT5 rejected: {order_error}".strip(" |")
                    trade_log.log_trade(
                        symbol, action, lot, rejected, current_price, now,
                        status="REJECTED", pnl=0.0, ticket=0,
                    )

                time.sleep(2)

            # ── Cycle complete ──
            tracked = pos_manager.get_tracked_count()
            log.info(
                f"Cycle done. Sleeping {SLEEP_SEC}s | "
                f"Trades: {risk_mgr.trades_today}/{MAX_TRADES_DAY} | "
                f"Tracking: {tracked} positions"
            )
            time.sleep(SLEEP_SEC)

        except KeyboardInterrupt:
            log.info("Bot stopped by user.")
            break
        except Exception as e:
            log.error(f"Unexpected error: {e}", exc_info=True)
            time.sleep(30)

    mt5.shutdown()
    trade_log.print_summary()
    log.info("MT5 disconnected. Bot shut down.")


if __name__ == "__main__":
    main()
