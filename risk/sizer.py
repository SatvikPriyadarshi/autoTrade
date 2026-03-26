from __future__ import annotations

import MetaTrader5 as mt5

from config.settings import (
    MAX_RISK_PER_TRADE_PERCENT,
    MAX_RISK_PER_TRADE_USD,
    MAX_TRADE_LOSS_USD,
    MIN_LOT,
    MAX_LOT,
)
from core.mt5_client import get_base_symbol


def size_position(symbol: str, direction: str, entry: float, sl: float) -> dict:
    """Return sizing metadata with strict per-trade risk cap."""
    account = mt5.account_info()
    sym = mt5.symbol_info(symbol)
    if not account or not sym:
        return {"ok": False, "reason": "NO_ACCOUNT_OR_SYMBOL", "lot": 0.0}

    risk_per_lot = abs(mt5.order_calc_profit(
        mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL,
        symbol,
        1.0,
        entry,
        sl,
    ) or 0.0)
    if risk_per_lot <= 0:
        return {"ok": False, "reason": "INVALID_RISK_PER_LOT", "lot": 0.0}

    balance = float(account.balance)
    risk_budget = min(
        balance * (MAX_RISK_PER_TRADE_PERCENT / 100.0),
        MAX_RISK_PER_TRADE_USD,
        MAX_TRADE_LOSS_USD,
    )
    raw_lot = risk_budget / risk_per_lot

    lot_step = float(getattr(sym, "volume_step", 0.01) or 0.01)
    lot_min_broker = float(getattr(sym, "volume_min", 0.01) or 0.01)
    lot_max_broker = float(getattr(sym, "volume_max", 100.0) or 100.0)

    base = get_base_symbol(symbol)
    lot_min_cfg = float(MIN_LOT.get(base, 0.01))
    lot_max_cfg = float(MAX_LOT.get(base, lot_max_broker))

    lot = round(raw_lot / lot_step) * lot_step
    min_allowed = max(lot_min_broker, lot_min_cfg)
    max_allowed = min(lot_max_broker, lot_max_cfg)

    if lot < min_allowed:
        return {
            "ok": False,
            "reason": "LOT_BELOW_MIN_SKIP",
            "lot": 0.0,
            "risk_budget": risk_budget,
            "risk_per_lot": risk_per_lot,
            "min_lot": min_allowed,
        }

    lot = max(min_allowed, min(lot, max_allowed))
    lot = round(lot, 2)
    est_loss = risk_per_lot * lot

    if est_loss > risk_budget * 1.02:
        return {
            "ok": False,
            "reason": "RISK_EXCEEDS_LIMIT",
            "lot": 0.0,
            "risk_budget": risk_budget,
            "est_loss": est_loss,
        }

    return {
        "ok": True,
        "reason": "OK",
        "lot": lot,
        "est_loss": est_loss,
        "risk_budget": risk_budget,
        "risk_per_lot": risk_per_lot,
        "sl_distance": abs(entry - sl),
    }
