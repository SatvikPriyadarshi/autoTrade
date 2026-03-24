"""
Trade Logger — CSV logging with trade outcome tracking.
Logs all signals and trades, and can update trade status (OPEN → WIN/LOSS) with real P&L.
"""

import csv
import os
import shutil
import logging
from datetime import datetime
from config.settings import GMT

log = logging.getLogger("smc_bot")


class TradeLogger:
    """
    Manages two CSV files:
      - signals.csv: every AI evaluation (BUY/SELL/HOLD)
      - trades.csv:  every executed order + status + P&L

    On each bot restart, existing logs with data are rotated to logs/history/.
    """

    def __init__(self, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        self.log_dir     = log_dir
        self.history_dir = os.path.join(log_dir, "history")
        os.makedirs(self.history_dir, exist_ok=True)
        self.signals_file = os.path.join(log_dir, "signals.csv")
        self.trades_file  = os.path.join(log_dir, "trades.csv")
        self._rotate_existing_logs()
        self._init_files()

    # ──────────────────────────────────────────
    #  FILE MANAGEMENT
    # ──────────────────────────────────────────
    def _file_has_data_rows(self, filepath: str) -> bool:
        if not os.path.exists(filepath):
            return False
        try:
            with open(filepath, "r", newline="") as f:
                rows = list(csv.reader(f))
                return len(rows) > 1
        except Exception:
            return False

    def _rotate_existing_logs(self):
        has_signals = self._file_has_data_rows(self.signals_file)
        has_trades  = self._file_has_data_rows(self.trades_file)
        if not has_signals and not has_trades:
            return

        run_stamp = datetime.now(GMT).strftime("%Y-%m-%d_%H-%M-%S")
        run_dir = os.path.join(self.history_dir, run_stamp)
        os.makedirs(run_dir, exist_ok=True)

        if os.path.exists(self.signals_file):
            shutil.move(self.signals_file, os.path.join(run_dir, "signals.csv"))
        if os.path.exists(self.trades_file):
            shutil.move(self.trades_file, os.path.join(run_dir, "trades.csv"))
        log.info(f"[TradeLogger] Rotated previous logs to {run_dir}")

    def _init_files(self):
        if not os.path.exists(self.signals_file):
            with open(self.signals_file, "w", newline="") as f:
                csv.writer(f).writerow([
                    "timestamp", "symbol", "action", "confidence",
                    "reason", "entry", "sl", "tp", "rr_ratio",
                    "key_level", "confluences",
                ])

        if not os.path.exists(self.trades_file):
            with open(self.trades_file, "w", newline="") as f:
                csv.writer(f).writerow([
                    "timestamp", "symbol", "direction", "lot",
                    "entry", "sl", "tp", "rr_ratio", "confidence",
                    "reason", "status", "pnl", "ticket",
                ])

    # ──────────────────────────────────────────
    #  SIGNAL LOGGING
    # ──────────────────────────────────────────
    def log_signal(self, symbol: str, decision: dict, price: float, ts: datetime):
        action   = decision.get("action", "HOLD")
        entry    = decision.get("entry", price)
        sl       = decision.get("sl", "")
        tp       = decision.get("tp", "")
        rr_ratio = decision.get("rr_ratio", "")

        # Avoid noisy placeholders for HOLD signals
        if action == "HOLD":
            entry = "" if not entry or (isinstance(entry, (int, float)) and entry == 0) else entry
            sl = "" if not sl or (isinstance(sl, (int, float)) and sl == 0) else sl
            tp = "" if not tp or (isinstance(tp, (int, float)) and tp == 0) else tp
            rr_ratio = "" if not rr_ratio or (isinstance(rr_ratio, (int, float)) and rr_ratio == 0) else rr_ratio

        confluences = ", ".join(decision.get("confluences", []))

        with open(self.signals_file, "a", newline="") as f:
            csv.writer(f).writerow([
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                action,
                decision.get("confidence", 0),
                decision.get("reason", ""),
                entry, sl, tp, rr_ratio,
                decision.get("key_level", ""),
                confluences,
            ])

    # ──────────────────────────────────────────
    #  TRADE LOGGING
    # ──────────────────────────────────────────
    def log_trade(
        self,
        symbol: str,
        direction: str,
        lot: float,
        decision: dict,
        price: float,
        ts: datetime,
        status: str = "OPEN",
        pnl: float = 0.0,
        ticket: int = 0,
    ):
        with open(self.trades_file, "a", newline="") as f:
            csv.writer(f).writerow([
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                symbol,
                direction,
                lot,
                decision.get("entry", price),
                decision.get("sl", ""),
                decision.get("tp", ""),
                decision.get("rr_ratio", ""),
                decision.get("confidence", 0),
                decision.get("reason", ""),
                status,
                pnl,
                ticket,
            ])

    # ──────────────────────────────────────────
    #  TRADE OUTCOME UPDATE
    # ──────────────────────────────────────────
    def update_trade_status(self, ticket: int, status: str, pnl: float):
        """
        Update a trade row by ticket number with final status (WIN/LOSS) and P&L.
        This is called when the PositionManager detects a closed position.
        """
        if not os.path.exists(self.trades_file):
            return False

        rows = []
        updated = False
        with open(self.trades_file, "r", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                row_ticket = str(row.get("ticket", ""))
                if row_ticket == str(ticket) and row.get("status", "OPEN") == "OPEN":
                    row["status"] = status
                    row["pnl"] = round(pnl, 2)
                    updated = True
                    log.info(f"[TradeLogger] Updated ticket {ticket}: {status} | P&L: ${pnl:.2f}")
                rows.append(row)

        if updated and fieldnames:
            with open(self.trades_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        return updated

    def update_trade_by_symbol_time(self, symbol: str, timestamp: str, status: str, pnl: float):
        """Fallback: update by symbol + timestamp if ticket is unavailable."""
        if not os.path.exists(self.trades_file):
            return False

        rows = []
        updated = False
        with open(self.trades_file, "r", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames
            for row in reader:
                if (row.get("symbol") == symbol
                        and row.get("timestamp") == timestamp
                        and row.get("status", "OPEN") == "OPEN"):
                    row["status"] = status
                    row["pnl"] = round(pnl, 2)
                    updated = True
                rows.append(row)

        if updated and fieldnames:
            with open(self.trades_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        return updated

    # ──────────────────────────────────────────
    #  PERFORMANCE RETRIEVAL
    # ──────────────────────────────────────────
    def get_recent_performance(self, symbol: str, last_n: int = 10) -> dict:
        """
        Get the bot's recent performance on a specific symbol.
        Used to provide feedback context to Claude.
        """
        if not os.path.exists(self.trades_file):
            return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0}

        trades = []
        with open(self.trades_file, "r", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                if row.get("symbol") == symbol and row.get("status") in ("WIN", "LOSS"):
                    trades.append(row)

        recent = trades[-last_n:] if trades else []
        wins   = sum(1 for t in recent if t.get("status") == "WIN")
        losses = sum(1 for t in recent if t.get("status") == "LOSS")
        total  = len(recent)

        return {
            "total":    total,
            "wins":     wins,
            "losses":   losses,
            "win_rate": (wins / total * 100) if total > 0 else 0,
        }

    def print_summary(self):
        if not os.path.exists(self.trades_file):
            log.info("No trades logged yet.")
            return
        with open(self.trades_file, "r") as f:
            trades = list(csv.DictReader(f))
        total = len(trades)
        wins  = sum(1 for t in trades if t.get("status") == "WIN")
        losses = sum(1 for t in trades if t.get("status") == "LOSS")
        pnl = sum(float(t.get("pnl", 0)) for t in trades if t.get("pnl"))
        log.info(f"{'='*50}")
        log.info(f"  TRADE SUMMARY — {total} trades | {wins}W / {losses}L | P&L: ${pnl:.2f}")
        log.info(f"{'='*50}")
        for t in trades[-10:]:
            log.info(
                f"  {t.get('timestamp')} | {t.get('symbol')} {t.get('direction')} | "
                f"Status: {t.get('status')} | P&L: {t.get('pnl', 'N/A')}"
            )
