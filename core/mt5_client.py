"""
MT5 Client — All MetaTrader 5 connection, data, and order helpers.
"""

import time
import MetaTrader5 as mt5
import pandas as pd
from config.settings import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_TERMINAL_PATH,
    MT5_INIT_RETRIES, MT5_INIT_RETRY_DELAY,
    SYMBOLS, SYMBOL_ALIASES, RESOLVED_SYMBOL_TO_BASE,
    MIN_LOT, MAX_LOT, RISK_PCT, MAX_RISK_PER_TRADE_USD,
    BOT_MAGIC, BOT_COMMENT, log, PIP_VALUE,
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
def get_candles(symbol: str, timeframe: int, count: int) -> pd.DataFrame:
    """Fetch OHLCV candles from MT5."""
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, count)
    if rates is None:
        return pd.DataFrame()
    df = pd.DataFrame(rates)
    df["time"] = pd.to_datetime(df["time"], unit="s")
    return df[["time", "open", "high", "low", "close", "tick_volume"]]


def get_spread(symbol: str) -> int:
    """Current spread in points."""
    tick = mt5.symbol_info_tick(symbol)
    info = mt5.symbol_info(symbol)
    if not tick or not info:
        return 9999
    return round((tick.ask - tick.bid) / info.point)


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


# ──────────────────────────────────────────────
#  POSITION SIZING
# ──────────────────────────────────────────────
def calculate_lot_size(symbol: str, direction: str, entry: float, sl: float) -> float:
    """
    Risk exactly RISK_PCT% of balance (capped by MAX_RISK_PER_TRADE_USD).
    Uses MT5 profit calculator for accurate pip-value per lot.
    """
    account = mt5.account_info()
    sym_info = mt5.symbol_info(symbol)
    if not account or not sym_info:
        log.warning(f"[{symbol}] Could not get account/symbol info, defaulting to 0.01")
        return 0.01

    balance = account.balance
    risk_from_pct = balance * (RISK_PCT / 100.0)
    risk_amount = min(risk_from_pct, MAX_RISK_PER_TRADE_USD)

    sl_distance = abs(entry - sl)
    if sl_distance == 0:
        return 0.01

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    risk_per_lot = abs(mt5.order_calc_profit(order_type, symbol, 1.0, entry, sl) or 0.0)
    if risk_per_lot <= 0:
        return 0.01

    lot = risk_amount / risk_per_lot

    # Round to broker lot step
    lot_step = sym_info.volume_step
    lot = round(lot / lot_step) * lot_step

    # Clamp to symbol limits
    base = get_base_symbol(symbol)
    lot = max(MIN_LOT.get(base, 0.01), min(lot, MAX_LOT.get(base, 2.0)))
    lot = round(lot, 2)

    est_risk = risk_per_lot * lot
    log.info(
        f"[{symbol}] Lot: {lot} | Est risk: ${est_risk:.2f} | "
        f"Target: ${risk_amount:.2f} | SL dist: {sl_distance:.5f} | Bal: ${balance:.2f}"
    )
    return lot


# ──────────────────────────────────────────────
#  ORDER PLACEMENT
# ──────────────────────────────────────────────
def place_order(symbol: str, direction: str, lot: float, sl: float, tp: float) -> tuple[bool, str, int]:
    """
    Send a market order to MT5.  Tries multiple filling modes.
    Returns (success, error_message, ticket).
    """
    tick = mt5.symbol_info_tick(symbol)
    if not tick:
        return False, "No tick data available", 0

    order_type = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
    price = tick.ask if direction == "BUY" else tick.bid

    # Auto-detect broker-supported filling modes from symbol info
    sym_info = mt5.symbol_info(symbol)
    filling_mode_bitmask = getattr(sym_info, "filling_mode", 0) if sym_info else 0

    # Build list of modes the broker actually supports (bitmask bits 0=FOK, 1=IOC, 2=RETURN)
    all_modes = [
        ("FOK",    mt5.ORDER_FILLING_FOK,    1),
        ("IOC",    mt5.ORDER_FILLING_IOC,    2),
        ("RETURN", mt5.ORDER_FILLING_RETURN, 4),
    ]
    if filling_mode_bitmask:
        # Only use modes the broker supports
        filling_modes = [(name, mode) for name, mode, bit in all_modes if filling_mode_bitmask & bit]
    else:
        # Fallback: try all modes
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
            log.info(f"Order placed | {symbol} {direction} | Fill={mode_name} | SL={sl} TP={tp} | Ticket={ticket}")
            return True, "", ticket

        retcode = getattr(result, "retcode", "N/A") if result else "N/A"
        comment = getattr(result, "comment", "Unknown") if result else "Unknown"
        last_error = f"retcode={retcode}, comment={comment}, fill={mode_name}"
        log.warning(f"Order attempt failed [{symbol}] {last_error}")

    log.error(f"Order failed [{symbol}] after all filling modes: {last_error}")
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

