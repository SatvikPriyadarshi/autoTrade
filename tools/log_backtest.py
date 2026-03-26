#!/usr/bin/env python3
"""
Offline analysis of bot CSV logs — tune algo knobs without live MT5.

Examples:
  python tools/log_backtest.py logs/trades.csv
  python tools/log_backtest.py logs/trades.csv --signals logs/signals.csv
  python tools/log_backtest.py logs/trades.csv --min-confidence 85

Reads trades.csv (status WIN/LOSS/CLOSED/PARTIAL/OPEN) and optional signals.csv
to report win rate, PnL, and how many historical BUY/SELL signals would pass a
higher confidence filter.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path


def _read_csv(path: str) -> list[list[str]]:
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        return list(csv.reader(f))


def summarize_trades(rows: list[list[str]]) -> dict:
    if len(rows) < 2:
        return {"error": "no data rows"}
    header = rows[0]
    idx = {name: i for i, name in enumerate(header)}
    need = ["symbol", "status", "pnl", "direction", "rr_ratio", "confidence"]
    for k in need:
        if k not in idx:
            return {"error": f"missing column {k} in trades header"}

    by_sym: dict[str, list[float]] = defaultdict(list)
    wins = losses = 0
    pnl_total = 0.0
    for row in rows[1:]:
        if len(row) <= max(idx.values()):
            continue
        st = (row[idx["status"]] or "").strip().upper()
        if st not in ("WIN", "LOSS"):
            continue
        try:
            pnl = float(row[idx["pnl"]] or 0)
        except ValueError:
            pnl = 0.0
        sym = row[idx["symbol"]]
        by_sym[sym].append(pnl)
        pnl_total += pnl
        if st == "WIN":
            wins += 1
        else:
            losses += 1

    closed = wins + losses
    wr = (100.0 * wins / closed) if closed else 0.0
    return {
        "closed_trades": closed,
        "wins": wins,
        "losses": losses,
        "win_rate_pct": round(wr, 2),
        "pnl_total": round(pnl_total, 2),
        "by_symbol": {s: (len(v), round(sum(v), 2)) for s, v in sorted(by_sym.items())},
    }


def filter_signals(rows: list[list[str]], min_conf: float) -> tuple[int, int, int]:
    """Returns (total BUY+SELL, passing min_conf, HOLD)."""
    if len(rows) < 2:
        return 0, 0, 0
    h = rows[0]
    idx = {name: i for i, name in enumerate(h)}
    for k in ("action", "confidence"):
        if k not in idx:
            return 0, 0, 0
    total_bs = pass_f = holds = 0
    for row in rows[1:]:
        if len(row) <= max(idx.values()):
            continue
        act = (row[idx["action"]] or "").strip().upper()
        if act == "HOLD":
            holds += 1
            continue
        if act not in ("BUY", "SELL"):
            continue
        total_bs += 1
        try:
            c = float(row[idx["confidence"]] or 0)
        except ValueError:
            c = 0.0
        if c >= min_conf:
            pass_f += 1
    return total_bs, pass_f, holds


def main() -> int:
    p = argparse.ArgumentParser(description="Summarize trading_bot CSV logs")
    p.add_argument("trades_csv", nargs="?", default="logs/trades.csv", help="Path to trades.csv")
    p.add_argument("--signals", default="", help="Optional signals.csv path")
    p.add_argument("--min-confidence", type=float, default=80.0, help="Counterfactual: signals with conf >= this")
    args = p.parse_args()

    tpath = Path(args.trades_csv)
    if not tpath.is_file():
        print(f"No file: {tpath}", file=sys.stderr)
        return 1

    trades = _read_csv(str(tpath))
    summ = summarize_trades(trades)
    print(f"=== {tpath} ===")
    if "error" in summ:
        print(summ["error"])
    else:
        print(f"Closed (WIN+LOSS): {summ['closed_trades']} | WR: {summ['win_rate_pct']}% | PnL: {summ['pnl_total']}")
        for sym, (n, pnl) in summ["by_symbol"].items():
            print(f"  {sym}: n={n} pnl={pnl}")

    if args.signals:
        spath = Path(args.signals)
        if spath.is_file():
            sig = _read_csv(str(spath))
            total, passed, holds = filter_signals(sig, args.min_confidence)
            print(f"\n=== {spath} (counterfactual) ===")
            print(f"BUY+SELL signals: {total} | with confidence >= {args.min_confidence}: {passed} | HOLD rows: {holds}")
        else:
            print(f"\n(Signals file not found: {spath})")

    print(
        "\nTune live bot via env: ALGO_MIN_POI_CONFLUENCE, ALGO_MTF_MIN_CONFIDENCE, "
        "ALGO_REQUIRE_M5_CONFIRM, ALGO_REQUIRE_MTF_CONFIRM, MIN_CONFIDENCE (settings.py)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
