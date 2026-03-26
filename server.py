"""
Dashboard API Server
Run alongside bot.py to serve the trading dashboard.
Usage: python server.py
Then open: http://localhost:5000
"""

from flask import Flask, jsonify, send_from_directory, request
from flask_cors import CORS
import csv
import os
import json
import MetaTrader5 as mt5
from datetime import datetime, date, timedelta, timezone
from collections import defaultdict
from zoneinfo import ZoneInfo

app = Flask(__name__, static_folder="Dashboard")
CORS(app)

LOG_DIR      = "logs"
HISTORY_DIR  = os.path.join(LOG_DIR, "history")
TRADES_FILE  = os.path.join(LOG_DIR, "trades.csv")
SIGNALS_FILE = os.path.join(LOG_DIR, "signals.csv")
IST = ZoneInfo("Asia/Kolkata")


# ──────────────────────────────────────────────
#  HELPERS
# ──────────────────────────────────────────────
def read_csv(filepath: str) -> list:
    if not os.path.exists(filepath):
        return []
    with open(filepath, "r") as f:
        reader = csv.DictReader(f)
        return list(reader)


def safe_float(val, default=0.0):
    try:
        return float(val)
    except (TypeError, ValueError):
        return default


def utc_log_ts_to_ist_date(ts: str) -> str:
    if not ts:
        return ""
    try:
        dt_utc = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        return dt_utc.astimezone(IST).strftime("%Y-%m-%d")
    except Exception:
        return ""


def epoch_to_ist_date(epoch_seconds: int) -> str:
    try:
        return datetime.fromtimestamp(epoch_seconds, tz=timezone.utc).astimezone(IST).strftime("%Y-%m-%d")
    except Exception:
        return ""


def _sorted_by_timestamp(rows: list) -> list:
    return sorted(rows, key=lambda r: r.get("timestamp", ""))


def _history_run_dirs() -> list:
    if not os.path.exists(HISTORY_DIR):
        return []
    dirs = []
    for name in os.listdir(HISTORY_DIR):
        p = os.path.join(HISTORY_DIR, name)
        if os.path.isdir(p):
            dirs.append(p)
    dirs.sort()
    return dirs


def read_all_trades() -> list:
    rows = []
    for run_dir in _history_run_dirs():
        rows.extend(read_csv(os.path.join(run_dir, "trades.csv")))
    rows.extend(read_csv(TRADES_FILE))
    return _sorted_by_timestamp(rows)


def read_all_signals() -> list:
    rows = []
    for run_dir in _history_run_dirs():
        rows.extend(read_csv(os.path.join(run_dir, "signals.csv")))
    rows.extend(read_csv(SIGNALS_FILE))
    return _sorted_by_timestamp(rows)


def get_mt5_account_snapshot() -> dict:
    """Best-effort MT5 account snapshot for dashboard cards."""
    try:
        if not mt5.initialize():
            return {}
        info = mt5.account_info()
        if not info:
            return {}
        return {
            "account_login": info.login,
            "account_balance": round(float(info.balance), 2),
            "account_equity": round(float(info.equity), 2),
            "account_margin": round(float(info.margin), 2),
            "account_profit_open": round(float(info.profit), 2),
            "account_currency": info.currency,
        }
    except Exception:
        return {}
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def get_mt5_realized_daily_map(days_back: int = 120) -> dict:
    """
    Return realized P/L per day from MT5 deal history.
    Includes profit + commission + swap for DEAL_ENTRY_OUT deals.
    """
    daily = defaultdict(float)
    try:
        if not mt5.initialize():
            return {}

        dt_to = datetime.now()
        dt_from = dt_to - timedelta(days=days_back)
        deals = mt5.history_deals_get(dt_from, dt_to) or []

        close_like_entries = {
            getattr(mt5, "DEAL_ENTRY_OUT", 1),
            getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
            getattr(mt5, "DEAL_ENTRY_INOUT", 2),
        }

        for d in deals:
            symbol = str(getattr(d, "symbol", "") or "")
            entry = getattr(d, "entry", None)
            realized = float(getattr(d, "profit", 0.0)) + float(getattr(d, "commission", 0.0)) + float(getattr(d, "swap", 0.0))

            # Prefer close-like entries; fallback includes non-zero realized symbol deals.
            is_close_like = entry in close_like_entries
            is_realized_symbol_deal = bool(symbol) and abs(realized) > 0
            if not (is_close_like or is_realized_symbol_deal):
                continue

            ts = epoch_to_ist_date(getattr(d, "time", 0))
            if not ts:
                continue
            daily[ts] += realized
        return dict(daily)
    except Exception:
        return {}
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def get_mt5_closed_trade_count_daily(days_back: int = 120) -> dict:
    daily = defaultdict(int)
    try:
        if not mt5.initialize():
            return {}
        dt_to = datetime.now()
        dt_from = dt_to - timedelta(days=days_back)
        deals = mt5.history_deals_get(dt_from, dt_to) or []

        close_like_entries = {
            getattr(mt5, "DEAL_ENTRY_OUT", 1),
            getattr(mt5, "DEAL_ENTRY_OUT_BY", 3),
            getattr(mt5, "DEAL_ENTRY_INOUT", 2),
        }
        for d in deals:
            symbol = str(getattr(d, "symbol", "") or "")
            if not symbol:
                continue
            entry = getattr(d, "entry", None)
            if entry not in close_like_entries:
                continue
            ts = epoch_to_ist_date(getattr(d, "time", 0))
            if not ts:
                continue
            daily[ts] += 1
        return dict(daily)
    except Exception:
        return {}
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


def get_mt5_cashflow_total(days_back: int = 3650) -> float:
    """
    Sum account cashflow operations (deposit/withdraw/credit/balance adjustments).
    Used to derive all-time realized P/L from current account balance.
    """
    try:
        if not mt5.initialize():
            return 0.0
        dt_to = datetime.now()
        dt_from = dt_to - timedelta(days=days_back)
        deals = mt5.history_deals_get(dt_from, dt_to) or []

        cashflow_types = {
            getattr(mt5, "DEAL_TYPE_BALANCE", 2),
            getattr(mt5, "DEAL_TYPE_CREDIT", 3),
            getattr(mt5, "DEAL_TYPE_CHARGE", 4),
            getattr(mt5, "DEAL_TYPE_CORRECTION", 11),
            getattr(mt5, "DEAL_TYPE_BONUS", 6),
            getattr(mt5, "DEAL_TYPE_COMMISSION", 7),
            getattr(mt5, "DEAL_TYPE_COMMISSION_DAILY", 8),
            getattr(mt5, "DEAL_TYPE_COMMISSION_MONTHLY", 9),
            getattr(mt5, "DEAL_TYPE_COMMISSION_AGENT_DAILY", 10),
            getattr(mt5, "DEAL_TYPE_COMMISSION_AGENT_MONTHLY", 11),
        }

        total = 0.0
        for d in deals:
            if getattr(d, "type", None) in cashflow_types or not str(getattr(d, "symbol", "") or ""):
                total += float(getattr(d, "profit", 0.0))
        return total
    except Exception:
        return 0.0
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


# ──────────────────────────────────────────────
#  API ROUTES
# ──────────────────────────────────────────────
@app.route("/api/stats")
def get_stats():
    trades = read_all_trades()
    signals = read_all_signals()

    executed = [t for t in trades if (t.get("status", "OPEN") or "OPEN").upper() != "REJECTED"]
    rejected = [t for t in trades if (t.get("status", "OPEN") or "OPEN").upper() == "REJECTED"]
    open_trades = [t for t in executed if (t.get("status", "OPEN") or "OPEN").upper() == "OPEN"]
    closed_trades = [t for t in executed if (t.get("status", "OPEN") or "OPEN").upper() in ("WIN", "LOSS", "CLOSED")]

    total_trades = len(executed)
    winning = [t for t in closed_trades if safe_float(t.get("pnl")) > 0]
    losing  = [t for t in closed_trades if safe_float(t.get("pnl")) < 0]

    realized_pnl_from_logs = sum(safe_float(t.get("pnl")) for t in closed_trades)
    win_rate    = (len(winning) / len(closed_trades) * 100) if len(closed_trades) > 0 else 0
    avg_rr      = sum(safe_float(t.get("rr_ratio")) for t in executed) / len(executed) if len(executed) > 0 else 0

    # Today's stats
    today_ist = datetime.now(IST).strftime("%Y-%m-%d")
    today_trades_logs = [t for t in executed if utc_log_ts_to_ist_date(t.get("timestamp", "")) == today_ist]
    today_realized_from_logs = sum(safe_float(t.get("pnl")) for t in today_trades_logs)

    # Best / worst trade
    best  = max(closed_trades, key=lambda t: safe_float(t.get("pnl")), default=None)
    worst = min(closed_trades, key=lambda t: safe_float(t.get("pnl")), default=None)

    # Hold signals count
    total_signals = len(signals)
    hold_signals  = len([s for s in signals if s.get("action") == "HOLD"])
    mt5_snapshot = get_mt5_account_snapshot()
    mt5_realized_daily = get_mt5_realized_daily_map(days_back=120)
    mt5_closed_count_daily = get_mt5_closed_trade_count_daily(days_back=120)
    account_balance = mt5_snapshot.get("account_balance", 0.0)
    account_equity = mt5_snapshot.get("account_equity", 0.0)
    open_pnl = mt5_snapshot.get("account_profit_open", 0.0)
    realized_pnl_mt5 = sum(mt5_realized_daily.values()) if mt5_realized_daily else 0.0
    cashflow_total = get_mt5_cashflow_total(days_back=3650)
    realized_pnl_from_balance = safe_float(account_balance, 0.0) - safe_float(cashflow_total, 0.0)
    # Prefer balance-derived all-time realized P/L when account snapshot is available.
    if mt5_snapshot:
        realized_pnl = realized_pnl_from_balance
    else:
        realized_pnl = realized_pnl_mt5 if mt5_realized_daily else realized_pnl_from_logs
    today_realized_mt5 = safe_float(mt5_realized_daily.get(today_ist, 0.0))
    # Prefer MT5 realized P/L if available; logs can miss manual closes.
    today_realized_pnl = today_realized_mt5 if mt5_realized_daily else today_realized_from_logs
    today_trades_count = int(mt5_closed_count_daily.get(today_ist, 0)) if mt5_closed_count_daily else len(today_trades_logs)
    total_pnl = realized_pnl + open_pnl
    today_pnl = today_realized_pnl + open_pnl

    return jsonify({
        "total_trades":    total_trades,
        "open_trades":     len(open_trades),
        "closed_trades":   len(closed_trades),
        "rejected_trades": len(rejected),
        "total_signals":   total_signals,
        "hold_signals":    hold_signals,
        "winning_trades":  len(winning),
        "losing_trades":   len(losing),
        "realized_pnl":    round(realized_pnl, 2),
        "open_pnl":        round(open_pnl, 2),
        "total_pnl":       round(total_pnl, 2),
        "win_rate":        round(win_rate, 1),
        "avg_rr":          round(avg_rr, 2),
        "today_trades":    today_trades_count,
        "today_pnl":       round(today_pnl, 2),
        "today_realized_pnl": round(today_realized_pnl, 2),
        "best_trade_pnl":  round(safe_float(best.get("pnl") if best else 0), 2),
        "worst_trade_pnl": round(safe_float(worst.get("pnl") if worst else 0), 2),
        "account_balance": round(account_balance, 2),
        "account_equity":  round(account_equity, 2),
        "account_currency": mt5_snapshot.get("account_currency", "USD"),
    })


@app.route("/api/trades")
def get_trades():
    trades = read_all_trades()
    # Return most recent first
    trades.reverse()
    return jsonify(trades)


@app.route("/api/signals")
def get_signals():
    signals = read_all_signals()
    signals.reverse()
    return jsonify(signals[:100])  # Last 100


@app.route("/api/pnl_chart")
def get_pnl_chart():
    """Cumulative PnL over time for chart."""
    trades = read_all_trades()
    cumulative = 0
    data = []
    for t in trades:
        cumulative += safe_float(t.get("pnl"))
        data.append({
            "time":  t.get("timestamp", ""),
            "pnl":   round(cumulative, 2),
            "trade": f"{t.get('symbol')} {t.get('direction')}"
        })
    return jsonify(data)


@app.route("/api/symbol_breakdown")
def get_symbol_breakdown():
    """PnL and trade count per symbol."""
    trades = read_all_trades()
    breakdown = defaultdict(lambda: {"trades": 0, "pnl": 0.0, "wins": 0})
    for t in trades:
        sym = t.get("symbol", "UNKNOWN")
        pnl = safe_float(t.get("pnl"))
        breakdown[sym]["trades"] += 1
        breakdown[sym]["pnl"]    += pnl
        if pnl > 0:
            breakdown[sym]["wins"] += 1
    result = []
    for sym, data in breakdown.items():
        result.append({
            "symbol":   sym,
            "trades":   data["trades"],
            "pnl":      round(data["pnl"], 2),
            "win_rate": round(data["wins"] / data["trades"] * 100, 1) if data["trades"] > 0 else 0
        })
    return jsonify(result)


@app.route("/api/daily_breakdown")
def get_daily_breakdown():
    """Daily PnL for bar chart."""
    trades = read_all_trades()
    daily = defaultdict(float)
    for t in trades:
        day = t.get("timestamp", "")[:10]
        daily[day] += safe_float(t.get("pnl"))

    # Overlay MT5 realized history so manual/external closes are reflected.
    mt5_realized_daily = get_mt5_realized_daily_map(days_back=120)
    for d, pnl in mt5_realized_daily.items():
        daily[d] = pnl

    # Include current floating P/L in today's bucket so UI matches live account drawdown.
    mt5_snapshot = get_mt5_account_snapshot()
    open_pnl = safe_float(mt5_snapshot.get("account_profit_open", 0.0))
    today = datetime.now(IST).strftime("%Y-%m-%d")
    daily[today] += open_pnl

    result = [{"date": d, "pnl": round(p, 2)} for d, p in sorted(daily.items())]
    return jsonify(result)


@app.route("/api/mt5_deals")
def get_mt5_deals():
    """Paginated MT5 deals for debugging P/L and day grouping."""
    try:
        limit = max(1, min(int(request.args.get("limit", 20)), 200))
        offset = max(0, int(request.args.get("offset", 0)))
        only_bot = request.args.get("only_bot", "0") == "1"

        if not mt5.initialize():
            return jsonify({"rows": [], "total": 0, "offset": offset, "limit": limit})
        dt_to = datetime.now()
        dt_from = dt_to - timedelta(days=3650)
        deals = mt5.history_deals_get(dt_from, dt_to) or []

        # Newest first.
        deals = sorted(deals, key=lambda d: getattr(d, "time", 0), reverse=True)

        rows = []
        for d in deals:
            ts_utc = datetime.fromtimestamp(getattr(d, "time", 0), tz=timezone.utc)
            ts_ist = ts_utc.astimezone(IST)
            magic = int(getattr(d, "magic", 0) or 0)
            comment = str(getattr(d, "comment", "") or "")
            is_bot = magic == 20250101 or "SMC_AI_BOT" in comment
            if only_bot and not is_bot:
                continue
            rows.append({
                "ticket": getattr(d, "ticket", ""),
                "symbol": getattr(d, "symbol", ""),
                "entry": getattr(d, "entry", ""),
                "type": getattr(d, "type", ""),
                "magic": magic,
                "comment": comment,
                "source": "BOT" if is_bot else "MANUAL",
                "volume": round(float(getattr(d, "volume", 0.0)), 4),
                "profit": round(float(getattr(d, "profit", 0.0)), 2),
                "commission": round(float(getattr(d, "commission", 0.0)), 2),
                "swap": round(float(getattr(d, "swap", 0.0)), 2),
                "net": round(
                    float(getattr(d, "profit", 0.0))
                    + float(getattr(d, "commission", 0.0))
                    + float(getattr(d, "swap", 0.0)),
                    2,
                ),
                "time_utc": ts_utc.strftime("%Y-%m-%d %H:%M:%S"),
                "time_ist": ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
                "day_ist": ts_ist.strftime("%Y-%m-%d"),
            })

        total = len(rows)
        page = rows[offset: offset + limit]
        return jsonify({
            "rows": page,
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": (offset + limit) < total,
            "only_bot": only_bot,
        })
    except Exception:
        return jsonify({"rows": [], "total": 0, "offset": 0, "limit": 20, "has_more": False, "only_bot": False})
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


@app.route("/api/mt5_positions")
def get_mt5_positions():
    """Live MT5 open positions (includes bot/manual source tag)."""
    try:
        only_bot = request.args.get("only_bot", "0") == "1"
        if not mt5.initialize():
            return jsonify([])

        positions = mt5.positions_get() or []
        rows = []
        for p in positions:
            magic = int(getattr(p, "magic", 0) or 0)
            comment = str(getattr(p, "comment", "") or "")
            is_bot = magic == 20250101 or "SMC_AI_BOT" in comment
            if only_bot and not is_bot:
                continue

            ts_utc = datetime.fromtimestamp(getattr(p, "time", 0), tz=timezone.utc)
            ts_ist = ts_utc.astimezone(IST)
            rows.append({
                "ticket": getattr(p, "ticket", ""),
                "symbol": getattr(p, "symbol", ""),
                "type": getattr(p, "type", ""),
                "volume": round(float(getattr(p, "volume", 0.0)), 4),
                "price_open": round(float(getattr(p, "price_open", 0.0)), 5),
                "price_current": round(float(getattr(p, "price_current", 0.0)), 5),
                "sl": round(float(getattr(p, "sl", 0.0)), 5),
                "tp": round(float(getattr(p, "tp", 0.0)), 5),
                "profit": round(float(getattr(p, "profit", 0.0)), 2),
                "magic": magic,
                "comment": comment,
                "source": "BOT" if is_bot else "MANUAL",
                "time_ist": ts_ist.strftime("%Y-%m-%d %H:%M:%S"),
            })

        rows.sort(key=lambda r: r.get("time_ist", ""), reverse=True)
        return jsonify(rows)
    except Exception:
        return jsonify([])
    finally:
        try:
            mt5.shutdown()
        except Exception:
            pass


@app.route("/api/bot_thinking")
def get_bot_thinking():
    """
    Explain AI decision context per trade by combining trade rows with
    matching signal rows (same symbol + timestamp when available).
    """
    trades = read_all_trades()
    signals = read_all_signals()

    signal_by_key = {}
    for s in signals:
        key = (s.get("timestamp", ""), s.get("symbol", ""))
        signal_by_key[key] = s

    rows = []
    for t in trades:
        ts = t.get("timestamp", "")
        symbol = t.get("symbol", "")
        signal = signal_by_key.get((ts, symbol), {})

        action = (t.get("direction", "") or "").upper()
        status = (t.get("status", "OPEN") or "OPEN").upper()
        confidence = t.get("confidence", signal.get("confidence", ""))
        rr_ratio = t.get("rr_ratio", signal.get("rr_ratio", ""))
        key_level = signal.get("key_level", "")

        # Keep original trade reason (includes MT5 rejection when applicable).
        reason = t.get("reason", "") or signal.get("reason", "")
        confluences = signal.get("confluences", "")

        rows.append({
            "timestamp": ts,
            "symbol": symbol,
            "action": action,
            "status": status,
            "confidence": confidence,
            "rr_ratio": rr_ratio,
            "entry": t.get("entry", signal.get("entry", "")),
            "sl": t.get("sl", signal.get("sl", "")),
            "tp": t.get("tp", signal.get("tp", "")),
            "key_level": key_level,
            "reason": reason,
            "confluences": confluences,
        })

    rows = sorted(rows, key=lambda r: r.get("timestamp", ""), reverse=True)
    return jsonify(rows)


# ──────────────────────────────────────────────
#  SERVE DASHBOARD
# ──────────────────────────────────────────────
@app.route("/")
def index():
    return send_from_directory("Dashboard", "index.html")


if __name__ == "__main__":
    print("Dashboard running at http://localhost:5000")
    app.run(debug=True, port=5000)
