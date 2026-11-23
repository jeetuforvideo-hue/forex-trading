"""
=============================================================================
SMC TRADING BOT (XAU/USD & BTC/USD) — PRO EDITION
=============================================================================
Key Features & Rules:
1. Pairs: XAU/USD (Gold) and BTC/USD (Bitcoin).
2. Strategy: Smart Money Concepts (SMC)
   - 1H Market Bias / Order Flow
   - 15M Key Liquidity Sweep (PDH/PDL, London H/L, Asian H/L, 1H Swings)
   - 5M Market Structure Shift (MSS) with Strong Displacement Body
3. Smart Early Alerts & Notifications:
   - ⏳ PRE-ALERT: Alerts when 15M sweep happens & 5M MSS is forming (Get ready!)
   - 🔔 SESSION POPUPS: Automatic alerts when London (12:30 PM IST) & New York (6:30 PM IST) start
   - 🎯 CONFIRMED SIGNAL: Dispatches trade with exact Entry, SL, 1:2 TP, and Lot Size
   - 🛡️ 1:1 BREAKEVEN ALERT: Moves SL to Entry when price reaches +$30 (+1R)
   - 🏆 EXIT NOTIFICATION: Instant alert when TP (+$60) or SL (-$30) is hit
4. TradingView Long/Short Position Charts:
   - Realistic, hand-annotated TradingView look
   - Green Profit Box & Red Risk Box (Long/Short Tool)
   - Swept wick annotated with '💧 Liquidity Swept'
   - Neckline annotated with '⚡ 5M MSS Break'
   - Live Spot price badge with candle countdown timer
5. Risk Management:
   - Exactly 2 trades per day
   - Exactly $30.00 planned loss per trade
   - 1:2 Risk to Reward (Risk $30 -> Gain +$60)
   - Max Daily Loss: $60.00 (or 2 consecutive losses -> Stop for the day)
6. 24/7 Bitcoin: BTC trades continuously 365 days (Saturday & Sunday Enabled).
7. Crash-Proof: Persistent State ('bot_state.json') restores trades across reboots.
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
            print(f"Package install notice ({pkg}): {e}")

import requests
import numpy as np
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")  # Fast Non-GUI backend
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon

# ---------------------------------------------------------------------------
# Configuration & Credentials
# ---------------------------------------------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID   = os.getenv("CHAT_ID") or "5610520408"

# Risk Parameters
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

DATA_EXECUTOR  = ThreadPoolExecutor(max_workers=5, thread_name_prefix="DataWorker")
CHART_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ChartWorker")
STATE_LOCK     = threading.Lock()

# ---------------------------------------------------------------------------
# Pairs Configuration (XAU/USD & BTC/USD Only)
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
        "spread_estimate": 0.35,
        "contract_multiplier": 100.0,  # 1 Lot = 100 oz ($1 move = $100 per lot)
        "min_lot": 0.01,
        "max_lot": 1.00,
        "min_sl_dist": 2.50,
        "max_sl_dist": 15.00,
        "is_crypto": False             # Respects Forex market hours
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
        "spread_estimate": 25.0,
        "contract_multiplier": 1.0,    # 1 Lot = 1 BTC ($1 move = $1 per lot)
        "min_lot": 0.01,
        "max_lot": 2.00,
        "min_sl_dist": 150.0,
        "max_sl_dist": 1500.0,
        "is_crypto": True              # 24/7 365 Days Active (TRADES ON WEEKENDS)
    }
}

# ---------------------------------------------------------------------------
# Time & Session Helpers
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

def is_london_or_ny_session_active():
    """
    STRICT SESSION FILTER (London & New York Only):
    Trading setups and Pre-Alerts are ONLY permitted during:
    - London Session: 12:30 PM to 06:30 PM IST (07:00 to 13:00 UTC)
    - London / NY Overlap: 06:30 PM to 10:00 PM IST (13:00 to 16:30 UTC)
    - New York Session: 06:30 PM to 02:30 AM IST (13:00 to 21:00 UTC)
    Total Allowed Trading Window: 12:30 PM IST to 02:30 AM IST (07:00 to 21:00 UTC)

    Asian Session (05:30 AM to 12:30 PM IST) & Late Night (02:30 AM to 05:30 AM IST):
    STRICTLY FORBIDDEN! No Pre-Alerts and No Confirmed Trades!
    Asian session is exclusively used to establish Asian High & Low levels for London/NY sweeps.
    """
    now_utc = get_utc_now()
    utc_hr = now_utc.hour + (now_utc.minute / 60.0)
    return (7.0 <= utc_hr < 21.0)

# ---------------------------------------------------------------------------
# Persistent State Manager
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
            "daily_halt": False,
            "announced_sessions": []
        }
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if data.get("date") == today_str:
                        return data
                    else:
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
                self.state["open_trades"] = []
                self.state["announced_sessions"] = []
                self.save_state()
            send_telegram_message(
                f"🌅 <b>NEW TRADING DAY INITIALIZED ({today_str})</b>\n"
                f"• Daily Limits: <b>Max 2 Trades</b> | <b>$30 Risk per Trade</b>\n"
                f"• Pair Status: <b>XAUUSD</b> (Forex) | <b>BTCUSD</b> (24/7 Active)"
            )

    def can_trade_today(self):
        self.check_new_day()
        if not is_london_or_ny_session_active():
            return False, "Asian / Off-Session (Trading ONLY allowed in London & NY: 12:30 PM – 2:30 AM IST)"
        if self.state["trades_today"] >= MAX_DAILY_TRADES:
            return False, f"Max {MAX_DAILY_TRADES} trades completed for today"
        if self.state["losses_today"] >= MAX_DAILY_LOSSES:
            return False, f"Max {MAX_DAILY_LOSSES} losses hit today (-${abs(self.state['daily_realized_pnl']):.2f})"
        if self.state["daily_realized_pnl"] <= -DAILY_STOP_LOSS_USD:
            return False, f"Daily stop loss (-${DAILY_STOP_LOSS_USD}) reached"
        return True, "Ready"

state_mgr = StateManager()

# Runtime in-memory caches
last_candle_traded   = {name: None for name in PAIRS_CONFIG}
last_pre_alert_time  = {name: datetime.min.replace(tzinfo=timezone.utc) for name in PAIRS_CONFIG}
active_pre_alerts    = {name: None for name in PAIRS_CONFIG}
pair_cooldown_until  = {name: datetime.min.replace(tzinfo=timezone.utc) for name in PAIRS_CONFIG}
ohlc_cache           = {}

# ---------------------------------------------------------------------------
# Telegram Dispatcher
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
            TELEGRAM_SESSION.post(url, data={"chat_id": target_chat_id}, files=files, timeout=8.0)
            send_telegram_message(caption_html, target_chat_id)
    except Exception as e:
        print(f"Telegram Photo Error: {e}")
        send_telegram_message(caption_html, target_chat_id)

# ---------------------------------------------------------------------------
# Market Data Engine
# ---------------------------------------------------------------------------
def fetch_binance_ohlc(symbol, interval="5m", limit=180):
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
    now_ts = time.time()
    cache_entry = ohlc_cache.get(pair_name)
    if cache_entry and (now_ts - cache_entry["time"]) < 18.0:
        return cache_entry["1h"], cache_entry["15m"], cache_entry["5m"], cache_entry["spot"]

    df_1h, df_15m, df_5m = None, None, None
    live_spot = None

    if info.get("is_crypto"):
        sym = info["binance_symbol"]
        f_1h  = DATA_EXECUTOR.submit(fetch_binance_ohlc, sym, "1h", 120)
        f_15m = DATA_EXECUTOR.submit(fetch_binance_ohlc, sym, "15m", 150)
        f_5m  = DATA_EXECUTOR.submit(fetch_binance_ohlc, sym, "5m", 150)
        df_1h, df_15m, df_5m = f_1h.result(), f_15m.result(), f_5m.result()
        if df_5m is not None and not df_5m.empty:
            live_spot = float(df_5m.iloc[-1]["Close"])
    else:
        tv_future = DATA_EXECUTOR.submit(fetch_tradingview_spot_and_5m, info["tv_tickers"], info["tv_screener"])
        try:
            tkr = yf.Ticker(info["yf_symbol"])
            df_1h  = sanitize_df(tkr.history(period="10d", interval="1h", auto_adjust=False))
            df_15m = sanitize_df(tkr.history(period="5d", interval="15m", auto_adjust=False))
            df_5m  = sanitize_df(tkr.history(period="2d", interval="5m", auto_adjust=False))
        except Exception:
            pass

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

def get_active_session_name():
    """Returns human-readable active session name and exact current IST time."""
    now_utc = get_utc_now()
    utc_hr = now_utc.hour + (now_utc.minute / 60.0)
    now_ist = get_ist_now()
    ist_time_str = now_ist.strftime("%I:%M %p IST")

    if 13.0 <= utc_hr <= 16.5:
        session_label = "London / NY Overlap (6:30 PM – 10:00 PM IST) 🔥"
    elif 7.0 <= utc_hr < 13.0:
        session_label = "London Session (12:30 PM – 6:30 PM IST) 🇬🇧"
    elif 13.0 <= utc_hr < 21.0:
        session_label = "New York Session (6:30 PM – 2:30 AM IST) 🇺🇸"
    elif 0.0 <= utc_hr < 7.0:
        session_label = "Asian Session (5:30 AM – 12:30 PM IST) 🌏"
    else:
        session_label = "Late NY / Pre-Asia (2:30 AM – 5:30 AM IST) 🌙"

    return f"{session_label} [Time: {ist_time_str}]"

def get_smc_levels_and_1h_bias(df_1h, df_15m, decimals, live_spot=None):
    closed_1h = df_1h.iloc[:-1] if len(df_1h) > 20 else df_1h
    atr_1h = float(closed_1h.iloc[-1]["ATR"])
    c_last = closed_1h.iloc[-1]
    h1_close = float(c_last["Close"])
    h1_ema20 = float(c_last["EMA20"])
    h1_ema50 = float(c_last["EMA50"])

    # Live forming 1H candle / live spot (Prevents 1-hour lag during strong rallies)
    spot = live_spot if live_spot is not None else float(df_1h.iloc[-1]["Close"])
    cur_1h_open = float(df_1h.iloc[-1]["Open"])
    cur_1h_is_green = spot >= cur_1h_open

    # 15M Intraday Order Flow (Reacts in real-time to rallies & dips)
    m15_c0 = df_15m.iloc[-2] if len(df_15m) >= 2 else df_15m.iloc[-1]
    m15_close = float(m15_c0["Close"])
    m15_ema20 = float(m15_c0["EMA20"])
    m15_ema50 = float(m15_c0["EMA50"])
    m15_bullish = (spot > m15_ema20) or (m15_close > m15_ema20 and m15_ema20 >= m15_ema50)

    highs = closed_1h["High"].values
    lows  = closed_1h["Low"].values
    swing_highs, swing_lows = [], []
    for i in range(2, len(closed_1h) - 1):
        if highs[i] >= max(highs[i-2:i]) and highs[i] >= highs[i+1]:
            swing_highs.append(round(float(highs[i]), decimals))
        if lows[i] <= min(lows[i-2:i]) and lows[i] <= lows[i+1]:
            swing_lows.append(round(float(lows[i]), decimals))

    # Fast, Responsive Multi-Timeframe Trend & Structure Detection (BUY & SELL Enabled)
    if spot >= h1_ema20 or (m15_bullish and cur_1h_is_green):
        if spot >= h1_ema50 or (swing_lows and spot > swing_lows[-1]):
            h1_bias = "BULLISH"
            trend_desc = "BULLISH (Upward Order Flow / Higher Lows) 📈"
        else:
            h1_bias = "BULLISH"
            trend_desc = "BULLISH (Upward Momentum & 15M Bullish Shift) 🚀"
    elif spot < h1_ema20 and not m15_bullish:
        if spot <= h1_ema50 or (swing_highs and spot < swing_highs[-1]):
            h1_bias = "BEARISH"
            trend_desc = "BEARISH (Downward Order Flow / Lower Highs) 📉"
        else:
            h1_bias = "BEARISH"
            trend_desc = "BEARISH (Downward Reversal Momentum) 🩸"
    else:
        if m15_bullish or spot >= h1_ema20:
            h1_bias = "BULLISH"
            trend_desc = "BULLISH (15M Order Flow Leading Upward) 📈"
        else:
            h1_bias = "BEARISH"
            trend_desc = "BEARISH (15M Order Flow Leading Downward) 📉"

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

    # Major 1H Structure Swings
    if swing_highs:
        levels.append({"name": "1H Swing High", "short": "1H High", "price": swing_highs[-1], "type": "BUY_SIDE_LIQUIDITY"})
    if swing_lows:
        levels.append({"name": "1H Swing Low", "short": "1H Low", "price": swing_lows[-1], "type": "SELL_SIDE_LIQUIDITY"})

    # Dynamic 15M Intraday Swing Points (Pullback Liquidity during Rallies/Dumps)
    highs_15m = df_15m["High"].values
    lows_15m  = df_15m["Low"].values
    n_15m = len(df_15m)

    recent_15m_lows = []
    recent_15m_highs = []
    for i in range(max(2, n_15m - 20), n_15m - 2):
        if lows_15m[i] <= min(lows_15m[i-2:i]) and lows_15m[i] <= min(lows_15m[i+1:i+3]):
            recent_15m_lows.append(round(float(lows_15m[i]), decimals))
        if highs_15m[i] >= max(highs_15m[i-2:i]) and highs_15m[i] >= max(highs_15m[i+1:i+3]):
            recent_15m_highs.append(round(float(highs_15m[i]), decimals))

    if recent_15m_lows:
        levels.append({"name": "15M Intraday Low", "short": "15M Low", "price": recent_15m_lows[-1], "type": "SELL_SIDE_LIQUIDITY"})
    if recent_15m_highs:
        levels.append({"name": "15M Intraday High", "short": "15M High", "price": recent_15m_highs[-1], "type": "BUY_SIDE_LIQUIDITY"})

    return {
        "h1_bias": h1_bias,
        "trend_desc": trend_desc,
        "atr_1h": atr_1h,
        "pdh": pdh, "pdl": pdl,
        "london_high": london_high, "london_low": london_low,
        "asian_high": asian_high, "asian_low": asian_low,
        "levels": levels
    }

# ---------------------------------------------------------------------------
# Position Sizing
# ---------------------------------------------------------------------------
def calculate_lot_size(pair_name, info, sl_dist):
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

    lot_size = max(min_lot, min(round(raw_lot, 2), max_lot))
    actual_risk = round(lot_size * loss_per_1_lot, 2)

    if actual_risk > (RISK_PER_TRADE_USD + 0.75) and lot_size > min_lot:
        lot_size = round(lot_size - 0.01, 2)
        actual_risk = round(lot_size * loss_per_1_lot, 2)

    return lot_size, actual_risk

# ---------------------------------------------------------------------------
# SMC Strategy Engine (With Pre-Alert & Confirmed Entry Stages)
# ---------------------------------------------------------------------------
def evaluate_smc_setup(pair_name, info, df_1h, df_15m, df_5m, live_spot):
    dec = info["decimals"]
    smc_ctx = get_smc_levels_and_1h_bias(df_1h, df_15m, dec, live_spot=live_spot)
    h1_bias = smc_ctx["h1_bias"]
    trend_desc = smc_ctx["trend_desc"]

    m5_c0  = df_5m.iloc[-2]  # Last closed 5M candle
    atr_5m = float(m5_c0["ATR"])
    body_r = float(m5_c0["Body_Ratio"])
    p_close = float(m5_c0["Close"])
    p_open  = float(m5_c0["Open"])

    if np.isnan(atr_5m) or atr_5m <= 0:
        return None

    m15_recent = df_15m.iloc[-6:-1]
    # Flexible 7-candle lookback for minor swing neckline pivot
    m5_lookback = df_5m.iloc[-9:-2]
    neck_high = float(m5_lookback["High"].max())
    neck_low  = float(m5_lookback["Low"].min())

    candidates = []

    for lv in smc_ctx["levels"]:
        lvl_price = lv["price"]
        lvl_short = lv["short"]
        lvl_type  = lv["type"]

        # BUY SETUP
        # 1. Pro-Trend Pullback: If order flow is BULLISH, catch ANY dip/pullback sweep
        # 2. Reversal Bottom Grab: If a major level (Asian Low, London Low, PDL, 1H Low) is swept, allow BUY reversal
        is_major_sell_liq = lvl_short in ["ASIA L", "LON L", "PDL", "1H Low"]
        allow_buy_check = (h1_bias == "BULLISH") or is_major_sell_liq

        if allow_buy_check and lvl_type == "SELL_SIDE_LIQUIDITY":
            m15_min_low = float(m15_recent["Low"].min())
            m5_min_low  = float(df_5m.iloc[-8:-1]["Low"].min())
            min_recent_low = min(m15_min_low, m5_min_low)
            swept = (min_recent_low <= lvl_price + (atr_5m * 0.12)) and (live_spot >= lvl_price - (atr_5m * 0.20))

            if swept:
                sweep_low = min(m5_min_low, min_recent_low)

                # Confirmed Breakout vs Pre-Alert Forming
                mss_confirmed = (
                    (p_close > neck_high) and
                    (p_close > p_open) and
                    (body_r >= 0.40) and
                    (live_spot >= lvl_price)
                )

                mss_forming = (
                    not mss_confirmed and
                    (live_spot >= lvl_price) and
                    (live_spot < neck_high + atr_5m * 0.15) and
                    ((neck_high - live_spot) <= atr_5m * 1.10)
                )

                if mss_confirmed or mss_forming:
                    entry = round(p_close if mss_confirmed else neck_high, dec)
                    sl = round(sweep_low - (atr_5m * 0.18), dec)
                    sl_dist = round(entry - sl, dec)

                    if info["min_sl_dist"] <= sl_dist <= info["max_sl_dist"]:
                        lot_size, actual_risk = calculate_lot_size(pair_name, info, sl_dist)
                        if lot_size is not None:
                            be_level = round(entry + sl_dist, dec)
                            tp_level = round(entry + (sl_dist * PRIMARY_RR_TARGET), dec)
                            stage = "CONFIRMED" if mss_confirmed else "PRE_ALERT"

                            candidates.append({
                                "stage": stage,
                                "direction": "BUY",
                                "action": "BUY (LONG) 🟢",
                                "h1_bias": h1_bias,
                                "trend_desc": trend_desc,
                                "swept_level": f"{lvl_short} ({lvl_price:.{dec}f})",
                                "swept_price": lvl_price,
                                "neckline": neck_high,
                                "sweep_point": sweep_low,
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
                                    f"1H Trend: {trend_desc}",
                                    f"Liquidity: {lvl_short} Swept & Reclaimed",
                                    "5M Trigger: Market Structure Shift (MSS) Breakout" if mss_confirmed else f"5M Trigger: Approaching Neckline {neck_high:.{dec}f}"
                                ]
                            })

        # SELL SETUP
        # 1. Pro-Trend Pullback: If order flow is BEARISH, catch ANY rally sweep
        # 2. Reversal Top Grab: If a major level is swept AND market is NOT in a raging bull rally
        is_major_buy_liq = lvl_short in ["ASIA H", "LON H", "PDH", "1H High"]
        allow_sell_check = (h1_bias == "BEARISH") or (is_major_buy_liq and h1_bias != "BULLISH")

        if allow_sell_check and lvl_type == "BUY_SIDE_LIQUIDITY":
            m15_max_high = float(m15_recent["High"].max())
            m5_max_high  = float(df_5m.iloc[-8:-1]["High"].max())
            max_recent_high = max(m15_max_high, m5_max_high)
            swept = (max_recent_high >= lvl_price - (atr_5m * 0.12)) and (live_spot <= lvl_price + (atr_5m * 0.20))

            if swept:
                sweep_high = max(m5_max_high, max_recent_high)

                mss_confirmed = (
                    (p_close < neck_low) and
                    (p_close < p_open) and
                    (body_r >= 0.40) and
                    (live_spot <= lvl_price)
                )

                mss_forming = (
                    not mss_confirmed and
                    (live_spot <= lvl_price) and
                    (live_spot > neck_low - atr_5m * 0.15) and
                    ((live_spot - neck_low) <= atr_5m * 1.10)
                )

                if mss_confirmed or mss_forming:
                    entry = round(p_close if mss_confirmed else neck_low, dec)
                    sl = round(sweep_high + (atr_5m * 0.18), dec)
                    sl_dist = round(sl - entry, dec)

                    if info["min_sl_dist"] <= sl_dist <= info["max_sl_dist"]:
                        lot_size, actual_risk = calculate_lot_size(pair_name, info, sl_dist)
                        if lot_size is not None:
                            be_level = round(entry - sl_dist, dec)
                            tp_level = round(entry - (sl_dist * PRIMARY_RR_TARGET), dec)
                            stage = "CONFIRMED" if mss_confirmed else "PRE_ALERT"

                            candidates.append({
                                "stage": stage,
                                "direction": "SELL",
                                "action": "SELL (SHORT) 🔴",
                                "h1_bias": h1_bias,
                                "trend_desc": trend_desc,
                                "swept_level": f"{lvl_short} ({lvl_price:.{dec}f})",
                                "swept_price": lvl_price,
                                "neckline": neck_low,
                                "sweep_point": sweep_high,
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
                                    f"1H Trend: {trend_desc}",
                                    f"Liquidity: {lvl_short} Swept & Reclaimed",
                                    "5M Trigger: Market Structure Shift (MSS) Breakout" if mss_confirmed else f"5M Trigger: Approaching Neckline {neck_low:.{dec}f}"
                                ]
                            })

    if not candidates:
        return None
    candidates.sort(key=lambda x: (
        1 if x["stage"] == "CONFIRMED" else 0,
        1 if x["direction"] == ("BUY" if h1_bias == "BULLISH" else "SELL") else 0
    ), reverse=True)
    return candidates[0]

# ---------------------------------------------------------------------------
# TradingView Long / Short Position Chart Generator
# ---------------------------------------------------------------------------
def generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=None, status_badge=None):
    """
    Renders authentic TradingView chart with:
    - Long / Short Position tool (Green Target box & Red Stop Loss box)
    - Hand-drawn style annotations (Liquidity Swept arrow & 5M MSS Break line)
    - Live spot badge with candle countdown timer
    - Clean white aesthetic with right-side price axis badges
    """
    try:
        sub = df_5m.iloc[-45:].copy()
        if len(sub) < 15:
            return None

        dec = info["decimals"]
        sym = info["symbol"]
        smc_ctx = get_smc_levels_and_1h_bias(df_1h, df_15m, dec, live_spot=live_spot)

        fig = plt.figure(figsize=(9.6, 5.2), dpi=100, facecolor="#ffffff")
        ax = fig.add_axes([0.02, 0.08, 0.86, 0.82], facecolor="#ffffff")

        n_bars = len(sub)
        x_vals = np.arange(n_bars)
        right_pad = n_bars + 18

        ax.grid(True, linestyle="-", linewidth=0.5, color="#f1f5f9", zorder=1)
        for s in ["top", "left"]:
            ax.spines[s].set_visible(False)
        ax.spines["right"].set_color("#cbd5e1")
        ax.spines["bottom"].set_color("#cbd5e1")

        opens  = sub["Open"].values.astype(float)
        highs  = sub["High"].values.astype(float)
        lows   = sub["Low"].values.astype(float)
        closes = sub["Close"].values.astype(float)

        all_y = list(highs) + list(lows) + [live_spot]
        if active_trade:
            all_y.extend([active_trade["entry"], active_trade["sl"], active_trade["tp"]])

        c_min, c_max = float(np.min(all_y)), float(np.max(all_y))
        y_span = max(c_max - c_min, 1e-4)

        # Plot Japanese Candlesticks
        bulls = closes >= opens
        bears = ~bulls
        ax.vlines(x_vals[bulls], lows[bulls], highs[bulls], color="#089981", linewidth=1.2, zorder=3)
        ax.vlines(x_vals[bears], lows[bears], highs[bears], color="#f23645", linewidth=1.2, zorder=3)
        ax.bar(x_vals[bulls], np.abs(closes[bulls] - opens[bulls]), bottom=np.minimum(opens[bulls], closes[bulls]), width=0.66, color="#089981", edgecolor="#089981", zorder=3)
        ax.bar(x_vals[bears], np.abs(closes[bears] - opens[bears]), bottom=np.minimum(opens[bears], closes[bears]), width=0.66, color="#f23645", edgecolor="#f23645", zorder=3)

        # Plot Nearby Liquidity Pools (Dashed horizontal lines with labels)
        for lv in smc_ctx["levels"]:
            p = lv["price"]
            if (c_min - y_span * 0.15) <= p <= (c_max + y_span * 0.15):
                col = "#b91c1c" if "BUY" in lv["type"] else "#047857"
                ax.axhline(p, color=col, linestyle="--", linewidth=0.8, alpha=0.60, zorder=2)
                ax.text(
                    1, p + (y_span * 0.012), f" {lv['short']} ({p:.{dec}f}) ",
                    color=col, fontsize=7.2, fontweight="bold",
                    bbox=dict(boxstyle="square,pad=0.15", facecolor="#ffffff", edgecolor=col, linewidth=0.6, alpha=0.85),
                    zorder=4
                )

        # Draw TradingView Long / Short Position Tool
        if active_trade:
            ent = active_trade["entry"]
            sl  = active_trade["sl"]
            tp  = active_trade["tp"]
            be  = active_trade["be_level"]
            is_buy = active_trade["direction"] == "BUY"

            x_box_start = n_bars - 2
            box_width = 19

            # Profit Box (Green) & Risk Box (Red)
            target_h = abs(tp - ent)
            stop_h   = abs(ent - sl)

            # Profit Area Rectangle
            ax.add_patch(mpatches.Rectangle(
                (x_box_start, min(ent, tp)), box_width, target_h,
                facecolor="#089981", alpha=0.18, edgecolor="#089981", linewidth=1.0, zorder=2
            ))
            # Stop Area Rectangle
            ax.add_patch(mpatches.Rectangle(
                (x_box_start, min(ent, sl)), box_width, stop_h,
                facecolor="#f23645", alpha=0.18, edgecolor="#f23645", linewidth=1.0, zorder=2
            ))

            # Entry Level Center Line
            ax.plot([x_box_start - 3, right_pad], [ent, ent], color="#2563eb", linestyle="--", linewidth=1.3, zorder=5)
            # TP & SL Boundary Lines
            ax.plot([x_box_start, right_pad], [tp, tp], color="#089981", linewidth=1.4, zorder=5)
            ax.plot([x_box_start, right_pad], [sl, sl], color="#dc2626", linewidth=1.4, zorder=5)
            # 1:1 Breakeven Line
            ax.plot([x_box_start, right_pad], [be, be], color="#089981", linestyle=":", linewidth=1.0, zorder=4)

            # Directional Entry Arrow
            arrow_col = "#089981" if is_buy else "#f23645"
            arrow_y = ent - (y_span * 0.04 if is_buy else -y_span * 0.04)
            ax.annotate(
                "", xy=(x_box_start, ent), xytext=(x_box_start, arrow_y),
                arrowprops=dict(facecolor=arrow_col, edgecolor=arrow_col, width=2.5, headwidth=8.0),
                zorder=7
            )

            # Central Long/Short Tool Badge (Shifted right so candles remain 100% visible)
            badge_title = "🎯 LONG POSITION" if is_buy else "🎯 SHORT POSITION"
            badge_shift_x = x_box_start + 6.0
            ax.text(
                badge_shift_x, ent, f" {badge_title} | 1:2 RR ($30 -> +$60) ",
                color="#ffffff", fontsize=7.4, fontweight="bold", ha="left", va="center",
                bbox=dict(boxstyle="round,pad=0.25", facecolor="#1e40af", edgecolor="none"),
                zorder=8
            )

            # Hand-drawn Style Markers (Swept Wick & MSS Break)
            if active_trade.get("sweep_point"):
                sp_y = active_trade["sweep_point"]
                ax.text(
                    n_bars - 8, sp_y, "💧 Liquidity Swept!",
                    color="#b91c1c" if not is_buy else "#047857", fontsize=7.6, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.18", facecolor="#ffffff", edgecolor="#cbd5e1", linewidth=0.6),
                    zorder=7
                )
            if active_trade.get("neckline"):
                nl_y = active_trade["neckline"]
                ax.axhline(nl_y, color="#64748b", linestyle=":", linewidth=0.9, alpha=0.7)
                ax.text(n_bars - 12, nl_y, "⚡ 5M MSS Break", color="#475569", fontsize=7.4, fontweight="bold")

            # Right Axis Exact Price Badges
            ax.text(
                right_pad + 0.3, tp, f" 1:2 TP {tp:.{dec}f} (+ $60) ",
                color="#ffffff", fontsize=7.8, fontweight="bold", va="center", ha="left",
                bbox=dict(boxstyle="square,pad=0.20", facecolor="#16a34a", edgecolor="none"),
                clip_on=False, zorder=9
            )
            ax.text(
                right_pad + 0.3, ent, f" ENTRY {ent:.{dec}f} ",
                color="#ffffff", fontsize=7.8, fontweight="bold", va="center", ha="left",
                bbox=dict(boxstyle="square,pad=0.20", facecolor="#2563eb", edgecolor="none"),
                clip_on=False, zorder=9
            )
            ax.text(
                right_pad + 0.3, sl, f" SL {sl:.{dec}f} (- $30) ",
                color="#ffffff", fontsize=7.8, fontweight="bold", va="center", ha="left",
                bbox=dict(boxstyle="square,pad=0.20", facecolor="#dc2626", edgecolor="none"),
                clip_on=False, zorder=9
            )

        # Live Spot Tag with 5M Candle Countdown Timer
        now_utc = get_utc_now()
        rem_sec = max(0, (5 - (now_utc.minute % 5)) * 60 - now_utc.second)
        mm, ss = divmod(rem_sec, 60)
        spot_col = "#089981" if closes[-1] >= opens[-1] else "#f23645"
        ax.axhline(live_spot, color=spot_col, linestyle=":", linewidth=0.9, zorder=6)
        ax.text(
            right_pad + 0.3, live_spot, f" SPOT {live_spot:.{dec}f} \n {mm:02d}:{ss:02d} ",
            color="#ffffff", fontsize=7.6, fontweight="bold", va="center", ha="left",
            bbox=dict(boxstyle="square,pad=0.22", facecolor=spot_col, edgecolor="none"),
            clip_on=False, zorder=10
        )

        ax.set_ylim(c_min - (y_span * 0.08), c_max + (y_span * 0.08))
        ax.set_xlim(-0.8, right_pad)
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", labelsize=8.0, colors="#0f172a")
        ax.set_xticks([])

        # Top Bar Header
        header_text = f"{sym} · 5M SMC Liquidity & Structure"
        if status_badge:
            header_text += f" [{status_badge}]"
        fig.text(0.02, 0.94, header_text, fontsize=9.8, fontweight="bold", color="#0f172a")

        bias_bg = "#ecfdf5" if smc_ctx["h1_bias"] == "BULLISH" else ("#fef2f2" if smc_ctx["h1_bias"] == "BEARISH" else "#f8fafc")
        fig.text(
            0.88, 0.94, f" 1H Bias: {smc_ctx['h1_bias']} ",
            fontsize=8.0, fontweight="bold", color="#0f172a", ha="right",
            bbox=dict(boxstyle="round,pad=0.22", facecolor=bias_bg, edgecolor="#cbd5e1", linewidth=0.6)
        )

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100, facecolor="#ffffff")
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Chart Render Error: {e}")
        return None

# ---------------------------------------------------------------------------
# Asynchronous Alert Handlers
# ---------------------------------------------------------------------------
def async_dispatch_pre_alert(setup, info, df_1h, df_15m, df_5m, live_spot):
    """Sends pre-alert when setup is forming so user can prepare in advance."""
    def _worker():
        dec = info["decimals"]
        session_str = get_active_session_name()
        trend_str = setup.get("trend_desc", setup.get("h1_bias", "NEUTRAL"))
        caption = (
            f"⏳ <b>SMC PRE-ALERT: SETUP FORMING!</b> ⏳\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🔹 <b>Market:</b> <b>{info['symbol']}</b>\n"
            f"📢 <b>Upcoming Action:</b> <b>{setup['action']}</b>\n"
            f"🕒 <b>Session:</b> <b>{session_str}</b>\n"
            f"🧭 <b>1H Trend:</b> <b>{trend_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧲 <b>Swept Pool:</b> <b>{setup['swept_level']}</b>\n"
            f"📍 <b>Live Spot:</b> <code>{live_spot:.{dec}f}</code>\n"
            f"🎯 <b>Watch For 5M Trigger @</b> <code>{setup['entry']:.{dec}f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 <b>Planned Lot Size:</b> <b><code>{setup['lot_size']} Lot</code></b>\n"
            f"🛑 <b>Tentative SL:</b> <code>{setup['sl']:.{dec}f}</code> (Risk: ${setup['risk_usd']:.2f})\n"
            f"🎯 <b>Tentative 1:2 TP:</b> <code>{setup['tp']:.{dec}f}</code> (+${setup['reward_usd']:.2f})\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⛔ <b>सख्त नियम (Do NOT Enter Yet):</b>\n"
            f"<i>यह केवल तैयारी का पूर्व-अलर्ट है। अग्रिम (Advance) में कोई ऑर्डर न लगाएं!\n"
            f"5M कैंडल ठोस बॉडी के साथ क्लोज होने पर जब 'CONFIRMED SIGNAL' आएगा, तभी ट्रेड लें। यदि कैंडल कमजोर रही तो ट्रेड रद्द (Cancel) हो जाएगा।</i>"
        )
        chart_buf = generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=setup, status_badge="SETUP FORMING")
        if chart_buf:
            send_telegram_photo(chart_buf.getvalue(), caption)
        else:
            send_telegram_message(caption)

    CHART_EXECUTOR.submit(_worker)

def async_dispatch_setup_cancelled(info, reason="5M candle closed without solid displacement body (Wick Rejection / Fakeout)."):
    """Sends notification when a pre-alert setup is rejected/cancelled."""
    def _worker():
        caption = (
            f"❌ <b>SMC SETUP CANCELLED: {info['symbol']}</b> ❌\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ <b>Status:</b> <b>Setup Invalidated (Trade Aborted)</b>\n"
            f"🔍 <b>Reason:</b> {reason}\n"
            f"🛡️ <b>Capital Protected:</b> <i>कमजोर कैंडल या फेक ब्रेकआउट से बचा लिया गया। कोई ट्रेड नहीं लिया गया!</i>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📌 <i>अगले हाई-प्रोबेबिलिटी SMC सेटअप का इंतज़ार करें।</i>"
        )
        send_telegram_message(caption)
    CHART_EXECUTOR.submit(_worker)

def async_dispatch_trade_signal(trade, info, df_1h, df_15m, df_5m, live_spot):
    """Dispatches confirmed trade alert with Long/Short position tool on chart."""
    def _worker():
        dec = info["decimals"]
        trade_num = state_mgr.state["trades_today"]
        session_str = get_active_session_name()
        trend_str = trade.get("trend_desc", trade.get("h1_bias", "NEUTRAL"))
        caption = (
            f"🚨 <b>CONFIRMED SMC TRADE SIGNAL: {info['symbol']}</b> 🚨\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📢 <b>ACTION:</b> <b>{trade['action']}</b>\n"
            f"🕒 <b>SESSION:</b> <b>{session_str}</b>\n"
            f"🧭 <b>1H TREND:</b> <b>{trend_str}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>ENTRY SPOT:</b> <code>{trade['entry']:.{dec}f}</code>  (Live: <code>{live_spot:.{dec}f}</code>)\n"
            f"🛑 <b>STOP LOSS:</b> <code>{trade['sl']:.{dec}f}</code>  (Dist: {trade['sl_dist']:.{dec}f})\n"
            f"🎯 <b>1:2 FULL TP:</b> <code>{trade['tp']:.{dec}f}</code>  (Dist: {trade['tp_dist']:.{dec}f})\n"
            f"🛡️ <b>1:1 BREAKEVEN:</b> <code>{trade['be_level']:.{dec}f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💼 <b>LOT SIZE:</b> <b><code>{trade['lot_size']} Lot</code></b>\n"
            f"💵 <b>PLANNED RISK:</b> <b>${trade['risk_usd']:.2f}</b>\n"
            f"💰 <b>NET 1:2 REWARD:</b> <b>+${trade['reward_usd']:.2f}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🧠 <b>SMC LOGIC:</b>\n"
            f"1️⃣ {trade['smc_notes'][0]}\n"
            f"2️⃣ {trade['smc_notes'][1]}\n"
            f"3️⃣ {trade['smc_notes'][2]}\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"📊 <i>Trade #{trade_num}/{MAX_DAILY_TRADES} for today. Set SL & TP in your broker now.</i>"
        )
        chart_buf = generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=trade, status_badge="CONFIRMED ENTRY")
        if chart_buf:
            send_telegram_photo(chart_buf.getvalue(), caption)
        else:
            send_telegram_message(caption)

    CHART_EXECUTOR.submit(_worker)

def async_dispatch_exit_notification(trade, hit_type, exit_price, pnl_usd, info, df_1h, df_15m, df_5m, live_spot):
    """Sends instant alert when TP or SL is hit, along with the Long/Short chart."""
    def _worker():
        dec = info["decimals"]
        if hit_type == "TP":
            title = f"🎯 <b>1:2 FULL TARGET HIT! (+${pnl_usd:.2f})</b> 🎯"
            body = (
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 <b>Market:</b> <b>{info['symbol']}</b> ({trade['direction']})\n"
                f"🎯 <b>Target Hit:</b> <code>{exit_price:.{dec}f}</code>\n"
                f"💰 <b>Profit Realized:</b> <b>+${pnl_usd:.2f} (+2R)</b>\n"
                f"📈 <b>Today's Net PnL:</b> <code>${state_mgr.state['daily_realized_pnl']:+.2f}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🏆 <i>Trade target successfully achieved with 1:2 Risk to Reward!</i>"
            )
            badge = "1:2 TARGET HIT"
        elif hit_type == "BE":
            title = f"🛡️ <b>BREAKEVEN EXIT (0 LOSS)</b> 🛡️"
            body = (
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 <b>Market:</b> <b>{info['symbol']}</b> ({trade['direction']})\n"
                f"📍 <b>Exited @ Entry:</b> <code>{exit_price:.{dec}f}</code>\n"
                f"🛡️ <b>Capital Preserved:</b> $0 Loss (Risk-Free Exit)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"✨ <i>1:1 lock protected your account. Onto the next high-quality setup!</i>"
            )
            badge = "BREAKEVEN EXIT"
        else:
            title = f"🛑 <b>STOP LOSS HIT (-${abs(pnl_usd):.2f})</b> 🛑"
            body = (
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"🔹 <b>Market:</b> <b>{info['symbol']}</b> ({trade['direction']})\n"
                f"🛑 <b>Stop Out:</b> <code>{exit_price:.{dec}f}</code>\n"
                f"💵 <b>Loss Amount:</b> <b>-${abs(pnl_usd):.2f} (-1R)</b>\n"
                f"🛑 <b>Losses Today:</b> {state_mgr.state['losses_today']}/{MAX_DAILY_LOSSES}\n"
                f"📉 <b>Today's Net PnL:</b> <code>${state_mgr.state['daily_realized_pnl']:+.2f}</code>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"⚠️ <i>Strict risk rule enforced: Maximum $30 loss per trade.</i>"
            )
            badge = "STOP LOSS HIT"

        caption = f"{title}\n{body}"
        chart_buf = generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=trade, status_badge=badge)
        if chart_buf:
            send_telegram_photo(chart_buf.getvalue(), caption)
        else:
            send_telegram_message(caption)

    CHART_EXECUTOR.submit(_worker)

# ---------------------------------------------------------------------------
# Session Kickoff Broadcaster (London & New York Popups)
# ---------------------------------------------------------------------------
def check_session_kickoffs():
    """Sends automatic popup when London or New York Session starts."""
    utc_now = get_utc_now()
    utc_hour = utc_now.hour + (utc_now.minute / 60.0)
    today_str = str(get_ist_now().date())

    announced = state_mgr.state.get("announced_sessions", [])

    # London Session (07:00 UTC / 12:30 PM IST)
    if (7.0 <= utc_hour < 7.5) and "london" not in announced:
        announced.append("london")
        state_mgr.state["announced_sessions"] = announced
        state_mgr.save_state()

        # Send London Kickoff Popup
        send_telegram_message(
            f"🔔 <b>LONDON SESSION KICKOFF (12:30 PM – 4:30 PM IST)</b> 🔔\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"💧 <b>Market Liquidity Influx Active!</b>\n"
            f"📌 <b>Strategy:</b> 15M London Sweep + 5M MSS\n"
            f"📋 <b>Risk Rule:</b> $30 Risk | Max 2 Trades/Day\n"
            f"• <b>Gold (XAUUSD):</b> Scanning London High/Low sweeps\n"
            f"• <b>Bitcoin (BTCUSD):</b> 🟢 24/7 Active\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <i>Bot is active and ready for London setups!</i>"
        )
        send_instant_chart("XAU/USD")

    # New York Session (13:00 UTC / 6:30 PM IST)
    if (13.0 <= utc_hour < 13.5) and "new_york" not in announced:
        announced.append("new_york")
        state_mgr.state["announced_sessions"] = announced
        state_mgr.save_state()

        # Send New York Kickoff Popup
        send_telegram_message(
            f"🔔 <b>NEW YORK SESSION KICKOFF (6:30 PM – 10:30 PM IST)</b> 🔔\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <b>NY Open High-Volatility Active!</b>\n"
            f"📌 <b>Strategy:</b> 15M NY Liquidity Sweep + 5M MSS\n"
            f"📋 <b>Risk Rule:</b> $30 Risk | Max 2 Trades/Day\n"
            f"• <b>Gold (XAUUSD):</b> London High/Low sweep watch\n"
            f"• <b>Bitcoin (BTCUSD):</b> 🟢 24/7 Active\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚡ <i>Hunting for prime New York trade setups!</i>"
        )
        send_instant_chart("XAU/USD")

# ---------------------------------------------------------------------------
# Real-Time Open Position Monitor (SL, TP, and 1:1 Breakeven)
# ---------------------------------------------------------------------------
def monitor_open_positions(pair_name, info, df_1h, df_15m, df_5m, live_spot):
    """Monitors live trades and triggers instant notifications when TP, SL, or 1:1 BE is reached."""
    open_trades = state_mgr.state.get("open_trades", [])
    if not open_trades:
        return

    latest_bar = df_5m.iloc[-1]
    bar_high = max(live_spot, float(latest_bar["High"]))
    bar_low  = min(live_spot, float(latest_bar["Low"]))
    dec = info["decimals"]

    for trade in open_trades:
        if trade.get("symbol") != info["symbol"] or not trade.get("is_open", True):
            continue

        direction = trade["direction"]
        ent = trade["entry"]
        sl  = trade["sl"]
        tp  = trade["tp"]
        be  = trade["be_level"]

        # 1:1 Breakeven Check
        if not trade.get("be_locked", False):
            be_reached = (direction == "BUY" and bar_high >= be) or (direction == "SELL" and bar_low <= be)
            if be_reached:
                trade["be_locked"] = True
                trade["sl"] = ent  # Move SL to Entry
                state_mgr.save_state()
                send_telegram_message(
                    f"🛡️ <b>1:1 (+1R) REACHED — MOVE SL TO ENTRY!</b> 🛡️\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🔹 <b>Market:</b> <b>{info['symbol']}</b>\n"
                    f"📍 <b>Current Price:</b> <code>{live_spot:.{dec}f}</code>\n"
                    f"👉 <b>Action:</b> Move Stop Loss to Entry (<code>{ent:.{dec}f}</code>) now!\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"✨ <i>Trade is now 100% Risk-Free. Capital secured!</i>"
                )

        # Check TP or SL Hit
        hit_type = None
        exit_price = None

        if direction == "BUY":
            if bar_high >= tp:
                hit_type = "TP"
                exit_price = tp
            elif bar_low <= sl:
                hit_type = "BE" if trade.get("be_locked") else "SL"
                exit_price = sl
        else:
            if bar_low <= tp:
                hit_type = "TP"
                exit_price = tp
            elif bar_high >= sl:
                hit_type = "BE" if trade.get("be_locked") else "SL"
                exit_price = sl

        if hit_type:
            trade["is_open"] = False
            trade["exit_price"] = exit_price

            if hit_type == "TP":
                pnl = trade["reward_usd"]
                state_mgr.state["daily_realized_pnl"] += pnl
            elif hit_type == "BE":
                pnl = 0.0
            else:
                pnl = -trade["risk_usd"]
                state_mgr.state["losses_today"] += 1
                state_mgr.state["daily_realized_pnl"] += pnl

            state_mgr.save_state()
            async_dispatch_exit_notification(trade, hit_type, exit_price, pnl, info, df_1h, df_15m, df_5m, live_spot)

            # If 2 losses hit, announce halt
            if state_mgr.state["losses_today"] >= MAX_DAILY_LOSSES or state_mgr.state["daily_realized_pnl"] <= -DAILY_STOP_LOSS_USD:
                send_telegram_message(
                    f"🛑 <b>DAILY STOP TRIGGERED (-${abs(state_mgr.state['daily_realized_pnl']):.2f})</b> 🛑\n"
                    f"Max daily loss ($60 or 2 losses) reached. Trading paused until tomorrow to protect capital."
                )

# ---------------------------------------------------------------------------
# Telegram Command Listener Thread
# ---------------------------------------------------------------------------
def telegram_command_listener():
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
                            open_cnt = sum(1 for t in st.get("open_trades", []) if t.get("is_open", True))
                            sess_label = get_active_session_name()
                            msg_out = (
                                f"📊 <b>SMC BOT STATUS ({st['date']})</b>\n\n"
                                f"🕒 <b>Active Session:</b> {sess_label}\n"
                                f"🚦 <b>Trading Status:</b> {'🟢 READY' if can_tr else '🔴 PAUSED (' + reason + ')'}\n"
                                f"🔢 <b>Trades Taken Today:</b> {st['trades_today']}/{MAX_DAILY_TRADES}\n"
                                f"🛑 <b>Losses Today:</b> {st['losses_today']}/{MAX_DAILY_LOSSES}\n"
                                f"💵 <b>Risk Per Trade:</b> ${RISK_PER_TRADE_USD:.2f}\n"
                                f"📈 <b>Daily Net PnL:</b> ${st['daily_realized_pnl']:+.2f}\n"
                                f"📂 <b>Open Positions:</b> {open_cnt}\n\n"
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
                                state_mgr.state["open_trades"] = []
                                state_mgr.save_state()
                            send_telegram_message("✅ <b>Today's Trades & Losses Reset to 0!</b>")

                        elif text in ["/gold", "gold"]:
                            send_instant_chart("XAU/USD")

                        elif text in ["/btc", "btc"]:
                            send_instant_chart("BTC/USD")

        except Exception as e:
            time.sleep(2)

def send_instant_chart(pair_name):
    def _worker():
        info = PAIRS_CONFIG[pair_name]
        df_1h, df_15m, df_5m, live_spot = get_3tf_data(pair_name, info)
        if df_1h is None or df_15m is None or df_5m is None:
            send_telegram_message(f"⚠️ Data loading for {info['symbol']}, please retry in 5s.")
            return

        df_1h  = enrich_indicators(df_1h)
        df_15m = enrich_indicators(df_15m)
        df_5m  = enrich_indicators(df_5m)

        buf = generate_smc_chart(df_1h, df_15m, df_5m, info, live_spot, status_badge="LIVE SNAPSHOT")
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
    print("SMC TRADING BOT PRO ACTIVE (XAU/USD & BTC/USD)")
    print(f"Risk Per Trade: ${RISK_PER_TRADE_USD} | Max Daily Trades: {MAX_DAILY_TRADES}")
    print("SMC Features: Pre-Alert + Long/Short Charts + Session Popups + Hit Alerts")
    print("=================================================================")

    threading.Thread(target=telegram_command_listener, daemon=True, name="TelegramListener").start()

    send_telegram_message(
        f"⚡ <b>SMC TRADING BOT PRO ONLINE</b> ⚡\n\n"
        f"• <b>Markets:</b> XAU/USD & BTC/USD\n"
        f"• <b>Bitcoin Mode:</b> 🟢 <b>24/7 Active (Saturday & Sunday Included)</b>\n"
        f"• <b>Risk Control:</b> <b>${RISK_PER_TRADE_USD:.2f} Risk</b> per trade | Max <b>{MAX_DAILY_TRADES} trades</b>/day\n"
        f"• <b>Target:</b> 1:2 RR (+${RISK_PER_TRADE_USD * PRIMARY_RR_TARGET:.2f})\n"
        f"• <b>Enhanced Features:</b>\n"
        f"  ⏳ <b>Pre-Alerts:</b> Early notification when setup is forming\n"
        f"  🔔 <b>Session Popups:</b> London (12:30 PM IST) & NY (6:30 PM IST)\n"
        f"  📐 <b>TradingView Tool:</b> Long/Short Position Box on all charts\n"
        f"  🎯 <b>Exit Alerts:</b> Instant notification when TP or SL is hit\n\n"
        f"<i>Type /status, /gold, or /btc anytime for live charts.</i>"
    )

    last_scan = 0

    while True:
        try:
            now = time.time()
            if (now - last_scan) >= 8:
                last_scan = now
                state_mgr.check_new_day()
                check_session_kickoffs()

                for pair_name, info in PAIRS_CONFIG.items():
                    if not info.get("is_crypto") and not is_forex_market_open():
                        continue

                    df_1h, df_15m, df_5m, live_spot = get_3tf_data(pair_name, info)
                    if df_1h is None or df_15m is None or df_5m is None or live_spot is None:
                        continue

                    df_1h  = enrich_indicators(df_1h)
                    df_15m = enrich_indicators(df_15m)
                    df_5m  = enrich_indicators(df_5m)

                    closed_candle_time = to_utc_timestamp(df_5m.index[-2])

                    # 1. First monitor any existing open trades for SL / TP / Breakeven
                    monitor_open_positions(pair_name, info, df_1h, df_15m, df_5m, live_spot)

                    # 2. STRICT SESSION GATE: Only London & New York (12:30 PM to 02:30 AM IST)
                    # Asian Session (05:30 AM to 12:30 PM IST) & Late Night (02:30 AM to 05:30 AM IST):
                    # STRICTLY FORBIDDEN! No Pre-Alerts and No Confirmed Trades!
                    if not is_london_or_ny_session_active():
                        continue

                    # 3. Check if account is eligible for a new trade
                    can_trade, _ = state_mgr.can_trade_today()
                    if not can_trade:
                        continue

                    # Don't take a new trade if an open trade in this pair already exists
                    has_open = any(t.get("symbol") == info["symbol"] and t.get("is_open", True) for t in state_mgr.state.get("open_trades", []))
                    if has_open:
                        continue

                    if get_utc_now() < pair_cooldown_until[pair_name]:
                        continue

                    # Evaluate SMC Setup (Pre-Alert vs Confirmed Entry)
                    setup = evaluate_smc_setup(pair_name, info, df_1h, df_15m, df_5m, live_spot)

                    # Monitor active pre-alerts: If a new 5M candle closed without confirmation, notify cancellation
                    prev_alert = active_pre_alerts.get(pair_name)
                    if prev_alert and closed_candle_time > prev_alert["candle_time"]:
                        if not setup or setup.get("stage") != "CONFIRMED":
                            async_dispatch_setup_cancelled(info, "5M कैंडल क्लोज होने पर अपेक्षित डिस्प्लेसमेंट बॉडी नहीं मिली (Wick Rejection / Fakeout). फेक ब्रेकआउट से बचने के लिए ट्रेड कैंसिल!")
                            active_pre_alerts[pair_name] = None

                    if not setup:
                        continue

                    setup["symbol"] = info["symbol"]

                    if setup["stage"] == "CONFIRMED":
                        if last_candle_traded[pair_name] != closed_candle_time:
                            last_candle_traded[pair_name] = closed_candle_time
                            pair_cooldown_until[pair_name] = get_utc_now() + timedelta(minutes=25)
                            active_pre_alerts[pair_name] = None  # Cleared on confirmation

                            setup["is_open"] = True
                            setup["be_locked"] = False
                            setup["entry_time"] = str(get_ist_now())

                            with STATE_LOCK:
                                state_mgr.state["trades_today"] += 1
                                state_mgr.state["open_trades"].append(setup)
                                state_mgr.save_state()

                            async_dispatch_trade_signal(setup, info, df_1h, df_15m, df_5m, live_spot)

                    elif setup["stage"] == "PRE_ALERT":
                        # Send pre-alert if not sent recently
                        now_utc = get_utc_now()
                        if (now_utc - last_pre_alert_time[pair_name]).total_seconds() > 900:  # 15m cooldown
                            last_pre_alert_time[pair_name] = now_utc
                            active_pre_alerts[pair_name] = {"candle_time": closed_candle_time, "setup": setup}
                            async_dispatch_pre_alert(setup, info, df_1h, df_15m, df_5m, live_spot)

            time.sleep(0.5)

        except KeyboardInterrupt:
            print("Bot stopped by user.")
            break
        except Exception as e:
            print(f"Scanner Exception: {e}")
            time.sleep(2)

if __name__ == "__main__":
    run_scanner()
