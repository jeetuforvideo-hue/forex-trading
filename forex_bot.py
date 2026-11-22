"""
=============================================================================
SMC TRADING BOT (XAU/USD & BTC/USD)
=============================================================================
Key Features & Rules:
1. Pairs: XAU/USD (Gold) and BTC/USD (Bitcoin).
2. Strategy: Smart Money Concepts (SMC)
   - 1H Market Context / Bias (Bullish / Bearish / Choppy)
   - 15M Key Liquidity Sweep (PDH/PDL, London H/L, Asian H/L, 1H Swings)
   - 5M Market Structure Shift (MSS) with Strong Displacement Body
3. Risk Management:
   - Max 2 Trades Per Day
   - $30.00 Fixed Risk Per Trade
   - Max Daily Loss: $60.00 (or 2 consecutive losses -> Stop for the day)
   - 1:2 Risk to Reward (Risk $30 -> Target +$60)
   - 1:1 Breakeven Lock (+1R -> Move SL to Entry)
4. Execution: Alert & Signal Mode (Clean, Uncluttered, Ready-to-Execute)
5. Speed: Asynchronous Chart Generation (Non-blocking scanner)
6. 24/7 Bitcoin: BTC trades continuously 365 days, including Saturday & Sunday.
7. Crash-Proof: State saved to 'bot_state.json' (Restores trade count on restart)
=============================================================================
"""

import os
import io
import json
import time
import threading
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# ---------------------------------------------------------------------------
# Auto-Install Required Packages
# ---------------------------------------------------------------------------
REQUIRED_PACKAGES = ["requests", "numpy", "pandas", "yfinance", "matplotlib"]
for pkg in REQUIRED_PACKAGES:
    try:
        __import__(pkg)
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
        except Exception as e:
            print(f"Warning installing {pkg}: {e}")

import requests
import numpy as np
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")  # Non-GUI fast backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon

# ---------------------------------------------------------------------------
# Configuration & Credentials
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID   = os.getenv("CHAT_ID") or "5610520408"

# Risk Configuration
STARTING_BALANCE_USD   = 5000.0
RISK_PER_TRADE_USD     = 30.00    # Exact $30 risk per trade
MAX_DAILY_TRADES       = 2        # Exactly 2 trades per day
MAX_DAILY_LOSSES       = 2        # Stop after 2 losses
DAILY_STOP_LOSS_USD    = 60.00    # Max allowed daily loss
PRIMARY_RR_TARGET      = 2.00     # 1:2 RR (Target: +$60.00)
BREAKEVEN_RR_TRIGGER   = 1.00     # 1:1 RR (Move SL to Entry)

STATE_FILE_PATH        = "bot_state.json"
JOURNAL_CSV_PATH       = "smc_trade_journal.csv"

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Cache-Control": "no-cache"
})

TELEGRAM_SESSION = requests.Session()
TELEGRAM_SESSION.headers.update({
    "User-Agent": "TelegramBot/1.0",
    "Connection": "keep-alive"
})

# Dedicated thread pools for non-blocking operations
DATA_EXECUTOR  = ThreadPoolExecutor(max_workers=5, thread_name_prefix="DataWorker")
CHART_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ChartWorker")
STATE_LOCK     = threading.Lock()

# ---------------------------------------------------------------------------
# Pairs Setup (XAU/USD & BTC/USD Only)
# ---------------------------------------------------------------------------
PAIRS_CONFIG = {
    "XAU/USD": {
        "symbol": "XAUUSD",
        "display_name": "Gold / U.S. Dollar (XAU/USD)",
        "cmd_keys": ["/gold", "gold", "/xauusd", "xauusd"],
        "tv_tickers": ["OANDA:XAUUSD", "FOREXCOM:XAUUSD", "FX_IDC:XAUUSD"],
        "tv_screener": "cfd",
        "binance_symbol": "PAXGUSDT",
        "yf_symbol": "GC=F",
        "decimals": 2,
        "spread_estimate": 0.35,          # $0.35 spread estimate
        "contract_multiplier": 100.0,      # 1 Lot = 100 oz ($1 move = $100 per lot)
        "min_lot": 0.01,
        "max_lot": 1.00,
        "min_sl_dist": 2.50,               # Minimum logical SL in points
        "max_sl_dist": 15.00,              # Maximum logical SL to prevent low RR
        "is_crypto": False                 # Respects Forex market hours (Closed weekends)
    },
    "BTC/USD": {
        "symbol": "BTCUSD",
        "display_name": "Bitcoin / U.S. Dollar (BTC/USD)",
        "cmd_keys": ["/btc", "btc", "/btcusd", "btcusd"],
        "tv_tickers": ["BITSTAMP:BTCUSD", "COINBASE:BTCUSD", "BINANCE:BTCUSDT"],
        "tv_screener": "crypto",
        "binance_symbol": "BTCUSDT",
        "yf_symbol": "BTC-USD",
        "decimals": 2,
        "spread_estimate": 25.0,           # $25 spread estimate
        "contract_multiplier": 1.0,        # Standard CFD: 1 Lot = 1 BTC ($1 move = $1 per lot)
        "min_lot": 0.01,
        "max_lot": 2.00,
        "min_sl_dist": 150.0,              # Minimum logical SL in points
        "max_sl_dist": 1500.0,             # Maximum logical SL
        "is_crypto": True                  # 24/7 365 Days Active (TRADES ON WEEKENDS)
    }
}

# ---------------------------------------------------------------------------
# Time Helpers
# ---------------------------------------------------------------------------
def get_utc_now():
    return datetime.now(timezone.utc)

def get_ist_now():
    return get_utc_now() + timedelta(hours=5, minutes=30)

def to_utc_timestamp(ts):
    if ts is None:
        return get_utc_now()
    if getattr(ts, "tzinfo", None) is not None:
        return ts.tz_convert(timezone.utc)
    return ts.replace(tzinfo=timezone.utc)

def is_forex_market_open():
    """Checks if standard Forex/Gold market is open (Closed weekends)."""
    now_ist = get_ist_now()
    wd = now_ist.weekday()
    # Closes Saturday ~3:30 AM IST, Reopens Monday ~3:30 AM IST
    if wd == 5 and (now_ist.hour + now_ist.minute / 60.0) >= 3.5:
        return False
    if wd == 6:
        return False
    if wd == 0 and (now_ist.hour + now_ist.minute / 60.0) < 3.5:
        return False
    return True

# ---------------------------------------------------------------------------
# Persistent State Manager (Crash-Proof Engine)
# ---------------------------------------------------------------------------
class StateManager:
    def __init__(self, filepath=STATE_FILE_PATH):
        self.filepath = filepath
        self.state = self.load_state()

    def load_state(self):
        today_str = str(get_ist_now().date())
        default_state = {
            "date": today_str,
            "trades_today": 0,
            "losses_today": 0,
            "daily_realized_pnl": 0.0,
            "open_trades": [],
            "daily_halt": False
        }
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today_str:
                        return data
                    else:
                        # New day started
                        default_state["date"] = today_str
                        return default_state
            except Exception as e:
                print(f"[State Load Error]: {e}")
        return default_state

    def save_state(self):
        with STATE_LOCK:
            try:
                with open(self.filepath, "w", encoding="utf-8") as f:
                    json.dump(self.state, f, indent=2)
            except Exception as e:
                print(f"[State Save Error]: {e}")

    def check_new_day(self):
        today_str = str(get_ist_now().date())
        if self.state.get("date") != today_str:
            with STATE_LOCK:
                self.state["date"] = today_str
                self.state["trades_today"] = 0
                self.state["losses_today"] = 0
                self.state["daily_realized_pnl"] = 0.0
                self.state["daily_halt"] = False
                self.save_state()
            send_telegram_message(
                f"🌅 <b>NEW TRADING DAY INITIALIZED ({today_str})</b>\n"
                f"• Daily Limits: <b>Max 2 Trades</b> | <b>$30 Risk per Trade</b>\n"
                f"• Pair Status: <b>XAUUSD</b> (Forex) | <b>BTCUSD</b> (24/7 Active)"
            )

    def can_trade_today(self):
        self.check_new_day()
        if self.state["trades_today"] >= MAX_DAILY_TRADES:
            return False, f"Max {MAX_DAILY_TRADES} trades completed for today"
        if self.state["losses_today"] >= MAX_DAILY_LOSSES:
            return False, f"Max {MAX_DAILY_LOSSES} losses hit today (-${abs(self.state['daily_realized_pnl']):.2f})"
        if self.state["daily_realized_pnl"] <= -DAILY_STOP_LOSS_USD:
            return False, f"Daily stop loss (-${DAILY_STOP_LOSS_USD}) reached"
        return True, "Ready"

state_mgr = StateManager()

# Runtime Memory for fast duplicate prevention
last_candle_traded   = {name: None for name in PAIRS_CONFIG}
pair_cooldown_until  = {name: datetime.min.replace(tzinfo=timezone.utc) for name in PAIRS_CONFIG}
ohlc_cache           = {}

# ---------------------------------------------------------------------------
# Telegram Messenger
# ---------------------------------------------------------------------------
def send_telegram_message(text, target_chat_id=CHAT_ID):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print(f"\n[TELEGRAM PREVIEW]:\n{text}\n")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": text, "parse_mode": "HTML"}
    try:
        TELEGRAM_SESSION.post(url, json=payload, timeout=4.0)
    except Exception as e:
        print(f"Telegram Msg Error: {e}")

def send_telegram_photo(photo_bytes, caption_html="", target_chat_id=CHAT_ID):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print(f"\n[PHOTO DISPATCHED WITH CAPTION]:\n{caption_html}\n")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
    files = {"photo": ("chart.png", photo_bytes, "image/png")}
    data = {"chat_id": target_chat_id, "caption": caption_html[:1024], "parse_mode": "HTML"}
    try:
        res = TELEGRAM_SESSION.post(url, data=data, files=files, timeout=8.0)
        if res.status_code != 200 and len(caption_html) > 1024:
            # Fallback if caption exceeded limit
            TELEGRAM_SESSION.post(url, data={"chat_id": target_chat_id}, files=files, timeout=8.0)
            send_telegram_message(caption_html, target_chat_id)
    except Exception as e:
        print(f"Telegram Photo Error: {e}")
        send_telegram_message(caption_html, target_chat_id)

# ---------------------------------------------------------------------------
# Market Data Engine (Fast, Multi-Source & Cached)
# ---------------------------------------------------------------------------
def fetch_binance_ohlc(symbol, interval="5m", limit=180):
    """High-speed official Binance API for 24/7 crypto and gold fallback."""
    for base in ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]:
        try:
            r = HTTP_SESSION.get(base, params={"symbol": symbol, "interval": interval, "limit": limit}, timeout=2.5)
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list) and len(data) >= 30:
                    df = pd.DataFrame(data, columns=[
                        "open_time", "Open", "High", "Low", "Close", "Volume",
                        "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"
                    ])
                    df["Date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                    df.set_index("Date", inplace=True)
                    for c in ["Open", "High", "Low", "Close", "Volume"]:
                        df[c] = df[c].astype(float)
                    return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            continue
    return None

def fetch_tradingview_spot_and_5m(tv_tickers, screener="cfd"):
    """Fetches real-time CFD tick data from TradingView scanner."""
    if isinstance(tv_tickers, str):
        tv_tickers = [tv_tickers]
    url = f"https://scanner.tradingview.com/{screener}/scan"
    cols = ["close", "open|5", "high|5", "low|5", "close|5"]
    payload = {"symbols": {"tickers": tv_tickers, "query": {"types": []}}, "columns": cols}
    try:
        res = HTTP_SESSION.post(url, json=payload, timeout=2.0)
        if res.status_code == 200:
            for item in res.json().get("data", []):
                d = item.get("d", [])
                if d and len(d) >= 5 and d[0] is not None:
                    spot = float(d[0])
                    return {
                        "spot": spot,
                        "o5": float(d[1]) if d[1] is not None else spot,
                        "h5": float(d[2]) if d[2] is not None else spot,
                        "l5": float(d[3]) if d[3] is not None else spot,
                        "c5": float(d[4]) if d[4] is not None else spot
                    }
    except Exception:
        pass
    return None

def sanitize_df(df, min_bars=30):
    if df is None or df.empty:
        return None
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df[~df.index.duplicated(keep="last")]
    for col in ["Open", "High", "Low", "Close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    if len(df) < min_bars:
        return None
    return df

def get_3tf_data(pair_name, info):
    """Fetches 1H, 15M, and 5M data with real-time spot calibration."""
    now_ts = time.time()
    cache_entry = ohlc_cache.get(pair_name)
    if cache_entry and (now_ts - cache_entry["time"]) < 20.0:
        return cache_entry["1h"], cache_entry["15m"], cache_entry["5m"], cache_entry["spot"]

    df_1h, df_15m, df_5m = None, None, None
    live_spot = None

    if info.get("is_crypto"):
        # Instant official Binance feeds for BTC
        sym = info["binance_symbol"]
        f_1h  = DATA_EXECUTOR.submit(fetch_binance_ohlc, sym, "1h", 120)
        f_15m = DATA_EXECUTOR.submit(fetch_binance_ohlc, sym, "15m", 150)
        f_5m  = DATA_EXECUTOR.submit(fetch_binance_ohlc, sym, "5m", 150)
        df_1h, df_15m, df_5m = f_1h.result(), f_15m.result(), f_5m.result()
        if df_5m is not None and not df_5m.empty:
            live_spot = float(df_5m.iloc[-1]["Close"])
    else:
        # Multi-source for Gold
        tv_future = DATA_EXECUTOR.submit(fetch_tradingview_spot_and_5m, info["tv_tickers"], info["tv_screener"])
        try:
            tkr = yf.Ticker(info["yf_symbol"])
            df_1h  = sanitize_df(tkr.history(period="10d", interval="1h", auto_adjust=False))
            df_15m = sanitize_df(tkr.history(period="5d", interval="15m", auto_adjust=False))
            df_5m  = sanitize_df(tkr.history(period="2d", interval="5m", auto_adjust=False))
        except Exception:
            pass

        # Fallback to PAXGUSDT if Yahoo is slow
        if df_1h is None or df_15m is None or df_5m is None:
            sym = info["binance_symbol"]
            df_1h  = fetch_binance_ohlc(sym, "1h", 120)
            df_15m = fetch_binance_ohlc(sym, "15m", 150)
            df_5m  = fetch_binance_ohlc(sym, "5m", 150)

        tv_data = tv_future.result()
        if tv_data and tv_data["spot"] > 0:
            live_spot = tv_data["spot"]
        elif df_5m is not None and not df_5m.empty:
            live_spot = float(df_5m.iloc[-1]["Close"])

    if df_1h is None or df_15m is None or df_5m is None or live_spot is None:
        return None, None, None, None

    # Calibrate latest bar with spot
    last_idx = df_5m.index[-1]
    df_5m.loc[last_idx, "Close"] = live_spot
    df_5m.loc[last_idx, "High"]  = max(float(df_5m.loc[last_idx, "High"]), live_spot)
    df_5m.loc[last_idx, "Low"]   = min(float(df_5m.loc[last_idx, "Low"]), live_spot)

    ohlc_cache[pair_name] = {
        "time": now_ts,
        "1h": df_1h, "15m": df_15m, "5m": df_5m, "spot": live_spot
    }
    return df_1h, df_15m, df_5m, live_spot

# ---------------------------------------------------------------------------
# SMC Indicator & Structure Utilities
# ---------------------------------------------------------------------------
def enrich_indicators(df):
    df = df.copy()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    hl = df["High"] - df["Low"]
    hc = np.abs(df["High"] - df["Close"].shift())
    lc = np.abs(df["Low"] - df["Close"].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean().bfill()
    df["Body"] = np.abs(df["Close"] - df["Open"])
    df["Range"] = (df["High"] - df["Low"]).replace(0, 1e-9)
    df["Body_Ratio"] = df["Body"] / df["Range"]
    return df

def get_smc_levels_and_1h_bias(df_1h, df_15m, decimals):
    """
    Extracts 1H Higher Timeframe Bias and Key Liquidity Pools:
    - PDH (Previous Day High), PDL (Previous Day Low)
    - Asian High / Low
    - London High / Low
    - 1H Swing Highs & Lows (Equal Highs / Equal Lows)
    """
    closed_1h = df_1h.iloc[:-1] if len(df_1h) > 20 else df_1h
    atr_1h = float(closed_1h.iloc[-1]["ATR"])
    c_last = closed_1h.iloc[-1]
    h1_close = float(c_last["Close"])
    h1_ema20 = float(c_last["EMA20"])
    h1_ema50 = float(c_last["EMA50"])

    # 1H Structure Swings
    highs = closed_1h["High"].values
    lows  = closed_1h["Low"].values
    swing_highs, swing_lows = [], []
    for i in range(2, len(closed_1h) - 1):
        if highs[i] >= max(highs[i-2:i]) and highs[i] >= highs[i+1]:
            swing_highs.append(round(float(highs[i]), decimals))
        if lows[i] <= min(lows[i-2:i]) and lows[i] <= lows[i+1]:
            swing_lows.append(round(float(lows[i]), decimals))

    # Determine 1H Trend
    if h1_close > h1_ema20 and h1_ema20 >= h1_ema50:
        h1_bias = "BULLISH"
    elif h1_close < h1_ema20 and h1_ema20 <= h1_ema50:
        h1_bias = "BEARISH"
    else:
        # Check swing progression
        if swing_highs and swing_lows and h1_close >= swing_lows[-1]:
            h1_bias = "BULLISH"
        elif swing_highs and swing_lows and h1_close <= swing_highs[-1]:
            h1_bias = "BEARISH"
        else:
            h1_bias = "RANGING"

    # 15M Session Liquidity Pools
    df_15m_utc = df_15m.copy()
    if isinstance(df_15m_utc.index, pd.DatetimeIndex):
        utc_idx = df_15m_utc.index.tz_convert("UTC") if df_15m_utc.index.tz else df_15m_utc.index.tz_localize("UTC")
        df_15m_utc["utc_date"] = utc_idx.date
        df_15m_utc["utc_hour"] = utc_idx.hour + (utc_idx.minute / 60.0)

    today_utc = df_15m_utc["utc_date"].iloc[-1]
    unique_dates = sorted(pd.unique(df_15m_utc["utc_date"]))
    prev_date = unique_dates[-2] if len(unique_dates) >= 2 else today_utc

    prev_day_bars = df_15m_utc[df_15m_utc["utc_date"] == prev_date]
    pdh = round(float(prev_day_bars["High"].max()), decimals) if not prev_day_bars.empty else float(closed_1h["High"].max())
    pdl = round(float(prev_day_bars["Low"].min()), decimals) if not prev_day_bars.empty else float(closed_1h["Low"].min())

    today_bars  = df_15m_utc[df_15m_utc["utc_date"] == today_utc]
    asian_bars  = today_bars[(today_bars["utc_hour"] >= 0.0) & (today_bars["utc_hour"] < 7.0)]
    london_bars = today_bars[(today_bars["utc_hour"] >= 7.0) & (today_bars["utc_hour"] < 13.0)]

    asian_high  = round(float(asian_bars["High"].max()), decimals) if not asian_bars.empty else pdh
    asian_low   = round(float(asian_bars["Low"].min()), decimals)  if not asian_bars.empty else pdl
    london_high = round(float(london_bars["High"].max()), decimals) if not london_bars.empty else pdh
    london_low  = round(float(london_bars["Low"].min()), decimals)  if not london_bars.empty else pdl

    levels = [
        {"name": "Previous Day High", "short": "PDH", "price": pdh, "type": "BUY_SIDE_LIQUIDITY"},
        {"name": "Previous Day Low",  "short": "PDL", "price": pdl, "type": "SELL_SIDE_LIQUIDITY"},
        {"name": "London Session High", "short": "LON H", "price": london_high, "type": "BUY_SIDE_LIQUIDITY"},
        {"name": "London Session Low",  "short": "LON L", "price": london_low,  "type": "SELL_SIDE_LIQUIDITY"},
        {"name": "Asian Session High",  "short": "ASIA H", "price": asian_high, "type": "BUY_SIDE_LIQUIDITY"},
        {"name": "Asian Session Low",   "short": "ASIA L", "price": asian_low,  "type": "SELL_SIDE_LIQUIDITY"}
    ]

    if swing_highs:
        levels.append({"name": "1H Swing High", "short": "1H High", "price": swing_highs[-1], "type": "BUY_SIDE_LIQUIDITY"})
    if swing_lows:
        levels.append({"name": "1H Swing Low", "short": "1H Low", "price": swing_lows[-1], "type": "SELL_SIDE_LIQUIDITY"})

    return {
        "h1_bias": h1_bias,
        "atr_1h": atr_1h,
        "pdh": pdh, "pdl": pdl,
        "london_high": london_high, "london_low": london_low,
        "asian_high": asian_high, "asian_low": asian_low,
        "levels": levels
    }

# ---------------------------------------------------------------------------
# Position Sizing: Exactly $30.00 Risk Calculation
# ---------------------------------------------------------------------------
def calculate_lot_size(pair_name, info, sl_dist):
    """
    Calculates exact lot size to risk precisely $30.00:
    - Gold (XAUUSD): 1 lot = 100 oz. $1 move = $100 per 1.0 lot.
      Formula: Lot = 30.00 / ((SL Distance + Spread) * 100)
    - Bitcoin (BTCUSD): 1 lot = 1 BTC. $1 move = $1 per 1.0 lot.
      Formula: Lot = 30.00 / ((SL Distance + Spread) * 1.0)
    """
    if sl_dist <= 0:
        return None, 0.0

    spread = info.get("spread_estimate", 0.0)
    effective_dist = sl_dist + spread
    mult = info.get("contract_multiplier", 100.0)

    loss_per_1_lot = effective_dist * mult
    if loss_per_1_lot <= 0:
        return None, 0.0

    raw_lot = RISK_PER_TRADE_USD / loss_per_1_lot
    min_lot = info.get("min_lot", 0.01)
    max_lot = info.get("max_lot", 1.00)

    # Round to 2 decimal places
    lot_size = max(min_lot, min(round(raw_lot, 2), max_lot))
    actual_risk = round(lot_size * loss_per_1_lot, 2)

    # If rounding pushed risk > $30.50, step down one micro lot if possible
    if actual_risk > (RISK_PER_TRADE_USD + 0.75) and lot_size > min_lot:
        lot_size = round(lot_size - 0.01, 2)
        actual_risk = round(lot_size * loss_per_1_lot, 2)

    return lot_size, actual_risk

# ---------------------------------------------------------------------------
# SMC Strategy Engine (15M Liquidity Sweep + 5M MSS with Displacement)
# ---------------------------------------------------------------------------
def evaluate_smc_setup(pair_name, info, df_1h, df_15m, df_5m, live_spot):
    """
    Smart Money Concepts (SMC) Setup Rules:
    1. 1H Bias Alignment (Only BUY in Bullish, only SELL in Bearish).
    2. 15M Liquidity Sweep: Price wicks through a key level (PDH/PDL/Session H/L)
       taking buy-stops/sell-stops, but closes back inside.
    3. 5M Market Structure Shift (MSS):
       - Bullish: 5M candle body closes ABOVE recent swing high with strong body (>50% body ratio).
       - Bearish: 5M candle body closes BELOW recent swing low with strong body (>50% body ratio).
    4. SL Placement: At the absolute extreme of the sweep wick (Invalidation level).
    5. TP Placement: 1:2 Risk to Reward (+ $60.00 profit).
    """
    dec = info["decimals"]
    smc_ctx = get_smc_levels_and_1h_bias(df_1h, df_15m, dec)
    h1_bias = smc_ctx["h1_bias"]

    if h1_bias == "RANGING":
        return None

    m5_c0  = df_5m.iloc[-2]  # Last closed 5M candle
    atr_5m = float(m5_c0["ATR"])
    body_r = float(m5_c0["Body_Ratio"])
    p_close = float(m5_c0["Close"])
    p_open  = float(m5_c0["Open"])

    if np.isnan(atr_5m) or atr_5m <= 0 or body_r < 0.45:
        return None  # Candle lacked displacement body

    m15_recent = df_15m.iloc[-6:-1]
    m5_prev3   = df_5m.iloc[-5:-2]

    candidates = []

    for lv in smc_ctx["levels"]:
        lvl_price = lv["price"]
        lvl_short = lv["short"]
        lvl_type  = lv["type"]

        # -------------------------------------------------------------------
        # BUY SETUP: Bullish 1H + Sell-Side Liquidity Swept on 15M + 5M MSS
        # -------------------------------------------------------------------
        if h1_bias == "BULLISH" and lvl_type == "SELL_SIDE_LIQUIDITY":
            m15_min_low = float(m15_recent["Low"].min())
            m15_last_c  = float(df_15m.iloc[-2]["Close"])

            # Swept below level and closed back above
            swept = (m15_min_low <= lvl_price) and (m15_last_c >= lvl_price - (atr_5m * 0.15))

            if swept:
                # 5M MSS: Body close above previous swing high
                neck_high = float(m5_prev3["High"].max())
                sweep_low = min(float(df_5m.iloc[-8:-1]["Low"].min()), m15_min_low)

                mss_confirmed = (
                    (p_close > neck_high) and
                    (p_close > p_open) and
                    (body_r >= 0.48) and
                    (live_spot >= lvl_price)
                )

                if mss_confirmed:
                    entry = round(p_close, dec)
                    # Logical SL: Just below sweep wick low with small buffer
                    sl = round(sweep_low - (atr_5m * 0.18), dec)
                    sl_dist = round(entry - sl, dec)

                    if info["min_sl_dist"] <= sl_dist <= info["max_sl_dist"]:
                        lot_size, actual_risk = calculate_lot_size(pair_name, info, sl_dist)
                        if lot_size is not None:
                            be_level = round(entry + sl_dist, dec)
                            tp_level = round(entry + (sl_dist * PRIMARY_RR_TARGET), dec)
                            candidates.append({
                                "direction": "BUY",
                                "action": "BUY (LONG) 🟢",
                                "h1_bias": h1_bias,
                                "swept_level": f"{lvl_short} ({lvl_price})",
                                "entry": entry,
                                "sl": sl,
                                "sl_dist": sl_dist,
                                "be_level": be_level,
                                "tp": tp_level,
                                "tp_dist": round(sl_dist * PRIMARY_RR_TARGET, dec),
                                "lot_size": lot_size,
                                "risk_usd": actual_risk,
                                "reward_usd": round(actual_risk * PRIMARY_RR_TARGET, 2),
                                "smc_notes": [
                                    f"1H Order Flow: {h1_bias}",
                                    f"Liquidity Sweep: {lvl_short} Swept & Rejected",
                                    "5M Trigger: Market Structure Shift (MSS) with Strong Body"
                                ]
                            })

        # -------------------------------------------------------------------
        # SELL SETUP: Bearish 1H + Buy-Side Liquidity Swept on 15M + 5M MSS
        # -------------------------------------------------------------------
        if h1_bias == "BEARISH" and lvl_type == "BUY_SIDE_LIQUIDITY":
            m15_max_high = float(m15_recent["High"].max())
            m15_last_c   = float(df_15m.iloc[-2]["Close"])

            # Swept above level and closed back below
            swept = (m15_max_high >= lvl_price) and (m15_last_c <= lvl_price + (atr_5m * 0.15))

            if swept:
                # 5M MSS: Body close below previous swing low
                neck_low = float(m5_prev3["Low"].min())
                sweep_high = max(float(df_5m.iloc[-8:-1]["High"].max()), m15_max_high)

                mss_confirmed = (
                    (p_close < neck_low) and
                    (p_close < p_open) and
                    (body_r >= 0.48) and
                    (live_spot <= lvl_price)
                )

                if mss_confirmed:
                    entry = round(p_close, dec)
                    # Logical SL: Just above sweep wick high with small buffer
                    sl = round(sweep_high + (atr_5m * 0.18), dec)
                    sl_dist = round(sl - entry, dec)

                    if info["min_sl_dist"] <= sl_dist <= info["max_sl_dist"]:
                        lot_size, actual_risk = calculate_lot_size(pair_name, info, sl_dist)
                        if lot_size is not None:
                            be_level = round(entry - sl_dist, dec)
                            tp_level = round(entry - (sl_dist * PRIMARY_RR_TARGET), dec)
                            candidates.append({
                                "direction": "SELL",
                                "action": "SELL (SHORT) 🔴",
                                "h1_bias": h1_bias,
                                "swept_level": f"{lvl_short} ({lvl_price})",
                                "entry": entry,
                                "sl": sl,
                                "sl_dist": sl_dist,
                                "be_level": be_level,
                                "tp": tp_level,
                                "tp_dist": round(sl_dist * PRIMARY_RR_TARGET, dec),
                                "lot_size": lot_size,
                                "risk_usd": actual_risk,
                                "reward_usd": round(actual_risk * PRIMARY_RR_TARGET, 2),
                                "smc_notes": [
                                    f"1H Order Flow: {h1_bias}",
                                    f"Liquidity Sweep: {lvl_short} Swept & Rejected",
                                    "5M Trigger: Market Structure Shift (MSS) with Strong Body"
                                ]
                            })

    return candidates[0] if candidates else None

# ---------------------------------------------------------------------------
# High-Speed Chart Generator (TradingView Style, Non-Blocking)
# ---------------------------------------------------------------------------
def generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=None):
    """Generates a clean, crisp, professional chart showing Entry, SL, TP, and SMC Levels."""
    try:
        sub = df_5m.iloc[-45:].copy()
        if len(sub) < 15:
            return None

        dec = info["decimals"]
        sym = info["symbol"]
        smc_ctx = get_smc_levels_and_1h_bias(df_1h, df_15m, dec)

        fig = plt.figure(figsize=(9.2, 5.0), dpi=95, facecolor="#ffffff")
        ax = fig.add_axes([0.02, 0.08, 0.88, 0.83], facecolor="#ffffff")

        n_bars = len(sub)
        x_vals = np.arange(n_bars)
        right_pad = n_bars + 14

        ax.grid(True, linestyle="-", linewidth=0.5, color="#f1f5f9")
        for s in ["top", "left"]:
            ax.spines[s].set_visible(False)
        ax.spines["right"].set_color("#cbd5e1")
        ax.spines["bottom"].set_color("#cbd5e1")

        opens  = sub["Open"].values.astype(float)
        highs  = sub["High"].values.astype(float)
        lows   = sub["Low"].values.astype(float)
        closes = sub["Close"].values.astype(float)

        c_min, c_max = float(np.min(lows)), float(np.max(highs))
        y_span = max(c_max - c_min, 1e-4)

        # Plot Candles
        bulls = closes >= opens
        bears = ~bulls
        ax.vlines(x_vals[bulls], lows[bulls], highs[bulls], color="#089981", linewidth=1.2, zorder=3)
        ax.vlines(x_vals[bears], lows[bears], highs[bears], color="#f23645", linewidth=1.2, zorder=3)
        ax.bar(x_vals[bulls], np.abs(closes[bulls] - opens[bulls]), bottom=np.minimum(opens[bulls], closes[bulls]), width=0.65, color="#089981", edgecolor="#089981", zorder=3)
        ax.bar(x_vals[bears], np.abs(closes[bears] - opens[bears]), bottom=np.minimum(opens[bears], closes[bears]), width=0.65, color="#f23645", edgecolor="#f23645", zorder=3)

        # Plot Nearby SMC Levels
        for lv in smc_ctx["levels"]:
            p = lv["price"]
            if (c_min - y_span * 0.3) <= p <= (c_max + y_span * 0.3):
                col = "#b91c1c" if "BUY" in lv["type"] else "#047857"
                ax.axhline(p, color=col, linestyle="--", linewidth=0.8, alpha=0.65)
                ax.text(1, p + (y_span * 0.01), f"{lv['short']} ({p:.{dec}f})", color=col, fontsize=7.5, fontweight="bold")

        # Plot Active Trade Levels (Entry / SL / TP)
        if active_trade:
            ent = active_trade["entry"]
            sl  = active_trade["sl"]
            tp  = active_trade["tp"]
            be  = active_trade["be_level"]

            ax.axhline(ent, color="#2563eb", linestyle="--", linewidth=1.4, label="Entry")
            ax.axhline(sl,  color="#dc2626", linestyle="-",  linewidth=1.4, label="Stop Loss")
            ax.axhline(tp,  color="#16a34a", linestyle="-",  linewidth=1.4, label="Take Profit")
            ax.axhline(be,  color="#089981", linestyle=":",  linewidth=1.0, label="1:1 BE Lock")

            # Entry Marker Tag
            ax.text(
                right_pad - 1, ent, f" ENTRY: {ent:.{dec}f} ",
                color="#ffffff", fontsize=8.0, fontweight="bold", va="center", ha="right",
                bbox=dict(facecolor="#2563eb", edgecolor="none", pad=2.5)
            )
            ax.text(
                right_pad - 1, sl, f" SL: {sl:.{dec}f} ",
                color="#ffffff", fontsize=8.0, fontweight="bold", va="center", ha="right",
                bbox=dict(facecolor="#dc2626", edgecolor="none", pad=2.5)
            )
            ax.text(
                right_pad - 1, tp, f" 1:2 TP: {tp:.{dec}f} ",
                color="#ffffff", fontsize=8.0, fontweight="bold", va="center", ha="right",
                bbox=dict(facecolor="#16a34a", edgecolor="none", pad=2.5)
            )

        # Live Spot Tag
        ax.axhline(live_spot, color="#475569", linestyle=":", linewidth=0.9)
        ax.text(
            right_pad - 1, live_spot, f" SPOT: {live_spot:.{dec}f} ",
            color="#ffffff", fontsize=7.5, fontweight="bold", va="center", ha="right",
            bbox=dict(facecolor="#475569", edgecolor="none", pad=2.0)
        )

        ax.set_xlim(-0.8, right_pad)
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", labelsize=8.0)
        ax.set_xticks([])

        # Header Title
        fig.text(0.02, 0.94, f"{sym} · 5M SMC Strategy Chart (1H: {smc_ctx['h1_bias']})", fontsize=10.0, fontweight="bold", color="#0f172a")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=95, facecolor="#ffffff")
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Chart Render Error: {e}")
        return None

def async_dispatch_trade_signal(trade, info, df_1h, df_15m, df_5m, live_spot):
    """Renders chart and sends Telegram alert asynchronously without blocking scanner."""
    def _worker():
        dec = info["decimals"]
        trade_num = state_mgr.state["trades_today"]

        # Ultra-Clean, Non-Cluttered Message Layout
        caption = (
            f"🚨 <b>SMC TRADE SIGNAL: {info['symbol']}</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 <b>ACTION:</b> <b>{trade['action']}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>ENTRY:</b> <code>{trade['entry']:.{dec}f}</code>  (Spot: <code>{live_spot:.{dec}f}</code>)\n"
            f"🛑 <b>STOP LOSS:</b> <code>{trade['sl']:.{dec}f}</code>  (Dist: {trade['sl_dist']:.{dec}f})\n"
            f"🎯 <b>TAKE PROFIT (1:2):</b> <code>{trade['tp']:.{dec}f}</code>  (Dist: {trade['tp_dist']:.{dec}f})\n"
            f"🛡️ <b>BREAKEVEN (1:1):</b> <code>{trade['be_level']:.{dec}f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 <b>LOT SIZE:</b> <b><code>{trade['lot_size']} Lot</code></b>\n"
            f"💵 <b>PLANNED RISK:</b> <b>${trade['risk_usd']:.2f}</b>\n"
            f"💰 <b>NET REWARD:</b> <b>+${trade['reward_usd']:.2f} (1:2 RR)</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 <b>SMC REASONING:</b>\n"
            f"1️⃣ {trade['smc_notes'][0]}\n"
            f"2️⃣ {trade['smc_notes'][1]}\n"
            f"3️⃣ {trade['smc_notes'][2]}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <i>Trade #{trade_num}/{MAX_DAILY_TRADES} for today. Set SL & TP in broker.</i>"
        )

        chart_buf = generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=trade)
        if chart_buf:
            send_telegram_photo(chart_buf.getvalue(), caption)
        else:
            send_telegram_message(caption)

    CHART_EXECUTOR.submit(_worker)

# ---------------------------------------------------------------------------
# Telegram Command Listener (Dedicated Background Thread)
# ---------------------------------------------------------------------------
def telegram_command_listener():
    """Runs independently so Telegram user queries receive immediate replies."""
    last_update_id = 0
    while True:
        try:
            if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
                time.sleep(3)
                continue

            url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
            params = {"offset": last_update_id + 1, "timeout": 10}
            res = TELEGRAM_SESSION.get(url, params=params, timeout=12.0)
            if res.status_code == 200:
                data = res.json()
                if data.get("ok"):
                    for item in data.get("result", []):
                        last_update_id = item["update_id"]
                        msg = item.get("message", {})
                        chat_id = str(msg.get("chat", {}).get("id", ""))
                        text = msg.get("text", "").strip().lower()

                        if chat_id != str(CHAT_ID):
                            continue

                        if text in ["/status", "status", "/start", "start"]:
                            can_tr, reason = state_mgr.can_trade_today()
                            st = state_mgr.state
                            msg_out = (
                                f"📊 <b>SMC BOT STATUS ({st['date']})</b>\n\n"
                                f"🚦 <b>Trading Status:</b> {'🟢 READY' if can_tr else '🔴 PAUSED (' + reason + ')'}\n"
                                f"🔢 <b>Trades Today:</b> {st['trades_today']}/{MAX_DAILY_TRADES}\n"
                                f"🛑 <b>Losses Today:</b> {st['losses_today']}/{MAX_DAILY_LOSSES}\n"
                                f"💵 <b>Risk Per Trade:</b> ${RISK_PER_TRADE_USD:.2f}\n"
                                f"📈 <b>Realized PnL:</b> ${st['daily_realized_pnl']:+.2f}\n\n"
                                f"• <b>BTC/USD:</b> 🟢 24/7 Active (Weekend Enabled)\n"
                                f"• <b>XAU/USD:</b> {'🟢 Market Open' if is_forex_market_open() else '🔴 Weekend Closed'}\n\n"
                                f"Commands: <code>/gold</code>, <code>/btc</code>, <code>/reset</code>"
                            )
                            send_telegram_message(msg_out)

                        elif text in ["/reset", "reset"]:
                            with STATE_LOCK:
                                state_mgr.state["trades_today"] = 0
                                state_mgr.state["losses_today"] = 0
                                state_mgr.state["daily_realized_pnl"] = 0.0
                                state_mgr.state["daily_halt"] = False
                                state_mgr.save_state()
                            send_telegram_message("✅ <b>Today's Trades & Loss Count Reset to 0!</b>")

                        elif text in ["/gold", "gold"]:
                            send_instant_chart("XAU/USD")

                        elif text in ["/btc", "btc"]:
                            send_instant_chart("BTC/USD")

        except Exception as e:
            time.sleep(2)

def send_instant_chart(pair_name):
    """Sends immediate snapshot chart for requested pair."""
    def _worker():
        info = PAIRS_CONFIG[pair_name]
        df_1h, df_15m, df_5m, live_spot = get_3tf_data(pair_name, info)
        if df_1h is None or df_15m is None or df_5m is None:
            send_telegram_message(f"⚠️ Data loading for {info['symbol']}, please retry in 5s.")
            return

        df_1h  = enrich_indicators(df_1h)
        df_15m = enrich_indicators(df_15m)
        df_5m  = enrich_indicators(df_5m)

        buf = generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot)
        caption = (
            f"📊 <b>{info['display_name']}</b>\n"
            f"📍 <b>Live Spot:</b> <code>{live_spot:.{info['decimals']}f}</code>\n"
            f"🕒 <b>Market Status:</b> {'🟢 24/7 Active' if info['is_crypto'] else ('🟢 Open' if is_forex_market_open() else '🔴 Weekend Closed')}"
        )
        if buf:
            send_telegram_photo(buf.getvalue(), caption)
        else:
            send_telegram_message(caption)

    CHART_EXECUTOR.submit(_worker)

# ---------------------------------------------------------------------------
# Main Scanner Loop
# ---------------------------------------------------------------------------
def run_scanner():
    print("=================================================================")
    print("SMC TRADING BOT ACTIVE (XAU/USD & BTC/USD)")
    print(f"Risk Per Trade: ${RISK_PER_TRADE_USD} | Max Daily Trades: {MAX_DAILY_TRADES}")
    print("BTC/USD: 24/7 Active (Saturday & Sunday Enabled)")
    print("=================================================================")

    # Start Telegram Listener Thread
    threading.Thread(target=telegram_command_listener, daemon=True, name="TelegramListener").start()

    send_telegram_message(
        f"⚡ <b>SMC TRADING BOT ONLINE</b> ⚡\n\n"
        f"• <b>Markets:</b> XAU/USD & BTC/USD\n"
        f"• <b>Bitcoin Mode:</b> 🟢 <b>24/7 Active (Saturday & Sunday Included)</b>\n"
        f"• <b>Risk Rule:</b> <b>${RISK_PER_TRADE_USD:.2f} Risk</b> per trade | Max <b>{MAX_DAILY_TRADES} trades</b>/day\n"
        f"• <b>Target:</b> 1:2 RR (+${RISK_PER_TRADE_USD * PRIMARY_RR_TARGET:.2f})\n"
        f"• <b>Strategy:</b> 1H Trend + 15M Sweep + 5M MSS\n\n"
        f"<i>Type /status, /gold, or /btc anytime for live charts.</i>"
    )

    last_scan = 0

    while True:
        try:
            now = time.time()
            if (now - last_scan) >= 8:
                last_scan = now
                state_mgr.check_new_day()

                can_trade, reason = state_mgr.can_trade_today()
                if not can_trade:
                    time.sleep(5)
                    continue

                for pair_name, info in PAIRS_CONFIG.items():
                    # Check Forex hours for Gold; BTC is ALWAYS checked (24/7 weekend)
                    if not info.get("is_crypto") and not is_forex_market_open():
                        continue

                    # Cooldown check
                    if get_utc_now() < pair_cooldown_until[pair_name]:
                        continue

                    df_1h, df_15m, df_5m, live_spot = get_3tf_data(pair_name, info)
                    if df_1h is None or df_15m is None or df_5m is None or live_spot is None:
                        continue

                    df_1h  = enrich_indicators(df_1h)
                    df_15m = enrich_indicators(df_15m)
                    df_5m  = enrich_indicators(df_5m)

                    closed_candle_time = to_utc_timestamp(df_5m.index[-2])
                    if last_candle_traded[pair_name] == closed_candle_time:
                        continue

                    setup = evaluate_smc_setup(pair_name, info, df_1h, df_15m, df_5m, live_spot)

                    if setup:
                        last_candle_traded[pair_name] = closed_candle_time
                        pair_cooldown_until[pair_name] = get_utc_now() + timedelta(minutes=25)

                        with STATE_LOCK:
                            state_mgr.state["trades_today"] += 1
                            state_mgr.state["open_trades"].append(setup)
                            state_mgr.save_state()

                        # Dispatch trade alert asynchronously
                        async_dispatch_trade_signal(setup, info, df_1h, df_15m, df_5m, live_spot)

            time.sleep(0.5)

        except KeyboardInterrupt:
            print("Bot stopped by user.")
            break
        except Exception as e:
            print(f"Scanner Exception: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run_scanner()
