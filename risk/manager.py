"""
Risk Manager — Daily limits, streak tracking, correlation checks.
"""

import logging
import MetaTrader5 as mt5
from datetime import date
from config.settings import (
    MAX_TRADES_DAY, MAX_DAILY_LOSS_PCT, MAX_DAILY_LOSS_USD,
    MAX_CONSECUTIVE_LOSSES, CORRELATED_PAIRS, BOT_MAGIC,
)

log = logging.getLogger("smc_bot")


class RiskManager:
    """
    Enforces:
      - Max trades per day
      - Max daily drawdown (% and USD)
      - Consecutive loss auto-pause
      - Correlated pair double-exposure prevention
    """

    def __init__(
        self,
        max_trades: int = MAX_TRADES_DAY,
        max_daily_loss_pct: float = MAX_DAILY_LOSS_PCT,
        max_daily_loss_usd: float = MAX_DAILY_LOSS_USD,
        max_consecutive_losses: int = MAX_CONSECUTIVE_LOSSES,
    ):
        self.max_trades = max_trades
        self.max_daily_loss_pct = max_daily_loss_pct
        self.max_daily_loss_usd = max_daily_loss_usd
        self.max_consecutive_losses = max_consecutive_losses

        self.trades_today = 0
        self.starting_balance = None
        self.current_date = date.today()

        # Streak tracker
        self.consecutive_losses = 0
        self.streak_paused = False

    # ──────────────────────────────────────────
    #  DAY RESET
    # ──────────────────────────────────────────
    def reset_if_new_day(self):
        today = date.today()
        if today != self.current_date:
            self.trades_today = 0
            self.current_date = today
            self.starting_balance = None
            self.consecutive_losses = 0
            self.streak_paused = False
            log.info("[RiskManager] New day — all counters reset.")

    # ──────────────────────────────────────────
    #  BALANCE CHECK
    # ──────────────────────────────────────────
    def _get_balance(self) -> float:
        info = mt5.account_info()
        return info.balance if info else 0.0

    # ──────────────────────────────────────────
    #  DAILY LIMIT CHECK
    # ──────────────────────────────────────────
    def is_daily_limit_hit(self) -> bool:
        """Check if any daily limit (trades, loss USD, loss %) has been reached."""
        if self.trades_today >= self.max_trades:
            log.info(f"[RiskManager] Daily trade limit reached: {self.trades_today}/{self.max_trades}")
            return True

        balance = self._get_balance()
        if self.starting_balance is None:
            self.starting_balance = balance
            return False

        loss_usd = self.starting_balance - balance
        loss_pct = (loss_usd / self.starting_balance * 100) if self.starting_balance > 0 else 0

        if loss_usd >= self.max_daily_loss_usd:
            log.info(f"[RiskManager] Daily USD loss limit: -${loss_usd:.2f} (cap ${self.max_daily_loss_usd:.2f})")
            return True

        if loss_pct >= self.max_daily_loss_pct:
            log.info(f"[RiskManager] Daily % loss limit: -{loss_pct:.2f}% (cap {self.max_daily_loss_pct:.1f}%)")
            return True

        return False

    # ──────────────────────────────────────────
    #  TRADE RECORDING
    # ──────────────────────────────────────────
    def record_trade(self):
        self.trades_today += 1
        log.info(f"[RiskManager] Trade #{self.trades_today}/{self.max_trades} today.")

    # ──────────────────────────────────────────
    #  STREAK TRACKING
    # ──────────────────────────────────────────
    def record_outcome(self, won: bool):
        """Call this when a trade closes to track the win/loss streak."""
        if won:
            self.consecutive_losses = 0
            self.streak_paused = False
        else:
            self.consecutive_losses += 1
            if self.consecutive_losses >= self.max_consecutive_losses:
                self.streak_paused = True
                log.warning(
                    f"[RiskManager] {self.consecutive_losses} consecutive losses — "
                    f"PAUSING trading for remainder of session."
                )

    def is_streak_paused(self) -> bool:
        return self.streak_paused

    # ──────────────────────────────────────────
    #  CORRELATION CHECK
    # ──────────────────────────────────────────
    def has_correlated_exposure(self, symbol: str, direction: str) -> bool:
        """
        Check if opening a trade on `symbol` in `direction` would create
        double exposure via a correlated pair.
        """
        from core.mt5_client import get_base_symbol

        base = get_base_symbol(symbol)
        positions = mt5.positions_get() or []

        for pos in positions:
            if getattr(pos, "magic", 0) != BOT_MAGIC:
                continue

            pos_base = get_base_symbol(pos.symbol)
            pair_key = frozenset((base, pos_base))
            correlation = CORRELATED_PAIRS.get(pair_key, 0)

            if correlation >= 0.7:
                pos_direction = "BUY" if pos.type == mt5.ORDER_TYPE_BUY else "SELL"
                if direction == pos_direction:
                    log.info(
                        f"[RiskManager] Correlated exposure blocked: "
                        f"{symbol} {direction} vs open {pos.symbol} {pos_direction} "
                        f"(correlation: {correlation})"
                    )
                    return True

        return False
