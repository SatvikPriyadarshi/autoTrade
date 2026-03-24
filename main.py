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
from analysis.smc import detect_order_blocks, detect_fvg, detect_bos_choch
from analysis.patterns import detect_chart_patterns, confirm_m5_entry
from analysis.support_resistance import detect_support_resistance, format_sr_for_prompt
from analysis.liquidity import detect_liquidity_sweeps, detect_equal_highs_lows, format_liquidity_for_prompt
from analysis.trend import detect_htf_trend, is_trade_aligned_with_trend
from analysis.multi_timeframe import get_multi_timeframe_confirmation, format_multi_timeframe_for_prompt

# ── AI ──
from ai.claude_analyst import analyse_with_claude, validate_trade_decision, validate_sl_tp_with_atr

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
    now = datetime.now(GMT)
    if now.weekday() >= 5:
        return False
    hour = now.hour
    london = LONDON_START <= hour < LONDON_END
    ny     = NY_START <= hour < NY_END
    return london or ny


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
            pos_manager.manage_all()

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
                smc_data = {
                    "order_blocks": detect_order_blocks(df_h1),
                    "fvgs":         detect_fvg(df_m15, base_symbol=base_symbol),
                    "bos_choch":    detect_bos_choch(df_h1),
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
                    log.info(f"[{symbol}] No POI, no patterns, no sweeps. Skipping API call.")
                    continue

                # ── Get recent performance for AI context ──
                perf = trade_log.get_recent_performance(symbol)

                # ── Call Claude AI ──
                decision = analyse_with_claude(
                    symbol=symbol,
                    current_price=current_price,
                    df_m15=df_m15,
                    df_h1=df_h1,
                    smc_data=smc_data,
                    patterns=patterns,
                    sr_data=sr_data,
                    liquidity_text=liquidity_text,
                    htf_trend=htf_trend,
                    volume_info=volume_info,
                    atr_m15=atr_m15,
                    atr_h1=atr_h1,
                    recent_performance=perf,
                )

                # ── Log the signal ──
                trade_log.log_signal(symbol, decision, current_price, now)

                action     = decision.get("action", "HOLD")
                confidence = decision.get("confidence", 0)

                if action not in ("BUY", "SELL") or confidence < MIN_CONFIDENCE:
                    log.info(f"[{symbol}] Decision: {action} (conf {confidence}%). No trade.")
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

                # Gate 2: Validate SL/TP correctness
                valid, reason = validate_trade_decision(decision, symbol, current_price)
                if not valid:
                    log.warning(f"[{symbol}] ❌ Trade validation failed: {reason}")
                    continue

                # Gate 3: Validate SL/TP against ATR
                valid, reason = validate_sl_tp_with_atr(decision, atr_m15, base_symbol)
                if not valid:
                    log.warning(f"[{symbol}] ❌ ATR validation failed: {reason}")
                    continue

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
                    log.info(f"[{symbol}] ❌ M5 confirmation failed: {m5_pattern}. Waiting for trigger.")
                    continue
                log.info(f"[{symbol}] ✅ M5 confirmed: {m5_pattern}")

                # Gate 7: Multi-timeframe confirmation (M5 + M15 + H1 alignment)
                mtf_confirmed, mtf_reason, mtf_analysis = get_multi_timeframe_confirmation(
                    symbol, action, df_m5, df_m15, df_h1, min_confidence=70.0
                )
                if not mtf_confirmed:
                    log.info(f"[{symbol}] ❌ Multi-timeframe confirmation failed: {mtf_reason}")
                    continue
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
                    symbol_last_trade_time[symbol] = now
                    symbol_last_was_loss[symbol] = False

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
