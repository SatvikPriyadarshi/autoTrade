# SMC + Price Action AI Trading Bot v2.0 — Detailed Explanation

This document explains the full architecture, data flow, and every module in the system.

## 1) Project Purpose

An automated intraday trading bot that:

- Connects to MetaTrader 5 (MT5) for market data and order placement
- Runs **comprehensive analysis**: SMC (Order Blocks, FVGs, BOS/CHOCH, Liquidity Sweeps), Price Action patterns, Support/Resistance, and HTF trend filtering
- Asks Claude AI for a final trade decision with full context
- **Validates every decision** (SL/TP correctness, ATR range, trend alignment, correlation)
- Manages open positions with **breakeven + trailing stop**
- Tracks trade outcomes automatically (WIN/LOSS with real P&L)
- Filters out **high-impact news events**
- Logs all signals/trades to CSV and serves a live dashboard

## 2) Folder Structure

```
trading_bot/
├── main.py                    # Entry point — main trading loop
├── config/
│   ├── __init__.py
│   └── settings.py            # All configuration centralized
├── core/
│   ├── __init__.py
│   ├── mt5_client.py          # MT5 connection, data, orders, lot sizing
│   └── position_manager.py    # Breakeven, trailing SL, outcome tracking
├── analysis/
│   ├── __init__.py
│   ├── smc.py                 # Order Blocks, FVGs (min-size + fill tracking), BOS/CHOCH
│   ├── patterns.py            # 11 candlestick patterns
│   ├── support_resistance.py  # Swing-based S/R with clustering
│   ├── liquidity.py           # Liquidity sweeps + equal highs/lows
│   └── trend.py               # H4 trend filter (EMA + structure)
├── ai/
│   ├── __init__.py
│   └── claude_analyst.py      # Prompt builder, Claude API, validation
├── risk/
│   ├── __init__.py
│   ├── manager.py             # Daily limits, streak tracking, correlation check
│   └── news_filter.py         # ForexFactory calendar filter
├── trade_logger/
│   ├── __init__.py
│   └── logger.py              # CSV logging with outcome tracking
├── Dashboard/
│   └── index.html             # Live web dashboard
├── server.py                  # Flask API for dashboard
├── logs/                      # Trade/signal CSV logs
├── .env / .env.example
├── start_all.bat / stop_all.bat
```

## 3) Module-by-Module Breakdown

### `config/settings.py`

Central configuration hub:
- Loads all environment variables with safe defaults
- Defines per-symbol settings (pip values, spread limits, lot limits, session hours)
- FVG minimum size thresholds, POI buffer distances
- Position management parameters (breakeven trigger, trailing step)
- Correlated pairs mapping

### `core/mt5_client.py`

All MetaTrader 5 interactions:
- `connect_mt5()` / `ensure_connected()`: connection with auto-retry and reconnection
- `resolve_all_symbols()`: maps base symbols to broker-specific names
- `get_candles()`: OHLCV data retrieval
- `get_spread()`, `get_current_price()`, `has_open_position()`
- `calculate_atr()`: Average True Range for SL/TP validation
- `get_volume_profile()`: volume vs 20-period average
- `calculate_lot_size()`: risk-based dynamic sizing (RISK_PCT% of balance)
- `place_order()`: market order with multi-fill-mode fallback
- `modify_position_sl()`: SL modification for breakeven/trailing

### `core/position_manager.py`

Manages open trades (crucial for profit protection):
- `register_trade()`: tracks new positions
- `manage_all()`: called every loop iteration
  - **Breakeven**: moves SL to entry + buffer when profit ≥ 1R
  - **Trailing**: trails SL at 50% of risk distance behind price when profit ≥ 1.5R
  - **Outcome detection**: finds closed positions, looks up real P&L from MT5 deal history, updates trade logs with WIN/LOSS

### `analysis/smc.py`

Smart Money Concepts detection:
- **Order Blocks**: detects bullish/bearish OBs with impulse-strength scoring, checks mitigation via subsequent price action
- **FVGs**: detects with **minimum size filter** (3 pips forex, 30 pips gold), fill status checked against subsequent candle wicks (not just current price)
- **BOS/CHOCH**: swing-based market structure using 2-bar lookback for accuracy
- `get_structure_bias()`: extracts BULLISH/BEARISH/NEUTRAL from structure text

### `analysis/patterns.py`

11 candlestick/structure patterns:
Bullish/Bearish Engulfing, Pin Bar (Hammer/Shooting Star), Doji, Morning/Evening Star, Three White Soldiers/Black Crows, Double Top/Bottom

### `analysis/support_resistance.py`

Swing-based S/R detection:
- Finds swing highs/lows with configurable window
- Clusters nearby levels (within 10 pips) and counts touches
- Returns strongest resistance above price and support below

### `analysis/liquidity.py`

Core SMC liquidity concepts:
- **Sweep detection**: identifies candles that wick beyond a swing level then close back (stop hunts)
- **Equal highs/lows**: finds liquidity pools where resting orders accumulate
- Both formatted into Claude's prompt for decision context

### `analysis/trend.py`

Higher-timeframe trend filter (H4):
- EMA 20/50 crossover
- Swing structure (HH+HL = bullish, LH+LL = bearish)
- Combined scoring: 3/3 = STRONG, 2/3 = MODERATE
- `is_trade_aligned_with_trend()`: blocks trades against strong trends

### `ai/claude_analyst.py`

AI decision engine:
- `_build_prompt()`: builds comprehensive prompt with ALL analysis data (SMC, patterns, S/R, liquidity, HTF trend, volume, ATR, performance history)
- `_parse_claude_json()`: robust JSON extraction (handles markdown wrapping, embedded text)
- `validate_trade_decision()`: checks SL/TP sides, entry proximity, actual R:R
- `validate_sl_tp_with_atr()`: ensures SL within 0.3–3.0× ATR range
- Forces entry to current price (prevents AI hallucination of old prices)

### `risk/manager.py`

Risk management:
- Daily trade count limit
- Daily loss limits (both USD and percentage)
- **Consecutive loss streak tracker** — auto-pauses after 3 losses
- **Correlation check** — blocks same-direction trades on EURUSD + GBPUSD

### `risk/news_filter.py`

News event filter:
- Fetches ForexFactory calendar (cached hourly)
- Maps event currencies to trading symbols
- Blocks trading 30 min before and 10 min after high/medium-impact events

### `trade_logger/logger.py`

CSV trade logging with outcome tracking:
- Logs all AI signals (including HOLD) to `logs/signals.csv`
- Logs all executed trades with ticket numbers to `logs/trades.csv`
- `update_trade_status()`: updates by ticket when position closes
- `get_recent_performance()`: retrieves win/loss stats for AI feedback
- Log rotation on bot restart (previous logs moved to `logs/history/`)

### `server.py` (Dashboard API)

Flask API serving dashboard data — reads from same CSV format, unchanged.

## 4) Trade Execution Flow

```
1. main.py starts → validates env → connects MT5 → resolves symbols
2. Main loop begins:
   a. ensure_connected() — auto-reconnect if MT5 drops
   b. position_manager.manage_all() — breakeven/trail/outcome tracking
   c. Session check (London/NY hours only)
   d. Daily limits + streak pause check
   e. For each symbol:
      ├── Per-symbol daily limit check
      ├── Cooldown check (10 min normal, 30 min after loss)
      ├── Optimal session check (per-symbol)
      ├── Open position check
      ├── Spread check
      ├── News filter check
      ├── Fetch M15 + H1 + H4 candles
      ├── Run ALL analyses:
      │   ├── SMC: Order Blocks, FVGs, BOS/CHOCH
      │   ├── Patterns: 11 candlestick patterns
      │   ├── S/R: swing-based support/resistance
      │   ├── Liquidity: sweeps + equal H/L
      │   ├── Trend: H4 EMA + structure
      │   └── Volume + ATR
      ├── Pre-filter: skip API if no POI/patterns/sweeps
      ├── Call Claude with full context
      ├── VALIDATION GATES:
      │   ├── Gate 1: SL/TP/RR present?
      │   ├── Gate 2: SL/TP on correct side? Entry near price? R:R ≥ 2?
      │   ├── Gate 3: SL/TP within ATR range?
      │   ├── Gate 4: H4 trend alignment?
      │   └── Gate 5: No correlated exposure?
      ├── Calculate lot size (risk-based)
      ├── Final risk cap check
      ├── Place order → register with position manager
      └── Log trade with ticket
   f. Sleep 60 seconds, repeat
```

## 5) SMC + Price Action Concepts Used

| Concept | Category | Module |
|---------|----------|--------|
| Order Blocks (bullish/bearish) | SMC | `analysis/smc.py` |
| Fair Value Gaps (with size filter) | SMC | `analysis/smc.py` |
| Break of Structure (BOS) | SMC | `analysis/smc.py` |
| Change of Character (CHOCH) | SMC | `analysis/smc.py` |
| Market Structure (HH/HL/LH/LL) | SMC | `analysis/smc.py` |
| Liquidity Sweeps (Stop Hunts) | SMC | `analysis/liquidity.py` |
| Equal Highs/Lows (Liquidity Pools) | SMC | `analysis/liquidity.py` |
| HTF Trend Direction | SMC | `analysis/trend.py` |
| Bullish/Bearish Engulfing | Price Action | `analysis/patterns.py` |
| Pin Bar (Hammer/Shooting Star) | Price Action | `analysis/patterns.py` |
| Doji | Price Action | `analysis/patterns.py` |
| Morning/Evening Star | Price Action | `analysis/patterns.py` |
| Three White Soldiers/Black Crows | Price Action | `analysis/patterns.py` |
| Double Top/Bottom | Price Action | `analysis/patterns.py` |
| Support/Resistance | Price Action | `analysis/support_resistance.py` |
| Volume Confirmation | Price Action | `core/mt5_client.py` |

## 6) Environment Variables

```env
# Required
MT5_LOGIN=12345678
MT5_PASSWORD=your_password
MT5_SERVER=YourBroker-Demo
ANTHROPIC_API_KEY=sk-ant-api03-xxx

# Optional — sessions
LONDON_SESSION_UTC=7-11
NY_SESSION_UTC=12-21

# Optional — risk
MAX_DAILY_LOSS_USD=100
MAX_RISK_PER_TRADE_USD=20
SYMBOL_COOLDOWN_MIN=10
LOSS_COOLDOWN_MIN=30
MAX_TRADES_PER_SYMBOL_PER_DAY=3
MAX_CONSECUTIVE_LOSSES=3

# Optional — news
NEWS_FILTER_ENABLED=true
NEWS_BLACKOUT_MINUTES=30

# Optional — MT5
MT5_INIT_RETRIES=5
MT5_INIT_RETRY_DELAY_SEC=3
# MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
```

## 7) How to Run

```bash
# Install dependencies
pip install flask flask-cors MetaTrader5 anthropic pandas python-dotenv requests

# Run bot
python main.py

# Run dashboard (separate terminal)
python server.py

# Or use the launcher
start_all.bat
```

Dashboard: http://localhost:5000
