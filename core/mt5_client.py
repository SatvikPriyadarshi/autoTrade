"""
MT5 Client — All MetaTrader 5 connection, data, and order helpers.
Enhanced with comprehensive error handling and retry logic.
"""

import time
import MetaTrader5 as mt5
import pandas as pd
from config.settings import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_TERMINAL_PATH,
    MT5_INIT_RETRIES, MT5_INIT_RETRY_DELAY,
    SYMBOLS, SYMBOL_ALIASES, RESOLVED_SYMBOL_TO_BASE,
    MIN_LOT, MAX_LOT, RISK_PCT, MAX_RISK_PER_TRADE_USD, MAX_TRADE_LOSS_USD,
    BOT_MAGIC, BOT_COMMENT, log, PIP_VALUE,
)
from core.error_handler import (
    error_handler, check_mt5_connection, validate_symbol_data,
    log_data_quality_metrics, log_errors, return_default_on_error
)


# ──────────────────────────────────────────────
#  CONNECTION
# ──────────────────────────────────────────────
def connect_mt5() -> bool:
    """Initialize MT5 terminal and log in, with retries."""
    for attempt in range(1, MT5_INIT_RETRIES + 1):
        mt5.shutdown()
        init_ok = (
            mt5.initialize(path=MT5_TERMINAL_PATH) if MT5_TERMINAL_PATH
            else mt5.initialize()
        )
        if not init_ok:
            log.error(f"MT5 init failed (attempt {attempt}/{MT5_INIT_RETRIES}): {mt5.last_error()}")
            if attempt < MT5_INIT_RETRIES:
                time.sleep(MT5_INIT_RETRY_DELAY)
            continue

        if not mt5.login(MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER):
            log.error(f"MT5 login failed (attempt {attempt}/{MT5_INIT_RETRIES}): {mt5.last_error()}")
            mt5.shutdown()
            if attempt < MT5_INIT_RETRIES:
                time.sleep(MT5_INIT_RETRY_DELAY)
            continue

        info = mt5.account_info()
        if not info:
            log.error(f"MT5 account_info unavailable (attempt {attempt}/{MT5_INIT_RETRIES}).")
            mt5.shutdown()
            if attempt < MT5_INIT_RETRIES:
                time.sleep(MT5_INIT_RETRY_DELAY)
            continue

        log.info(f"Connected | Account: {info.login} | Balance: {info.balance} {info.currency}")
        return True

    log.error("MT5 connection failed after retries. Ensure MT5 desktop is open and connected.")
    return False


def ensure_connected() -> bool:
    """Check MT5 connection; reconnect if lost."""
    info = mt5.account_info()
    if info:
        return True
    log.warning("MT5 connection lost. Reconnecting...")
    return connect_mt5()


# ──────────────────────────────────────────────
#  SYMBOL RESOLUTION
# ──────────────────────────────────────────────
def resolve_symbol(base_symbol: str) -> str | None:
    """Find the broker's actual symbol name for a base symbol."""
    candidates = list(SYMBOL_ALIASES.get(base_symbol, [base_symbol]))

    if base_symbol == "XAUUSD":
        for sym in mt5.symbols_get() or []:
            name = sym.name.upper()
            if ("XAU" in name and "USD" in name) or "GOLD" in name:
                if sym.name not in candidates:
                    candidates.append(sym.name)

    for candidate in candidates:
        info = mt5.symbol_info(candidate)
        if not info:
            continue
        if not getattr(info, "visible", True):
            mt5.symbol_select(candidate, True)
        tick = mt5.symbol_info_tick(candidate)
        if tick and tick.bid > 0 and tick.ask > 0:
            return candidate
    return None


def resolve_all_symbols() -> list[str]:
    """Resolve all configured symbols to broker names. Returns tradable list."""
    tradable = []
    RESOLVED_SYMBOL_TO_BASE.clear()
    for base in SYMBOLS:
        resolved = resolve_symbol(base)
        if not resolved:
            log.warning(f"[{base}] No tradable tick found. Skipping.")
            continue
        tradable.append(resolved)
        RESOLVED_SYMBOL_TO_BASE[resolved] = base
        if resolved != base:
            log.info(f"[{base}] Using broker alias: {resolved}")
    return tradable


def get_base_symbol(symbol: str) -> str:
    """Map broker symbol back to our base symbol name."""
    return RESOLVED_SYMBOL_TO_BASE.get(symbol, symbol)


# ──────────────────────────────────────────────
#  MARKET DATA
# ──────────────────────────────────────────────
@error_handler.with_retry
@log_errors
def get_candles(symbol: str, timeframe: int, count: int) -> pd.DataFrame:
    """
    Fetch OHLCV candles from MT5 with retry logic and data validation.
    """
    # Validate symbol first
    is_valid, error_msg = validate_symbol_data(symbol)
    if not is_valid:
        log.error(f"[{symbol}] Cannot fetch candles: {error_msg}")
        return pd.DataFrame()
    
    # Fetch candles with retry
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None:
        error_code = mt5.last_error()
        log.error(f"[{symbol}] Failed to fetch candles: MT5 error {error_code}")
        return pd.DataFrame()
    
    df = pd.DataFrame(rates)
    if df.empty:
        log.warning(f"[{symbol}] Empty candle data for timeframe {timeframe}")
        return df
    
    df["time"] = pd.to_datetime(df["time"], unit="s")
    
    # Log data quality metrics
    timeframe_str = {mt5.TIMEFRAME_M5: "M5", mt5.TIMEFRAME_M15: "M15", 
                     mt5.TIMEFRAME_H1: "H1", mt5.TIMEFRAME_H4: "H4"}.get(timeframe, str(timeframe))
    log_data_quality_metrics(symbol, df, timeframe_str)
    
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


@log_errors
def get_spread(symbol: str) -> int:
    """Current spread in points with validation."""
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    
    if not tick:
        log.error(f"[{symbol}] No tick data for spread calculation")
        return 9999
    
    if not info:
        log.error(f"[{symbol}] No symbol info for spread calculation")
        return 9999
    
    if info.point <= 0:
        log.error(f"[{symbol}] Invalid point value: {info.point}")
        return 9999
    
    spread = (tick.ask - tick.bid) / info.point
    spread_points = round(spread)
    
    # Log warning for high spread
    max_allowed = 50  # Default max spread
    if spread_points > max_allowed:
        log.warning(f"[{symbol}] High spread: {spread_points} points (bid: {tick.bid}, ask: {tick.ask})")
    
    return spread_points


def get_current_price(symbol: str) -> float | None:
    """Get current mid-price."""
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return None
    return (tick.bid + tick.ask) / 2.0


def has_open_position(symbol: str) -> bool:
    """Check if there's an existing open position for this symbol."""
    pos = mt5.positions_get(symbol=symbol)
    return bool(pos and len(pos) > 0)


def get_all_bot_positions() -> list:
    """Get all positions opened by this bot."""
    positions = mt5.positions_get() or []
    return [p for p in positions if getattr(p, "magic", 0) == BOT_MAGIC]


def calculate_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Calculate Average True Range from OHLC data."""
    if len(df) < period + 1:
        return 0.0
    high = df["high"]
    low = df["low"]
    close_prev = df["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close_prev).abs(),
        (low - close_prev).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean().iloc[-1]
    return float(atr) if pd.notna(atr) else 0.0


def get_volume_profile(df: pd.DataFrame, period: int = 20) -> dict:
    """Analyze volume relative to its average."""
    if len(df) < period:
        return {"ratio": 0, "rising": False, "above_avg": False}
    avg_vol = df["tick_volume"].rolling(period).mean().iloc[-1]
    cur_vol = df["tick_volume"].iloc[-1]
    prev_vol = df["tick_volume"].iloc[-2]
    return {
        "ratio":     round(cur_vol / avg_vol, 2) if avg_vol > 0 else 0,
        "rising":    cur_vol > prev_vol,
        "above_avg": cur_vol > avg_vol,
    }


def get_current_volume_profile(symbol: str, timeframe: int = mt5.TIMEFRAME_M15, period: int = 20) -> dict:
    """Get current volume profile for a symbol."""
    df = get_candles(symbol, timeframe, period + 5)  # Get a few extra candles
    if df.empty or len(df) < period:
        return {"ratio": 0, "rising": False, "above_avg": False, "available": False}
    
    return {
        **get_volume_profile(df, period),
        "available": True,
        "current_volume": int(df["tick_volume"].iloc[-1]) if len(df) > 0 else 0,
        "timeframe": timeframe
    }


# ──────────────────────────────────────────────
#  POSITION SIZING
# ──────────────────────────────────────────────
@log_errors
def calculate_lot_size(symbol: str, direction: str, entry: float, sl: float) -> float:
    """
    Calculate lot size with multiple safety checks:
    1. RISK_PCT% of balance
    2. Capped by MAX_RISK_PER_TRADE_USD
    3. Capped by MAX_TRADE_LOSS_USD (absolute hard stop)
    4. Validated against broker limits
    """
    # Get account and symbol info
    account = mt5.account_info()
    sym_info = mt5.symbol_info(symbol)
    
    if not account:
        log.error(f"[{symbol}] No account info available")
        return 0.01
    
    if not sym_info:
        log.error(f"[{symbol}] No symbol info available")
        return 0.01
    
    balance = account.balance
    if balance <= 0:
        log.error(f"[{symbol}] Invalid balance: ${balance:.2f}")
        return 0.01
    
    # Calculate risk amount with multiple caps
    risk_from_pct = balance * (RISK_PCT / 100.0)
    risk_amount = min(risk_from_pct, MAX_RISK_PER_TRADE_USD, MAX_TRADE_LOSS_USD)
    
    log.info(f"[{symbol}] Balance: ${balance:.2f} | Risk caps: {RISK_PCT}%=${risk_from_pct:.2f}, "
             f"MAX_RISK=${MAX_RISK_PER_TRADE_USD}, MAX_LOSS=${MAX_TRADE_LOSS_USD} | "
             f"Final risk: ${risk_amount:.2f}")
    
    # Validate SL distance
    sl_distance = abs(entry - sl)
    if sl_distance == 0:
        log.error(f"[{symbol}] Zero SL distance (entry: {entry}, sl: {sl})")
        return 0.01
    
    # Calculate risk per lot
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    risk_per_lot = abs(mt5.order_calc_profit(order_type, symbol, 1.0, entry, sl) or 0.0)
    
    if risk_per_lot <= 0:
        log.warning(f"[{symbol}] Invalid risk per lot: ${risk_per_lot:.2f}, using default 0.01")
        return 0.01
    
    # Calculate lot size
    lot = risk_amount / risk_per_lot
    
    # Round to broker lot step
    lot_step = sym_info.volume_step
    if lot_step <= 0:
        log.warning(f"[{symbol}] Invalid lot step: {lot_step}, using 0.01")
        lot_step = 0.01
    
    lot = round(lot / lot_step) * lot_step
    
    # Clamp to symbol limits
    base = get_base_symbol(symbol)
    min_lot = MIN_LOT.get(base, 0.01)
    max_lot = MAX_LOT.get(base, 2.0)
    
    if lot < min_lot:
        log.warning(f"[{symbol}] Lot {lot:.4f} below minimum {min_lot}, adjusting")
        lot = min_lot
    elif lot > max_lot:
        log.warning(f"[{symbol}] Lot {lot:.4f} above maximum {max_lot}, adjusting")
        lot = max_lot
    
    lot = round(lot, 2)
    
    # Final validation
    est_risk = risk_per_lot * lot
    if est_risk > MAX_TRADE_LOSS_USD:
        log.error(f"[{symbol}] Estimated risk ${est_risk:.2f} exceeds MAX_TRADE_LOSS_USD ${MAX_TRADE_LOSS_USD}")
        # Recalculate with hard cap
        lot = MAX_TRADE_LOSS_USD / risk_per_lot
        lot = round(lot / lot_step) * lot_step
        lot = max(min_lot, min(lot, max_lot))
        lot = round(lot, 2)
        est_risk = risk_per_lot * lot
    
    log.info(
        f"[{symbol}] Final lot: {lot} | Est risk: ${est_risk:.2f} | "
        f"Target risk: ${risk_amount:.2f} | SL dist: {sl_distance:.5f} | "
        f"Risk/lot: ${risk_per_lot:.2f} | Balance: ${balance:.2f}"
    )
    
    return lot


# ──────────────────────────────────────────────
#  ORDER PLACEMENT
# ──────────────────────────────────────────────
@error_handler.with_retry
@log_errors
def place_order(symbol: str, direction: str, lot: float, sl: float, tp: float) -> tuple[bool, str, int]:
    """
    Send a market order to MT5 with retry logic and comprehensive error handling.
    Tries multiple filling modes and validates order parameters.
    Returns (success, error_message, ticket).
    """
    # Pre-order validation
    is_valid, error_msg = validate_symbol_data(symbol)
    if not is_valid:
        return False, f"Pre-order validation failed: {error_msg}", 0
    
    # Validate lot size
    base_symbol = get_base_symbol(symbol)
    min_lot = MIN_LOT.get(base_symbol, 0.01)
    max_lot = MAX_LOT.get(base_symbol, 2.0)
    
    if lot < min_lot:
        return False, f"Lot size {lot} below minimum {min_lot}", 0
    if lot > max_lot:
        return False, f"Lot size {lot} above maximum {max_lot}", 0
    
    # Validate SL/TP
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return False, "No tick data available", 0
    
    current_price = (tick.bid + tick.ask) / 2.0
    if direction == "BUY":
        if sl >= current_price:
            return False, f"SL {sl} must be below current price {current_price} for BUY", 0
        if tp <= current_price:
            return False, f"TP {tp} must be above current price {current_price} for BUY", 0
    else:  # SELL
        if sl <= current_price:
            return False, f"SL {sl} must be above current price {current_price} for SELL", 0
        if tp >= current_price:
            return False, f"TP {tp} must be below current price {current_price} for SELL", 0
    
    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "BUY" else tick.bid
    
    # Check spread before ordering
    spread = get_spread(symbol)
    max_spread = 50  # Configurable threshold
    if spread > max_spread:
        log.warning(f"[{symbol}] High spread {spread} points, order may fail")
    
    # Auto-detect broker-supported filling modes
    sym_info = mt5.symbol_info(symbol)
    filling_mode_bitmask = getattr(sym_info, "filling_mode", 0) if sym_info else 0
    
    # Build list of supported filling modes
    all_modes = [
        ("FOK",    mt5.ORDER_FILLING_FOK,    1),
        ("IOC",    mt5.ORDER_FILLING_IOC,    2),
        ("RETURN", mt5.ORDER_FILLING_RETURN, 4),
    ]
    
    if filling_mode_bitmask:
        filling_modes = [(name, mode) for name, mode, bit in all_modes if filling_mode_bitmask & bit]
    else:
        filling_modes = [(name, mode) for name, mode, _ in all_modes]
    
    if not filling_modes:
        filling_modes = [("RETURN", mt5.ORDER_FILLING_RETURN)]
    
    last_error = "Unknown MT5 error"
    for mode_name, mode_value in filling_modes:
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       symbol,
            "volume":       lot,
            "type":         order_type,
            "price":        price,
            "sl":           round(sl, 5),
            "tp":           round(tp, 5),
            "deviation":    10,
            "magic":        BOT_MAGIC,
            "comment":      BOT_COMMENT,
            "type_time":    mt5.ORDER_TIME_GTC,
            "type_filling": mode_value,
        }
        
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            ticket = getattr(result, "order", 0) or getattr(result, "deal", 0)
            log.info(f"✅ Order placed | {symbol} {direction} | Lot: {lot} | "
                    f"Fill: {mode_name} | SL: {sl} TP: {tp} | Ticket: {ticket}")
            return True, "", ticket
        
        retcode = getattr(result, "retcode", "N/A") if result else "N/A"
        comment = getattr(result, "comment", "Unknown") if result else "Unknown"
        last_error = f"retcode={retcode}, comment={comment}, fill={mode_name}"
        log.warning(f"Order attempt failed [{symbol}] {last_error}")
        
        # Check for specific errors that shouldn't be retried
        fatal_errors = {
            mt5.TRADE_RETCODE_INVALID_PRICE,
            mt5.TRADE_RETCODE_INVALID_STOPS,
            mt5.TRADE_RETCODE_INVALID_VOLUME,
            mt5.TRADE_RETCODE_NOT_ENOUGH_MONEY,
        }
        if retcode in fatal_errors:
            log.error(f"Fatal error {retcode}, stopping retries")
            break
    
    log.error(f"❌ Order failed [{symbol}] after all filling modes: {last_error}")
    return False, last_error, 0


def modify_position_sl(ticket: int, new_sl: float) -> bool:
    """Modify the stop loss of an open position."""
    pos = None
    for p in mt5.positions_get() or []:
        if p.ticket == ticket:
            pos = p
            break
    if not pos:
        return False

    request = {
        "action":   mt5.TRADE_ACTION_SLTP,
        "symbol":   pos.symbol,
        "position": ticket,
        "sl":       round(new_sl, 5),
        "tp":       round(pos.tp, 5),
        "magic":    BOT_MAGIC,
    }
    result = mt5.order_send(request)
    if result and result.retcode == mt5.TRADE_RETCODE_DONE:
        log.info(f"SL modified | Ticket {ticket} | New SL: {new_sl:.5f}")
        return True
    log.warning(f"SL modify failed | Ticket {ticket} | {getattr(result, 'comment', 'Unknown')}")
    return False


def close_position(ticket: int) -> tuple[bool, float]:
    """
    Close an open position by ticket.
    Returns (success, realized_pnl).
    """
    pos = None
    for p in mt5.positions_get() or []:
        if p.ticket == ticket:
            pos = p
            break
    if not pos:
        return False, 0.0

    # Close = opposite order
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return False, 0.0

    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask

    filling_modes = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_RETURN,
    ]

    for fill_mode in filling_modes:
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       pos.volume,
            "type":         close_type,
            "position":     ticket,
            "price":        price,
            "deviation":    10,
            "magic":        BOT_MAGIC,
            "comment":      "SMC_INTRADAY_CLOSE",
            "type_filling": fill_mode,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            pnl = pos.profit
            log.info(f"Position closed | Ticket {ticket} | {pos.symbol} | P&L: ${pnl:.2f}")
            return True, pnl

    log.error(f"Failed to close position {ticket} | {pos.symbol}")
    return False, 0.0


def close_partial_position(ticket: int, percentage: float) -> tuple[bool, float]:
    """
    Close a percentage of an open position.
    Returns (success, realized_pnl_for_closed_part).
    """
    pos = None
    for p in mt5.positions_get() or []:
        if p.ticket == ticket:
            pos = p
            break
    if not pos:
        return False, 0.0
    
    sym = mt5.symbol_info(pos.symbol)
    vol_min = float(getattr(sym, "volume_min", 0.01) or 0.01) if sym else 0.01
    vol_step = float(getattr(sym, "volume_step", 0.01) or 0.01) if sym else 0.01
    if vol_step <= 0:
        vol_step = 0.01

    target = pos.volume * percentage
    steps = max(0, int(round(target / vol_step)))
    close_volume = round(min(steps * vol_step, pos.volume), 8)
    if close_volume < vol_min and target >= vol_min:
        steps = max(1, int(round(vol_min / vol_step)))
        close_volume = round(min(steps * vol_step, pos.volume), 8)
    if close_volume < vol_min:
        log.warning(
            f"Cannot close partial: volume {close_volume} < symbol min {vol_min} (ticket {ticket})"
        )
        return False, 0.0
    
    # Close = opposite order
    close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.ORDER_TYPE_BUY else mt5.ORDER_TYPE_BUY
    tick = mt5.symbol_info_tick(pos.symbol)
    if not tick:
        return False, 0.0

    price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
    
    # Calculate P&L for the closed portion
    # This is approximate - MT5 will calculate exact P&L
    position_pnl = pos.profit
    closed_pnl = position_pnl * percentage
    
    filling_modes = [
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
        mt5.ORDER_FILLING_RETURN,
    ]

    for fill_mode in filling_modes:
        request = {
            "action":       mt5.TRADE_ACTION_DEAL,
            "symbol":       pos.symbol,
            "volume":       close_volume,
            "type":         close_type,
            "position":     ticket,
            "price":        price,
            "deviation":    10,
            "magic":        BOT_MAGIC,
            "comment":      f"SMC_PARTIAL_CLOSE_{int(percentage*100)}%",
            "type_filling": fill_mode,
        }
        result = mt5.order_send(request)
        if result and result.retcode == mt5.TRADE_RETCODE_DONE:
            log.info(f"Partial close {percentage*100:.0f}% | Ticket {ticket} | "
                    f"{pos.symbol} | Volume: {close_volume}/{pos.volume} | "
                    f"Est P&L: ${closed_pnl:.2f}")
            return True, closed_pnl
    
    log.error(f"Failed to close partial position {ticket} | {pos.symbol}")
    return False, 0.0


def close_all_bot_positions() -> int:
    """
    Close ALL positions opened by this bot (intraday end-of-session close).
    Returns count of closed positions.
    """
    bot_positions = get_all_bot_positions()
    if not bot_positions:
        return 0

    closed = 0
    for pos in bot_positions:
        success, pnl = close_position(pos.ticket)
        if success:
            closed += 1
            log.info(f"[INTRADAY CLOSE] {pos.symbol} | Ticket {pos.ticket} | P&L: ${pnl:.2f}")

    if closed > 0:
        log.info(f"[INTRADAY] Closed {closed}/{len(bot_positions)} positions at session end.")
    return closed

