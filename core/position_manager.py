"""
Position Manager — Manages open trades with breakeven, partial TP, trailing stop, and outcome tracking.
Each loop iteration:
  1. Optional partial close (default 50% at PARTIAL_TP_TRIGGER_RR, usually 2R)
  2. Breakeven SL at BREAKEVEN_TRIGGER_RR (default 1R)
  3. Trail SL after TRAIL_TRIGGER_RR (default 1.5R)
  4. Closed positions → trade log WIN/LOSS + real P&L
"""

import logging
from datetime import datetime, timedelta, timezone
import MetaTrader5 as mt5

from config.settings import (
    PIP_VALUE, MIN_LOT,
    BREAKEVEN_TRIGGER_RR, BREAKEVEN_BUFFER_PIPS,
    TRAIL_TRIGGER_RR, TRAIL_STEP_RATIO,
    MAX_TRADE_DURATION_MIN, INTRADAY_MODE,
    PARTIAL_TP_ENABLED, PARTIAL_TP_TRIGGER_RR,
    PARTIAL_TP_PERCENTAGE, PARTIAL_TP_MIN_LOT,
    VOLUME_CONFIRMATION_ENABLED, VOLUME_CONFIRMATION_MIN_RATIO,
)
from core.mt5_client import modify_position_sl, close_position, close_partial_position, get_current_volume_profile

log = logging.getLogger("smc_bot")


class PositionManager:
    """
    Tracks bot-managed positions and applies:
      - Partial TP: close PARTIAL_TP_PERCENTAGE of volume when profit >= PARTIAL_TP_TRIGGER_RR x risk
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
        volume: float,  # Added to track position size for partial TP
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
            "volume":         volume,  # Track volume for partial TP
            "be_applied":     False,
            "partial_tp_applied": False,  # Track if partial TP has been taken
            "remaining_volume": volume,  # Track remaining volume after partial TP
            "open_time":      datetime.now(timezone.utc),
        }
        log.info(f"[PositionManager] Tracking ticket {ticket} | {symbol} {direction} | "
                f"Volume: {volume} | Risk: {risk_distance:.5f}")

    def manage_all(self) -> list[str]:
        """
        Main management loop — call this every iteration.
        Checks each tracked position for:
          1. Closed status -> update logs
          2. Stale trade duration -> force close
          3. Breakeven trigger
          4. Trailing stop trigger
        Returns a list of symbols that were closed during this cycle.
        """
        closed_symbols = []
        if not self.tracked:
            return closed_symbols

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
                closed_symbols.append(info["symbol"])
            else:
                pos = next((p for p in open_positions if p.ticket == ticket), None)
                if pos:
                    # Check if trade is stale (exceeded max duration)
                    if self._is_stale_trade(info):
                        self._force_close_stale(ticket, info)
                        closed_tickets.append(ticket)
                        closed_symbols.append(info["symbol"])
                    else:
                        # Position still open — manage SL
                        self._manage_open_position(ticket, info, pos)

        # Clean up closed positions from tracking
        for ticket in closed_tickets:
            self.tracked.pop(ticket, None)

        return closed_symbols

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
        Robust P&L lookup from MT5 deal history with multiple matching strategies.
        Includes profit + commission + swap for accuracy.
        """
        try:
            # Safest approach: Query deals specifically tied to this position ticket directly
            # This completely bypasses any Server vs Local timezone offsets!
            found_deals = mt5.history_deals_get(position=ticket)
            
            if not found_deals:
                # Fallback: Query massive timezone-agnostic date range to defeat MT5 server offsets
                from datetime import datetime, timedelta
                
                # Using naive local time and stretching to capture anything regardless of timezone
                now_local = datetime.now()
                from_time = now_local - timedelta(days=7)
                to_time = now_local + timedelta(days=2) # Future buffer forces MT5 to dump all latest deals!
                
                deals = mt5.history_deals_get(from_time, to_time) or []
                
                found_deals = []
                for deal in deals:
                    if getattr(deal, "position_id", 0) == ticket or getattr(deal, "ticket", 0) == ticket:
                        found_deals.append(deal)

            if not found_deals:
                log.warning(f"[PositionManager] Could not find any history deals for ticket {ticket}")
                return 0.0

            total_pnl = 0.0
            
            # Calculate total P&L from found deals
            for deal in found_deals:
                profit = float(getattr(deal, "profit", 0.0))
                commission = float(getattr(deal, "commission", 0.0))
                swap = float(getattr(deal, "swap", 0.0))
                total_pnl += profit + commission + swap
                
            log.info(f"[PositionManager] Found {len(found_deals)} deals for ticket {ticket}, total P&L: ${total_pnl:.2f}")
            return total_pnl

        except Exception as e:
            log.error(f"[PositionManager] Error fetching P&L for ticket {ticket}: {e}")
            # Try one more fallback: check if position still exists in MT5
            try:
                positions = mt5.positions_get(ticket=ticket)
                if positions:
                    pos = positions[0]
                    # If position still exists, return current profit
                    return float(getattr(pos, "profit", 0.0))
            except Exception:
                pass
            return 0.0

    def _manage_open_position(self, ticket: int, info: dict, pos):
        """Apply breakeven, partial TP, and trailing stop logic to an open position."""
        entry          = info["entry"]
        risk_distance  = info["risk_distance"]
        direction      = info["direction"]
        current_price  = pos.price_current
        current_sl     = pos.sl
        current_volume = pos.volume

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

        # -- PARTIAL TAKE-PROFIT (default: 50% at 2R; override via PARTIAL_TP_TRIGGER_RR) --
        min_lot_symbol = float(MIN_LOT.get(base_symbol, 0.01))
        partial_volume = current_volume * PARTIAL_TP_PERCENTAGE
        if (
            PARTIAL_TP_ENABLED
            and not info["partial_tp_applied"]
            and profit_in_r >= PARTIAL_TP_TRIGGER_RR
            and current_volume >= PARTIAL_TP_MIN_LOT
            and partial_volume >= min_lot_symbol
        ):
            success, closed_pnl = close_partial_position(ticket, PARTIAL_TP_PERCENTAGE)
            if success:
                info["partial_tp_applied"] = True
                info["remaining_volume"] = current_volume * (1 - PARTIAL_TP_PERCENTAGE)

                self.trade_logger.update_partial_close(
                    ticket,
                    PARTIAL_TP_PERCENTAGE,
                    closed_pnl,
                    f"Partial TP at {profit_in_r:.1f}R (trigger {PARTIAL_TP_TRIGGER_RR}R)",
                )

                log.info(
                    f"[PositionManager] PARTIAL TP {PARTIAL_TP_PERCENTAGE*100:.0f}% | "
                    f"Ticket {ticket} | Closed at {profit_in_r:.1f}R (trigger={PARTIAL_TP_TRIGGER_RR}R)"
                )
            else:
                log.warning(f"[PositionManager] Failed to take partial TP for ticket {ticket}")

        # -- BREAKEVEN: Move SL to entry when profit >= 1R --
        if not info["be_applied"] and profit_in_r >= BREAKEVEN_TRIGGER_RR:
            can_breakeven = True
            
            if VOLUME_CONFIRMATION_ENABLED:
                vol_profile = get_current_volume_profile(info["symbol"])
                if vol_profile and vol_profile.get("available", False): # Use .get() defensively 
                    # Only move to breakeven if volume is rising or above average confirmation threshold
                    has_volume = vol_profile.get("rising", False) or vol_profile.get("ratio", 0) >= VOLUME_CONFIRMATION_MIN_RATIO
                    if not has_volume:
                        can_breakeven = False
                        log.debug(f"[PositionManager] Delaying BE for {ticket} due to low volume "
                                  f"(ratio: {vol_profile.get('ratio', 0):.2f}, rising: {vol_profile.get('rising', False)})")

            if can_breakeven:
                if direction == "BUY":
                    new_sl = entry + be_buffer
                    if current_sl < new_sl:
                        if modify_position_sl(ticket, new_sl):
                            info["be_applied"] = True
                            log.info(f"[PositionManager] BREAKEVEN | Ticket {ticket} | New SL: {new_sl:.5f} | Vol Confirmed: {VOLUME_CONFIRMATION_ENABLED}")
                else:  # SELL
                    new_sl = entry - be_buffer
                    if current_sl > new_sl or current_sl == 0:
                        if modify_position_sl(ticket, new_sl):
                            info["be_applied"] = True
                            log.info(f"[PositionManager] BREAKEVEN | Ticket {ticket} | New SL: {new_sl:.5f} | Vol Confirmed: {VOLUME_CONFIRMATION_ENABLED}")

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

