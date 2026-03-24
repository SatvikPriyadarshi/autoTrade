"""
Position Manager — Manages open trades with breakeven, trailing stop, and outcome tracking.
Runs each loop iteration to:
  1. Move SL to breakeven when profit reaches 1:1 R
  2. Trail SL to lock in profits beyond 1.5R
  3. Detect closed positions and update trade logs with WIN/LOSS + actual P&L
"""

import logging
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5

from config.settings import (
    BOT_MAGIC, PIP_VALUE,
    BREAKEVEN_TRIGGER_RR, BREAKEVEN_BUFFER_PIPS,
    TRAIL_TRIGGER_RR, TRAIL_STEP_RATIO,
    MAX_TRADE_DURATION_MIN, INTRADAY_MODE,
)
from core.mt5_client import modify_position_sl, close_position

log = logging.getLogger("smc_bot")


class PositionManager:
    """
    Tracks bot-managed positions and applies:
      - Breakeven: move SL to entry +/- buffer when profit >= BREAKEVEN_TRIGGER_RR x risk
      - Trailing stop: trail SL behind price at TRAIL_STEP_RATIO x risk distance
      - Stale trade killer: closes trades older than MAX_TRADE_DURATION_MIN
      - Outcome tracking: detect closed positions, compute real P&L, update logger
    """

    def __init__(self, trade_logger, risk_manager):
        self.trade_logger  = trade_logger
        self.risk_manager  = risk_manager

        # Tracked positions: {ticket: {symbol, direction, entry, sl, tp, risk_distance, be_applied, open_time}}
        self.tracked: dict[int, dict] = {}

    def register_trade(
        self,
        ticket: int,
        symbol: str,
        direction: str,
        entry: float,
        sl: float,
        tp: float,
    ):
        """Register a newly opened trade for management."""
        risk_distance = abs(entry - sl)
        self.tracked[ticket] = {
            "symbol":         symbol,
            "direction":      direction,
            "entry":          entry,
            "sl":             sl,
            "tp":             tp,
            "original_sl":    sl,
            "risk_distance":  risk_distance,
            "be_applied":     False,
            "open_time":      datetime.now(timezone.utc),
        }
        log.info(f"[PositionManager] Tracking ticket {ticket} | {symbol} {direction} | Risk: {risk_distance:.5f}")

    def manage_all(self):
        """
        Main management loop — call this every iteration.
        Checks each tracked position for:
          1. Closed status -> update logs
          2. Stale trade duration -> force close
          3. Breakeven trigger
          4. Trailing stop trigger
        """
        if not self.tracked:
            return

        # Get current open positions from MT5
        open_positions = mt5.positions_get() or []
        open_tickets = {p.ticket for p in open_positions}

        # Check each tracked position
        closed_tickets = []
        for ticket, info in list(self.tracked.items()):
            if ticket not in open_tickets:
                # Position has been closed (by SL/TP or manually) -> track outcome
                self._handle_closed_position(ticket, info)
                closed_tickets.append(ticket)
            else:
                pos = next((p for p in open_positions if p.ticket == ticket), None)
                if pos:
                    # Check if trade is stale (exceeded max duration)
                    if self._is_stale_trade(info):
                        self._force_close_stale(ticket, info)
                        closed_tickets.append(ticket)
                    else:
                        # Position still open — manage SL
                        self._manage_open_position(ticket, info, pos)

        # Clean up closed positions from tracking
        for ticket in closed_tickets:
            self.tracked.pop(ticket, None)

    def _is_stale_trade(self, info: dict) -> bool:
        """Check if a trade has been open longer than MAX_TRADE_DURATION_MIN."""
        open_time = info.get("open_time")
        if not open_time:
            return False
        elapsed = (datetime.now(timezone.utc) - open_time).total_seconds() / 60.0
        return elapsed >= MAX_TRADE_DURATION_MIN

    def _force_close_stale(self, ticket: int, info: dict):
        """Force-close a stale trade that hasn't hit TP/SL within time limit."""
        elapsed = (datetime.now(timezone.utc) - info["open_time"]).total_seconds() / 60.0
        log.info(
            f"[PositionManager] STALE TRADE — closing {info['symbol']} {info['direction']} | "
            f"Ticket: {ticket} | Open for {elapsed:.0f} min (max {MAX_TRADE_DURATION_MIN})"
        )
        success, pnl = close_position(ticket)
        if success:
            status = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "CLOSED"
            self.trade_logger.update_trade_status(ticket, status, pnl)
            self.risk_manager.record_outcome(pnl > 0)

    def close_all_for_session_end(self):
        """
        INTRADAY MODE: Close all tracked bot positions before session ends.
        Called when session is about to end to ensure no overnight holds.
        """
        if not self.tracked:
            return

        log.info(f"[PositionManager] SESSION ENDING — Closing {len(self.tracked)} open positions...")

        closed_tickets = []
        for ticket, info in list(self.tracked.items()):
            success, pnl = close_position(ticket)
            if success:
                status = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "CLOSED"
                self.trade_logger.update_trade_status(ticket, status, pnl)
                self.risk_manager.record_outcome(pnl > 0)
                closed_tickets.append(ticket)
                log.info(
                    f"[INTRADAY CLOSE] {info['symbol']} {info['direction']} | "
                    f"Ticket: {ticket} | P&L: ${pnl:.2f}"
                )

        for ticket in closed_tickets:
            self.tracked.pop(ticket, None)

    def _handle_closed_position(self, ticket: int, info: dict):
        """
        A previously tracked position is no longer open.
        Look up the deal history to find real P&L, then update trade logs.
        """
        pnl = self._get_closed_pnl(ticket, info["symbol"])
        status = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "CLOSED"

        log.info(
            f"[PositionManager] Position closed: {info['symbol']} {info['direction']} | "
            f"Ticket: {ticket} | Status: {status} | P&L: ${pnl:.2f}"
        )

        # Update the trade log with real outcome
        self.trade_logger.update_trade_status(ticket, status, pnl)

        # Notify risk manager for streak tracking
        self.risk_manager.record_outcome(pnl > 0)

    def _get_closed_pnl(self, ticket: int, symbol: str) -> float:
        """
        Look up the realized P&L from MT5 deal history for a closed position.
        Includes profit + commission + swap for accuracy.
        """
        try:
            now = datetime.now(timezone.utc)
            from_time = now - timedelta(days=7)

            deals = mt5.history_deals_get(from_time, now) or []

            total_pnl = 0.0
            found = False
            for deal in deals:
                # Match deals by position ID
                deal_position = getattr(deal, "position_id", 0)
                if deal_position != ticket:
                    continue

                # Only count closing deals
                deal_entry = getattr(deal, "entry", None)
                close_entries = {
                    getattr(mt5, "DEAL_ENTRY_OUT", 1),
                    getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
                    getattr(mt5, "DEAL_ENTRY_INOUT", 2),
                }
                if deal_entry in close_entries:
                    profit     = float(getattr(deal, "profit", 0.0))
                    commission = float(getattr(deal, "commission", 0.0))
                    swap       = float(getattr(deal, "swap", 0.0))
                    total_pnl += profit + commission + swap
                    found = True

            if not found:
                log.warning(f"[PositionManager] Could not find close deals for ticket {ticket}")

            return total_pnl

        except Exception as e:
            log.error(f"[PositionManager] Error fetching P&L for ticket {ticket}: {e}")
            return 0.0

    def _manage_open_position(self, ticket: int, info: dict, pos):
        """Apply breakeven and trailing stop logic to an open position."""
        entry          = info["entry"]
        risk_distance  = info["risk_distance"]
        direction      = info["direction"]
        current_price  = pos.price_current
        current_sl     = pos.sl

        if risk_distance <= 0:
            return

        # Calculate current profit in price distance
        if direction == "BUY":
            profit_distance = current_price - entry
        else:  # SELL
            profit_distance = entry - current_price

        profit_in_r = profit_distance / risk_distance

        base_symbol = info["symbol"]
        pip = PIP_VALUE.get(base_symbol, 0.0001)
        be_buffer = BREAKEVEN_BUFFER_PIPS * pip

        # -- BREAKEVEN: Move SL to entry when profit >= 1R --
        if not info["be_applied"] and profit_in_r >= BREAKEVEN_TRIGGER_RR:
            if direction == "BUY":
                new_sl = entry + be_buffer
                if current_sl < new_sl:
                    if modify_position_sl(ticket, new_sl):
                        info["be_applied"] = True
                        log.info(f"[PositionManager] BREAKEVEN | Ticket {ticket} | New SL: {new_sl:.5f}")
            else:  # SELL
                new_sl = entry - be_buffer
                if current_sl > new_sl or current_sl == 0:
                    if modify_position_sl(ticket, new_sl):
                        info["be_applied"] = True
                        log.info(f"[PositionManager] BREAKEVEN | Ticket {ticket} | New SL: {new_sl:.5f}")

        # -- TRAILING STOP: Trail SL behind price after 1.5R --
        if info["be_applied"] and profit_in_r >= TRAIL_TRIGGER_RR:
            trail_distance = risk_distance * TRAIL_STEP_RATIO

            if direction == "BUY":
                new_sl = current_price - trail_distance
                if new_sl > current_sl:
                    modify_position_sl(ticket, new_sl)
                    log.info(f"[PositionManager] TRAIL SL | Ticket {ticket} | {current_sl:.5f} -> {new_sl:.5f}")
            else:  # SELL
                new_sl = current_price + trail_distance
                if new_sl < current_sl or current_sl == 0:
                    modify_position_sl(ticket, new_sl)
                    log.info(f"[PositionManager] TRAIL SL | Ticket {ticket} | {current_sl:.5f} -> {new_sl:.5f}")

    def get_tracked_count(self) -> int:
        return len(self.tracked)

