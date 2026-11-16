import os
import io
import sys
import time
import subprocess
from datetime import datetime, timedelta, timezone

# =====================================================================
# 0. AUTO-INSTALL REQUIRED PACKAGES
# =====================================================================
REQUIRED_PACKAGES = ["requests", "numpy", "pandas", "yfinance", "matplotlib"]
for pkg in REQUIRED_PACKAGES:
    try:
        __import__(pkg)
    except ImportError:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", "--user", pkg])
        except Exception:
            try:
                subprocess.check_call([sys.executable, "-m", "pip", "install", pkg])
            except Exception as e:
                print(f"Package install warning ({pkg}): {e}")

import requests
import numpy as np
import pandas as pd
import yfinance as yf

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import Polygon

HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache"
})

# =====================================================================
# 1. CONFIGURATION (1H ANALYSIS -> 5M EXECUTION | 10+ TRADES/DAY)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID   = os.getenv("CHAT_ID") or "5610520408"

STARTING_CAPITAL_USD        = 5000.0
TARGET_EQUITY_USD           = 5400.0

DAILY_MAX_LOSS_STOP_USD     = 65.0
DAILY_PROFIT_LOCK_USD       = 140.0
HARD_EQUITY_FLOOR_USD       = 4720.0

BASE_RISK_PER_TRADE_USD     = 20.0
WIN_STREAK_RISK_USD         = 24.0
CAUTION_RISK_PER_TRADE_USD  = 12.0
MIN_RISK_PER_TRADE_USD      = 8.0

MAX_DAILY_TRADES_CAP        = 14        # Supports 10+ quality trades per day
MAX_CONSECUTIVE_LOSSES_HALT = 3
MIN_SETUP_SCORE_REQUIRED    = 78

MIN_CORRIDOR_RR_RATIO       = 1.25
DEFAULT_TARGET_RR_RATIO     = 1.50
LOCK_PROFIT_R               = 0.55
TP1_PARTIAL_RATIO           = 0.65
MAX_TRADE_HOLD_MINUTES      = 55

MAX_CONCURRENT_TRADES       = 3
MIN_GAP_BETWEEN_TRADES_MIN  = 4

FOREX_PAIRS = {
    "XAU/USD": {
        "mt5_name": "XAUUSD",
        "tv_header_name": "Gold Spot / U.S. Dollar",
        "cmd_keys": ["/xauusd", "xauusd", "/gold", "gold"],
        "tv_tickers": ["FOREXCOM:XAUUSD", "OANDA:XAUUSD", "FX_IDC:XAUUSD"],
        "tv_screener": "cfd",
        "binance_symbol": None,
        "yf_symbols": ["XAUUSD=X", "GC=F"],
        "currencies": ["USD"],
        "decimals": 2,
        "spread": 0.30,
        "unit_name": "Pts",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 3.20,
        "max_sl_dist": 10.50,
        "max_lot_cap": 0.15,
        "is_crypto": False
    },
    "EUR/USD": {
        "mt5_name": "EURUSD",
        "tv_header_name": "Euro / U.S. Dollar",
        "cmd_keys": ["/eurusd", "eurusd", "/eur", "eur"],
        "tv_tickers": ["FX:EURUSD", "FOREXCOM:EURUSD", "OANDA:EURUSD"],
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbols": ["EURUSD=X"],
        "currencies": ["EUR", "USD"],
        "decimals": 5,
        "spread": 0.00012,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0008,
        "max_sl_dist": 0.0026,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "GBP/USD": {
        "mt5_name": "GBPUSD",
        "tv_header_name": "British Pound / U.S. Dollar",
        "cmd_keys": ["/gbpusd", "gbpusd", "/gbp", "gbp"],
        "tv_tickers": ["FX:GBPUSD", "FOREXCOM:GBPUSD", "OANDA:GBPUSD"],
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbols": ["GBPUSD=X"],
        "currencies": ["GBP", "USD"],
        "decimals": 5,
        "spread": 0.00015,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0009,
        "max_sl_dist": 0.0028,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "USD/JPY": {
        "mt5_name": "USDJPY",
        "tv_header_name": "U.S. Dollar / Japanese Yen",
        "cmd_keys": ["/usdjpy", "usdjpy", "/jpy", "jpy"],
        "tv_tickers": ["FX:USDJPY", "FOREXCOM:USDJPY", "OANDA:USDJPY"],
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbols": ["JPY=X"],
        "currencies": ["USD", "JPY"],
        "decimals": 3,
        "spread": 0.014,
        "unit_name": "Pips",
        "usd_exposure": "LONG_USD_ON_BUY",
        "min_sl_dist": 0.090,
        "max_sl_dist": 0.280,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "GBP/JPY": {
        "mt5_name": "GBPJPY",
        "tv_header_name": "British Pound / Japanese Yen",
        "cmd_keys": ["/gbpjpy", "gbpjpy", "/gj", "gj"],
        "tv_tickers": ["FX:GBPJPY", "FOREXCOM:GBPJPY", "OANDA:GBPJPY"],
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbols": ["GBPJPY=X"],
        "currencies": ["GBP", "JPY"],
        "decimals": 3,
        "spread": 0.024,
        "unit_name": "Pips",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 0.110,
        "max_sl_dist": 0.320,
        "max_lot_cap": 0.25,
        "is_crypto": False
    },
    "AUD/USD": {
        "mt5_name": "AUDUSD",
        "tv_header_name": "Australian Dollar / U.S. Dollar",
        "cmd_keys": ["/audusd", "audusd", "/aud", "aud"],
        "tv_tickers": ["FX:AUDUSD", "FOREXCOM:AUDUSD", "OANDA:AUDUSD"],
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbols": ["AUDUSD=X"],
        "currencies": ["AUD", "USD"],
        "decimals": 5,
        "spread": 0.00014,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0007,
        "max_sl_dist": 0.0023,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "USD/CAD": {
        "mt5_name": "USDCAD",
        "tv_header_name": "U.S. Dollar / Canadian Dollar",
        "cmd_keys": ["/usdcad", "usdcad", "/cad", "cad"],
        "tv_tickers": ["FX:USDCAD", "FOREXCOM:USDCAD", "OANDA:USDCAD"],
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbols": ["CAD=X"],
        "currencies": ["USD", "CAD"],
        "decimals": 5,
        "spread": 0.00015,
        "unit_name": "Pips",
        "usd_exposure": "LONG_USD_ON_BUY",
        "min_sl_dist": 0.0008,
        "max_sl_dist": 0.0025,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "BTC/USD": {
        "mt5_name": "BTCUSD",
        "tv_header_name": "Bitcoin / U.S. Dollar",
        "cmd_keys": ["/btcusd", "btcusd", "/btc", "btc"],
        "tv_tickers": ["BITSTAMP:BTCUSD", "COINBASE:BTCUSD", "BINANCE:BTCUSDT"],
        "tv_screener": "crypto",
        "binance_symbol": "BTCUSDT",
        "yf_symbols": ["BTC-USD"],
        "currencies": ["USD"],
        "decimals": 2,
        "spread": 25.00,
        "unit_name": "Pts",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 180.0,
        "max_sl_dist": 620.0,
        "max_lot_cap": 0.10,
        "is_crypto": True
    }
}

last_candle_traded     = {name: None for name in FOREX_PAIRS}
last_setup_traded      = {name: None for name in FOREX_PAIRS}
pair_cooldown_until    = {name: datetime.min.replace(tzinfo=timezone.utc) for name in FOREX_PAIRS}
sl_direction_lock      = {name: {"BUY": datetime.min.replace(tzinfo=timezone.utc), "SELL": datetime.min.replace(tzinfo=timezone.utc)} for name in FOREX_PAIRS}
last_global_trade_time = datetime.min.replace(tzinfo=timezone.utc)
global_pause_until     = datetime.min.replace(tzinfo=timezone.utc)
live_bars_memory       = {name: {} for name in FOREX_PAIRS}
ohlc_history_cache     = {}

cached_news_events     = []
last_news_fetch_time   = 0
chart_rotation_index   = 0

trades_today           = 0
consecutive_losses     = 0
consecutive_wins       = 0
last_update_id         = 0
daily_trades_log       = []
daily_realized_pnl     = 0.0
total_account_pnl      = 0.0
challenge_completed    = False
daily_halt_announced   = False

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

current_day = get_ist_now().date()

def is_forex_market_open():
    now = get_ist_now()
    wd = now.weekday()
    if wd == 5 and (now.hour + now.minute / 60.0) >= 3.5:
        return False
    if wd == 6:
        return False
    if wd == 0 and (now.hour + now.minute / 60.0) < 3.5:
        return False
    return True

def get_trading_session(is_crypto=False):
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)
    if is_crypto:
        return True, "Active"
    if not is_forex_market_open():
        return False, "Weekend Closed"
    if 3.0 <= hr <= 5.15:
        return False, "Rollover Pause"
    return True, "Active"

def send_telegram_message(message, target_chat_id=CHAT_ID):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print(f"\n[TELEGRAM]:\n{message}\n")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": message, "parse_mode": "HTML"}
    try:
        HTTP_SESSION.post(url, json=payload, timeout=8)
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def format_price(price, decimals):
    return f"{price:.{decimals}f}"

def format_tv_comma_price(price, decimals):
    return f"{price:,.{decimals}f}"

def calculate_pips_or_points(pair_name, price_diff, decimals):
    price_diff = abs(float(price_diff))
    if "XAU" in pair_name or "BTC" in pair_name:
        return f"${price_diff:.2f}"
    elif decimals == 3:
        return f"{price_diff * 100:.1f} Pips"
    else:
        return f"{price_diff * 10000:.1f} Pips"

def calculate_exact_mt5_lot(pair_name, pair_info, sl_dist, risk_usd, live_usdjpy_rate=152.0, live_usdcad_rate=1.39):
    if sl_dist <= 0:
        return 0.01
    if "XAU" in pair_name:
        loss_per_1_lot = sl_dist * 100.0
    elif "BTC" in pair_name:
        loss_per_1_lot = sl_dist * 1.0
    elif pair_name.endswith("JPY"):
        usdjpy = live_usdjpy_rate if live_usdjpy_rate > 80.0 else 152.0
        loss_per_1_lot = (sl_dist * 100000.0) / usdjpy
    elif pair_name.endswith("CAD"):
        usdcad = live_usdcad_rate if live_usdcad_rate > 0.8 else 1.39
        loss_per_1_lot = (sl_dist * 100000.0) / usdcad
    else:
        pips = sl_dist * 10000.0
        loss_per_1_lot = pips * 10.0

    if loss_per_1_lot <= 0:
        return 0.01
    raw_lot = risk_usd / loss_per_1_lot
    max_cap = pair_info.get("max_lot_cap", 0.30)
    return max(0.01, min(round(raw_lot, 2), max_cap))

def get_live_account_metrics():
    est_eq = STARTING_CAPITAL_USD + total_account_pnl
    return est_eq, est_eq, daily_realized_pnl

# =====================================================================
# 2. RISK, NEWS & STRICT OPPOSING USD SHIELD
# =====================================================================
def pre_flight_risk_check():
    global challenge_completed, daily_halt_announced
    now_utc = get_utc_now()
    bal, eq, day_pnl = get_live_account_metrics()

    if eq >= TARGET_EQUITY_USD:
        if not challenge_completed:
            challenge_completed = True
            send_telegram_message(f"🏆 <b>TARGET ACHIEVED: ${eq:.2f}</b>")
        return False, 0.0

    if eq <= HARD_EQUITY_FLOOR_USD or day_pnl <= -DAILY_MAX_LOSS_STOP_USD:
        if not daily_halt_announced:
            daily_halt_announced = True
            send_telegram_message(f"🛡️ <b>DAILY RISK LIMIT REACHED (${day_pnl:+.2f}) — TRADING PAUSED FOR TODAY</b>")
        return False, 0.0

    if day_pnl >= DAILY_PROFIT_LOCK_USD:
        if not daily_halt_announced:
            daily_halt_announced = True
            send_telegram_message(f"✅ <b>DAILY PROFIT TARGET LOCKED (+${day_pnl:.2f})</b>")
        return False, 0.0

    if trades_today >= MAX_DAILY_TRADES_CAP or now_utc < global_pause_until:
        return False, 0.0

    # Allow up to 2 simultaneous un-trailed trades so slow pairs never freeze the rest of the scanner
    untrailed_open = sum(1 for t in daily_trades_log if t["is_open"] and not t.get("trailed_to_cost", False))
    if untrailed_open >= 2:
        return False, 0.0

    remaining_to_goal = TARGET_EQUITY_USD - eq
    if consecutive_losses >= 1 or day_pnl <= -15.0:
        risk_usd = CAUTION_RISK_PER_TRADE_USD
    elif consecutive_wins >= 1 and day_pnl > 0:
        risk_usd = WIN_STREAK_RISK_USD
    else:
        risk_usd = BASE_RISK_PER_TRADE_USD

    if remaining_to_goal < (risk_usd * 1.2):
        risk_usd = max(MIN_RISK_PER_TRADE_USD, round(remaining_to_goal * 0.80, 1))

    max_allowed_now = DAILY_MAX_LOSS_STOP_USD - abs(min(0.0, day_pnl))
    risk_usd = max(MIN_RISK_PER_TRADE_USD, min(risk_usd, max_allowed_now))

    return True, round(risk_usd, 1)

def fetch_live_economic_calendar():
    global cached_news_events, last_news_fetch_time
    now_ts = time.time()
    if (now_ts - last_news_fetch_time) < 1800 and cached_news_events:
        return cached_news_events
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        res = HTTP_SESSION.get(url, timeout=6)
        if res.status_code == 200:
            data = res.json()
            high_events = []
            for item in data:
                impact = str(item.get("impact", "")).strip()
                if impact in ["High", "Holiday"]:
                    title    = str(item.get("title", "Macro Event"))
                    country  = str(item.get("country", "USD")).upper()
                    date_str = str(item.get("date", ""))
                    try:
                        evt_dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                        high_events.append({
                            "id": f"{country}_{title}_{evt_dt.strftime('%Y%m%d%H%M')}",
                            "title": title, "country": country, "time_utc": evt_dt, "impact": impact
                        })
                    except Exception:
                        pass
            cached_news_events = high_events
            last_news_fetch_time = now_ts
    except Exception:
        pass
    return cached_news_events

def check_news_window_for_pair(pair_info):
    events = fetch_live_economic_calendar()
    now_utc = get_utc_now()
    pair_ccy = pair_info.get("currencies", ["USD"])
    for ev in events:
        if ev["impact"] != "High":
            continue
        if ev["country"] not in pair_ccy and "USD" not in ev["country"]:
            continue
        diff_mins = (ev["time_utc"] - now_utc).total_seconds() / 60.0
        if -10.0 <= diff_mins <= 15.0:
            return False
    return True

def check_strict_usd_conflict_shield(pair_info, candidate_direction):
    """
    100% blocks opposing USD trades (e.g., XAUUSD SELL vs BTCUSD BUY or EURUSD BUY),
    while allowing non-conflicting setups so 10+ daily trades can flow smoothly.
    """
    exp_type = pair_info.get("usd_exposure", "NEUTRAL")
    if exp_type == "NEUTRAL":
        return True

    cand_usd_dir = "SHORT_USD" if (
        (exp_type == "SHORT_USD_ON_BUY" and candidate_direction == "BUY") or
        (exp_type == "LONG_USD_ON_BUY" and candidate_direction == "SELL")
    ) else "LONG_USD"

    same_usd_untrailed = 0
    for t in daily_trades_log:
        if t["is_open"]:
            t_info = FOREX_PAIRS.get(t["pair"], {})
            t_exp  = t_info.get("usd_exposure", "NEUTRAL")
            if t_exp == "NEUTRAL":
                continue
            open_usd_dir = "SHORT_USD" if (
                (t_exp == "SHORT_USD_ON_BUY" and t["direction"] == "BUY") or
                (t_exp == "LONG_USD_ON_BUY" and t["direction"] == "SELL")
            ) else "LONG_USD"

            # Never allow opposing USD direction while an existing USD trade is open!
            if open_usd_dir != cand_usd_dir:
                return False
            if not t.get("trailed_to_cost", False):
                same_usd_untrailed += 1

    if same_usd_untrailed >= 2:
        return False
    return True

# =====================================================================
# 3. 1H (ANALYSIS) + 5M (EXECUTION) REAL-TIME SPOT DATA ENGINE
# =====================================================================
def fetch_tradingview_median_spot(tv_tickers, screener="forex"):
    if isinstance(tv_tickers, str):
        tv_tickers = [tv_tickers]
    url = f"https://scanner.tradingview.com/{screener}/scan?cb={time.time_ns()}"
    payload = {"symbols": {"tickers": tv_tickers, "query": {"types": []}}, "columns": ["close"]}
    spots = []
    try:
        res = HTTP_SESSION.post(url, json=payload, timeout=3.5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            for item in data:
                d_arr = item.get("d", [])
                if d_arr and d_arr[0] is not None:
                    val = float(d_arr[0])
                    if val > 0:
                        spots.append(val)
    except Exception:
        pass
    if spots:
        return float(np.median(spots))
    return None

def fetch_instant_live_tick(info):
    if info.get("is_crypto") and info.get("binance_symbol"):
        sym = info["binance_symbol"]
        for t_url in [
            f"https://data-api.binance.vision/api/v3/ticker/price?symbol={sym}&cb={time.time_ns()}",
            f"https://api.binance.com/api/v3/ticker/price?symbol={sym}&cb={time.time_ns()}"
        ]:
            try:
                r = HTTP_SESSION.get(t_url, timeout=2.5).json()
                if isinstance(r, dict) and "price" in r:
                    p = float(r["price"])
                    if p > 0:
                        return p
            except Exception:
                pass
    return fetch_tradingview_median_spot(info.get("tv_tickers", []), info.get("tv_screener", "forex"))

def fetch_binance_vision_ohlc(binance_symbol, interval_str="1h", limit=160):
    if not binance_symbol:
        return None
    for base_url in ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]:
        params = {"symbol": binance_symbol, "interval": interval_str, "limit": limit}
        try:
            r = HTTP_SESSION.get(base_url, params=params, timeout=5).json()
            if isinstance(r, list) and len(r) >= 40:
                df = pd.DataFrame(r, columns=["open_time", "Open", "High", "Low", "Close", "Volume", "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"])
                df["Date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                df.set_index("Date", inplace=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = df[col].astype(float)
                return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            pass
    return None

def sanitize_ohlc_dataframe(df, min_bars=35):
    if df is None or df.empty:
        return None
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] for c in df.columns]
    df = df[~df.index.duplicated(keep="last")]
    for col in ["Open", "High", "Low", "Close"]:
        if col not in df.columns:
            return None
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)
    df = df[(df["Close"] > 0) & ((df["High"] > df["Low"]) | (df["Open"] != df["Close"]))]
    if len(df) < min_bars:
        return None

    top_body = df[["Open", "Close"]].max(axis=1)
    bot_body = df[["Open", "Close"]].min(axis=1)
    df["High"] = np.maximum(df["High"], top_body)
    df["Low"]  = np.minimum(df["Low"], bot_body)

    if "Volume" not in df.columns:
        df["Volume"] = 1.0
    return df[["Open", "High", "Low", "Close", "Volume"]]

def get_1h_and_5m_spot_data(pair_name, info, use_fast_cache=False):
    """
    Fetches ONLY the two required timeframes:
    1. df_1h (1-Hour Chart for Big-Timeframe Market & Zone Analysis)
    2. df_5m (5-Minute Chart for Sniper Trade Execution)
    3. live_spot_price (100% real-time sub-second tick)
    """
    now_ts     = time.time()
    now_utc    = get_utc_now()
    bucket_min = (now_utc.minute // 5) * 5
    bucket_ts  = now_utc.replace(minute=bucket_min, second=0, microsecond=0)

    raw_df_1h, raw_df_5m = None, None

    if use_fast_cache and pair_name in ohlc_history_cache:
        cached = ohlc_history_cache[pair_name]
        if (now_ts - cached["ts"]) <= 60.0 and cached.get("bucket_ts") == bucket_ts:
            raw_df_1h = cached["raw_df_1h"].copy()
            raw_df_5m = cached["raw_df_5m"].copy()

    if raw_df_1h is None or raw_df_5m is None:
        if info.get("is_crypto") and info.get("binance_symbol"):
            raw_df_1h = fetch_binance_vision_ohlc(info["binance_symbol"], "1h", 160)
            raw_df_5m = fetch_binance_vision_ohlc(info["binance_symbol"], "5m", 140)

        if raw_df_1h is None or raw_df_5m is None:
            for yf_sym in info.get("yf_symbols", []):
                try:
                    tkr  = yf.Ticker(yf_sym)
                    r_1h = tkr.history(period="15d", interval="1h", auto_adjust=False)
                    r_5m = tkr.history(period="5d", interval="5m", auto_adjust=False)
                    c_1h = sanitize_ohlc_dataframe(r_1h, min_bars=40)
                    c_5m = sanitize_ohlc_dataframe(r_5m, min_bars=50)
                    if c_1h is not None and c_5m is not None:
                        raw_df_1h, raw_df_5m = c_1h, c_5m
                        break
                except Exception:
                    continue

        raw_df_1h = sanitize_ohlc_dataframe(raw_df_1h, min_bars=40)
        raw_df_5m = sanitize_ohlc_dataframe(raw_df_5m, min_bars=50)
        if raw_df_1h is None or raw_df_5m is None:
            return None, None, None

        ohlc_history_cache[pair_name] = {
            "ts": now_ts,
            "bucket_ts": bucket_ts,
            "raw_df_1h": raw_df_1h.copy(),
            "raw_df_5m": raw_df_5m.copy()
        }

    df_1h = raw_df_1h.copy()
    df_5m = raw_df_5m.copy()

    tv_spot = fetch_instant_live_tick(info)
    raw_last_close = float(df_5m.iloc[-1]["Close"])

    if tv_spot is not None and tv_spot > 0:
        basis_diff = tv_spot - raw_last_close
        if abs(basis_diff) <= (tv_spot * 0.05):
            mem = live_bars_memory[pair_name]
            if bucket_ts not in mem:
                for col in ["Open", "High", "Low", "Close"]:
                    df_5m[col] = df_5m[col] + basis_diff
                    df_1h[col] = df_1h[col] + basis_diff
                mem[bucket_ts] = {
                    "basis_at_open": basis_diff,
                    "High": float(df_5m.iloc[-1]["High"]),
                    "Low": float(df_5m.iloc[-1]["Low"])
                }
            else:
                base_shift = mem[bucket_ts]["basis_at_open"]
                for col in ["Open", "High", "Low", "Close"]:
                    df_5m[col] = df_5m[col] + base_shift
                    df_1h[col] = df_1h[col] + base_shift

            if info.get("is_crypto", False) or is_forex_market_open():
                mem[bucket_ts]["High"] = max(mem[bucket_ts]["High"], tv_spot)
                mem[bucket_ts]["Low"]  = min(mem[bucket_ts]["Low"], tv_spot)

                last_idx_5m = df_5m.index[-1]
                df_5m.loc[last_idx_5m, "Close"] = tv_spot
                df_5m.loc[last_idx_5m, "High"]  = max(float(df_5m.loc[last_idx_5m, "High"]), mem[bucket_ts]["High"])
                df_5m.loc[last_idx_5m, "Low"]   = min(float(df_5m.loc[last_idx_5m, "Low"]), mem[bucket_ts]["Low"])

                last_idx_1h = df_1h.index[-1]
                df_1h.loc[last_idx_1h, "Close"] = tv_spot
                df_1h.loc[last_idx_1h, "High"]  = max(float(df_1h.loc[last_idx_1h, "High"]), tv_spot)
                df_1h.loc[last_idx_1h, "Low"]   = min(float(df_1h.loc[last_idx_1h, "Low"]), tv_spot)

            live_spot_price = tv_spot
        else:
            live_spot_price = raw_last_close
    else:
        live_spot_price = raw_last_close

    return df_1h, df_5m, live_spot_price

# =====================================================================
# 4. 1H MARKET ANALYSIS -> 5M SNIPER EXECUTION ENGINE
# =====================================================================
def enrich_price_action_metrics(df):
    df = df.copy()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()
    hl = df["High"] - df["Low"]
    hc = np.abs(df["High"] - df["Close"].shift())
    lc = np.abs(df["Low"] - df["Close"].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["ATR"]        = tr.rolling(14).mean().bfill()
    df["Body"]       = np.abs(df["Close"] - df["Open"])
    df["Range"]      = (df["High"] - df["Low"]).replace(0, 1e-9)
    df["Body_Ratio"] = df["Body"] / df["Range"]
    df["Upper_Wick"] = df["High"] - df[["Open", "Close"]].max(axis=1)
    df["Lower_Wick"] = df[["Open", "Close"]].min(axis=1) - df["Low"]
    df["Close_Pos"]  = (df["Close"] - df["Low"]) / df["Range"]
    avg_body         = df["Body"].rolling(16).mean().replace(0, 1e-9).bfill()
    df["Disp_Power"] = df["Body"] / avg_body
    return df

def build_1h_institutional_analysis(df_1h, live_spot, decimals):
    """
    Analyzes ONLY the 1-Hour (1H) Chart:
    1. Determines 1H Market Structure Trend (BULLISH / BEARISH).
    2. Maps 3 to 4 active 1H Institutional Supply/Demand Zones within the active trading range
       around live_spot (so zones are never 10 days away from current price).
    """
    sub = df_1h.iloc[-90:].copy()
    atr_1h = float(sub.iloc[-2]["ATR"])
    if np.isnan(atr_1h) or atr_1h <= 0:
        atr_1h = float((sub["High"] - sub["Low"]).mean())

    c1_1h    = sub.iloc[-2]
    h1_close = float(sub.iloc[-1]["Close"])
    h1_ema20 = float(c1_1h["EMA20"])
    h1_ema50 = float(c1_1h["EMA50"])

    highs  = sub["High"].values.astype(float)
    lows   = sub["Low"].values.astype(float)
    opens  = sub["Open"].values.astype(float)
    closes = sub["Close"].values.astype(float)
    n_bars = len(sub)

    # Extract 1H Swing Highs, Swing Lows & Order Block Rejection Necks within active vicinity (± 4.5x 1H ATR)
    pivots = []
    max_vicinity = atr_1h * 4.5

    for i in range(2, n_bars - 1):
        recency_boost = 1.35 if i >= (n_bars - 36) else 1.0

        # 1H Fractal Swing High
        if highs[i] >= max(highs[i - 2:i]) and highs[i] >= highs[i + 1]:
            neck = max(opens[i], closes[i])
            p_lvl = (highs[i] + neck) / 2.0
            if abs(p_lvl - live_spot) <= max_vicinity:
                pivots.append({"price": p_lvl, "weight": 2.6 * recency_boost, "idx": i})

        # 1H Fractal Swing Low
        if lows[i] <= min(lows[i - 2:i]) and lows[i] <= lows[i + 1]:
            neck = min(opens[i], closes[i])
            p_lvl = (lows[i] + neck) / 2.0
            if abs(p_lvl - live_spot) <= max_vicinity:
                pivots.append({"price": p_lvl, "weight": 2.6 * recency_boost, "idx": i})

        # 1H Strong Rejection Wicks
        u_w = highs[i] - max(opens[i], closes[i])
        l_w = min(opens[i], closes[i]) - lows[i]
        if u_w > atr_1h * 0.38 and abs(highs[i] - live_spot) <= max_vicinity:
            pivots.append({"price": highs[i] - u_w * 0.30, "weight": 1.6 * recency_boost, "idx": i})
        if l_w > atr_1h * 0.38 and abs(lows[i] - live_spot) <= max_vicinity:
            pivots.append({"price": lows[i] + l_w * 0.30, "weight": 1.6 * recency_boost, "idx": i})

    # Cluster 1H pivots into clean horizontal zones
    cluster_radius = atr_1h * 0.28
    min_zone_gap   = atr_1h * 0.72
    zone_half_h    = atr_1h * 0.11

    clusters = []
    for p in sorted(pivots, key=lambda x: x["price"]):
        placed = False
        for c in clusters:
            if abs(p["price"] - c["center"]) <= cluster_radius:
                total_w = c["weight"] + p["weight"]
                c["center"] = (c["center"] * c["weight"] + p["price"] * p["weight"]) / total_w
                c["weight"] = total_w
                c["min_idx"] = min(c["min_idx"], p["idx"])
                c["touches"] += 1
                placed = True
                break
        if not placed:
            clusters.append({
                "center": p["price"],
                "weight": p["weight"],
                "min_idx": p["idx"],
                "touches": 1
            })

    # Prioritize strong 1H zones closest to current live_spot
    clusters.sort(key=lambda x: (x["weight"] / (1.0 + abs(x["center"] - live_spot) / max(atr_1h, 1e-6))), reverse=True)
    selected = []
    for c in clusters:
        if all(abs(c["center"] - s["center"]) >= min_zone_gap for s in selected):
            selected.append(c)
        if len(selected) >= 4:
            break

    # Fallback to recent 24-hour 1H High/Low/Mid structure if fewer than 3 zones found
    if len(selected) < 3:
        recent_24h = sub.iloc[-24:-1]
        r_hi = float(recent_24h["High"].max())
        r_lo = float(recent_24h["Low"].min())
        r_md = (r_hi + r_lo) / 2.0
        for rx in [r_hi, r_md, r_lo]:
            if all(abs(rx - s["center"]) >= (atr_1h * 0.55) for s in selected):
                selected.append({"center": rx, "weight": 2.0, "min_idx": max(0, n_bars - 24), "touches": 2})
            if len(selected) >= 4:
                break

    selected.sort(key=lambda x: x["center"])

    zones = []
    for z in selected:
        c_val = z["center"]
        z_top = round(c_val + zone_half_h, decimals)
        z_bot = round(c_val - zone_half_h, decimals)
        z_mid = round((z_top + z_bot) / 2.0, decimals)
        zones.append({
            "top": z_top,
            "mid": z_mid,
            "bot": z_bot,
            "center": round(c_val, decimals),
            "touches": z["touches"],
            "start_idx": max(0, min(z["min_idx"], 12))
        })

    # Determine 1H Trend Bias
    h1_bull = (h1_close >= h1_ema20) or (h1_ema20 >= h1_ema50 and float(c1_1h["Close"]) > float(c1_1h["Open"]))
    h1_bias = "BULLISH" if h1_bull else "BEARISH"

    lowest_zone_bot  = zones[0]["bot"]
    highest_zone_top = zones[-1]["top"]
    lower_runner_lvl = round(min(float(np.min(lows[-30:])), lowest_zone_bot - atr_1h * 0.95), decimals)
    upper_runner_lvl = round(max(float(np.max(highs[-30:])), highest_zone_top + atr_1h * 0.95), decimals)

    return zones, atr_1h, h1_bias, lower_runner_lvl, upper_runner_lvl

def evaluate_1h_to_5m_setup(pair_name, info, df_1h, df_5m, live_spot, ignore_locks=False):
    """
    1H ANALYSIS -> 5M SNIPER EXECUTION (4 High-Probability Setups for 10+ Trades/Day):
    1. Setup 1: 1H Zone Breakout + 5M Candle Close & Follow-Through (⬆️ BUY / ⬇️ SELL)
    2. Setup 2: 1H Zone Support/Resistance Bounce + 5M Engulfing Structure Shift
    3. Setup 3: 1H Zone Liquidity Sweep (Stop-Hunt) + 5M Immediate Reclaim
    4. Setup 4: 1H Trend Corridor Impulse + 5M Pullback Continuation Break
    """
    now_utc = get_utc_now()
    if not ignore_locks:
        if now_utc < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
            return None
        session_ok, _ = get_trading_session(info.get("is_crypto", False))
        if not session_ok:
            return None
        if not check_news_window_for_pair(info):
            return None

    dec = info["decimals"]
    zones, atr_1h, h1_bias, lower_runner, upper_runner = build_1h_institutional_analysis(df_1h, live_spot, dec)
    if not zones or atr_1h <= 0:
        return None

    # 5M Execution Candles (-2 is just closed 5M trigger candle, -3 & -4 are prior 5M candles)
    m5_c0 = df_5m.iloc[-2]
    m5_c1 = df_5m.iloc[-3]
    m5_c2 = df_5m.iloc[-4]

    p_close  = float(m5_c0["Close"])
    p_open   = float(m5_c0["Open"])
    p_high   = float(m5_c0["High"])
    p_low    = float(m5_c0["Low"])
    atr_5m   = float(m5_c0["ATR"])
    body_r   = float(m5_c0["Body_Ratio"])
    close_p  = float(m5_c0["Close_Pos"])
    u_wick   = float(m5_c0["Upper_Wick"])
    l_wick   = float(m5_c0["Lower_Wick"])
    rng_5    = float(m5_c0["Range"])
    m5_ema20 = float(m5_c0["EMA20"])
    m5_ema50 = float(m5_c0["EMA50"])

    if np.isnan(atr_5m) or atr_5m <= 0 or rng_5 > (atr_5m * 1.95):
        return None

    candidates = []

    # =================================================================
    # SETUPS 1, 2 & 3: AT 1H INSTITUTIONAL ZONES (EXECUTED ON 5M)
    # =================================================================
    for idx, z in enumerate(zones):
        z_top = z["top"]
        z_mid = z["mid"]
        z_bot = z["bot"]
        z_id  = f"1H_ZONE_{idx}_{z_bot}_{z_top}"

        next_upper_target = zones[idx + 1]["bot"] if (idx + 1) < len(zones) else upper_runner
        next_lower_target = zones[idx - 1]["top"] if (idx - 1) >= 0 else lower_runner

        # -------------------------------------------------------------
        # BUY TRIGGERS ON 5M (⬆️ GREEN ARROW ABOVE 1H ZONE)
        # -------------------------------------------------------------
        recent_3_low  = min(p_low, float(m5_c1["Low"]), float(m5_c2["Low"]))
        recent_3_high = max(p_high, float(m5_c1["High"]), float(m5_c2["High"]))

        # Trigger 1: 1H Zone Breakout / Retest on 5M
        t1_bull_break = (
            (recent_3_low <= z_top + atr_5m * 0.35) and
            (recent_3_low >= z_bot - atr_5m * 0.25) and
            (p_close > z_top + atr_5m * 0.05) and
            (live_spot > z_top) and
            (p_close > p_open) and
            (body_r >= 0.42) and
            (close_p >= 0.62) and
            ((u_wick / rng_5) <= 0.30)
        )

        # Trigger 2: 1H Support Zone Bounce + 5M Bullish Structure Shift (Close > prev 5m High)
        t2_bull_bounce = (
            (recent_3_low <= z_top + atr_5m * 0.20) and
            (recent_3_low >= z_bot - atr_5m * 0.30) and
            (p_close >= z_mid) and
            (live_spot >= z_mid) and
            (p_close > p_open) and
            (p_close > float(m5_c1["High"])) and
            (body_r >= 0.45) and
            (close_p >= 0.64) and
            ((u_wick / rng_5) <= 0.28)
        )

        # Trigger 3: 1H Zone Stop-Hunt Liquidity Sweep below z_bot + Immediate 5M Reclaim
        t3_bull_sweep = (
            (recent_3_low < z_bot) and
            (recent_3_low >= z_bot - atr_5m * 1.10) and
            (p_close > z_bot + atr_5m * 0.05) and
            (live_spot > z_bot) and
            (p_close > p_open) and
            (p_close > float(m5_c1["Close"])) and
            (l_wick >= rng_5 * 0.25 or body_r >= 0.48) and
            (close_p >= 0.62)
        )

        if (t1_bull_break or t2_bull_bounce or t3_bull_sweep) and (ignore_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]):
            raw_sl  = min(z_bot, recent_3_low) - (atr_5m * 0.22)
            sl_dist = abs(live_spot - raw_sl)

            if sl_dist <= info["max_sl_dist"]:
                sl_dist  = max(info["min_sl_dist"], sl_dist)
                sl_price = round(live_spot - sl_dist, dec)

                corridor_space = next_upper_target - live_spot
                # Smart Target: If next 1H zone is at least 1.15R away, use it; if wider or breakout of top zone, use 1.50R
                if corridor_space >= (sl_dist * 1.15):
                    tp2_dist  = min(max(corridor_space * 0.95, sl_dist * MIN_CORRIDOR_RR_RATIO), sl_dist * 1.95)
                    tp2_price = round(live_spot + tp2_dist, dec)
                    tp1_price = round(live_spot + (tp2_dist * TP1_PARTIAL_RATIO), dec)
                    lock_lvl  = round(live_spot + (sl_dist * LOCK_PROFIT_R), dec)
                    rr_ratio  = round(tp2_dist / sl_dist, 2)

                    if t1_bull_break:
                        s_tag = "1H Zone Breakout + 5M Close"
                    elif t2_bull_bounce:
                        s_tag = "1H Support Bounce + 5M Shift"
                    else:
                        s_tag = "1H Zone Sweep + 5M Reclaim"

                    sc = 86 + (4 if h1_bias == "BULLISH" else 0)
                    candidates.append({
                        "direction": "BUY",
                        "action": "BUY (LONG) 🟢",
                        "short_tag": s_tag,
                        "zone_idx": idx,
                        "setup_id": f"{z_id}_BUY_{df_5m.index[-2]}",
                        "sl_price": sl_price,
                        "sl_dist": round(sl_dist, dec),
                        "lock_level": lock_lvl,
                        "tp1_price": tp1_price,
                        "tp2_price": tp2_price,
                        "tp2_dist": round(tp2_dist, dec),
                        "rr": rr_ratio,
                        "score": sc,
                        "why_text": (
                            f"• <b>1H Analysis:</b> Key 1H Zone (<code>{format_price(z_bot, dec)} – {format_price(z_top, dec)}</code>) held with {h1_bias} structure.\n"
                            f"• <b>5M Execution (⬆️):</b> {s_tag} confirmed; targeting 1H upper zone at <code>{format_price(tp2_price, dec)}</code>."
                        )
                    })

        # -------------------------------------------------------------
        # SELL TRIGGERS ON 5M (⬇️ RED ARROW BELOW 1H ZONE)
        # -------------------------------------------------------------
        t1_bear_break = (
            (recent_3_high >= z_bot - atr_5m * 0.35) and
            (recent_3_high <= z_top + atr_5m * 0.25) and
            (p_close < z_bot - atr_5m * 0.05) and
            (live_spot < z_bot) and
            (p_close < p_open) and
            (body_r >= 0.42) and
            (close_p <= 0.38) and
            ((l_wick / rng_5) <= 0.30)
        )

        t2_bear_bounce = (
            (recent_3_high >= z_bot - atr_5m * 0.20) and
            (recent_3_high <= z_top + atr_5m * 0.30) and
            (p_close <= z_mid) and
            (live_spot <= z_mid) and
            (p_close < p_open) and
            (p_close < float(m5_c1["Low"])) and
            (body_r >= 0.45) and
            (close_p <= 0.36) and
            ((l_wick / rng_5) <= 0.28)
        )

        t3_bear_sweep = (
            (recent_3_high > z_top) and
            (recent_3_high <= z_top + atr_5m * 1.10) and
            (p_close < z_top - atr_5m * 0.05) and
            (live_spot < z_top) and
            (p_close < p_open) and
            (p_close < float(m5_c1["Close"])) and
            (u_wick >= rng_5 * 0.25 or body_r >= 0.48) and
            (close_p <= 0.38)
        )

        if (t1_bear_break or t2_bear_bounce or t3_bear_sweep) and (ignore_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]):
            raw_sl  = max(z_top, recent_3_high) + (atr_5m * 0.22)
            sl_dist = abs(raw_sl - live_spot)

            if sl_dist <= info["max_sl_dist"]:
                sl_dist  = max(info["min_sl_dist"], sl_dist)
                sl_price = round(live_spot + sl_dist, dec)

                corridor_space = live_spot - next_lower_target
                if corridor_space >= (sl_dist * 1.15):
                    tp2_dist  = min(max(corridor_space * 0.95, sl_dist * MIN_CORRIDOR_RR_RATIO), sl_dist * 1.95)
                    tp2_price = round(live_spot - tp2_dist, dec)
                    tp1_price = round(live_spot - (tp2_dist * TP1_PARTIAL_RATIO), dec)
                    lock_lvl  = round(live_spot - (sl_dist * LOCK_PROFIT_R), dec)
                    rr_ratio  = round(tp2_dist / sl_dist, 2)

                    if t1_bear_break:
                        s_tag = "1H Zone Breakdown + 5M Close"
                    elif t2_bear_bounce:
                        s_tag = "1H Supply Reject + 5M Shift"
                    else:
                        s_tag = "1H Zone Sweep + 5M Reject"

                    sc = 86 + (4 if h1_bias == "BEARISH" else 0)
                    candidates.append({
                        "direction": "SELL",
                        "action": "SELL (SHORT) 🔴",
                        "short_tag": s_tag,
                        "zone_idx": idx,
                        "setup_id": f"{z_id}_SELL_{df_5m.index[-2]}",
                        "sl_price": sl_price,
                        "sl_dist": round(sl_dist, dec),
                        "lock_level": lock_lvl,
                        "tp1_price": tp1_price,
                        "tp2_price": tp2_price,
                        "tp2_dist": round(tp2_dist, dec),
                        "rr": rr_ratio,
                        "score": sc,
                        "why_text": (
                            f"• <b>1H Analysis:</b> Key 1H Zone (<code>{format_price(z_bot, dec)} – {format_price(z_top, dec)}</code>) rejected with {h1_bias} structure.\n"
                            f"• <b>5M Execution (⬇️):</b> {s_tag} confirmed; targeting 1H lower zone at <code>{format_price(tp2_price, dec)}</code>."
                        )
                    })

    # =================================================================
    # SETUP 4: 1H TREND CORRIDOR + 5M PULLBACK CONTINUATION
    # (Triggers when price is moving cleanly between 1H zones in 1H Trend direction)
    # =================================================================
    inside_any_1h_zone = any((z["bot"] - atr_5m * 0.15) <= live_spot <= (z["top"] + atr_5m * 0.15) for z in zones)
    if not inside_any_1h_zone:
        upper_z = next((z for z in zones if z["bot"] > live_spot), None)
        lower_z = next((z for z in reversed(zones) if z["top"] < live_spot), None)

        win_16 = df_5m.iloc[-18:-2]
        sw_hi  = float(win_16["High"].max())
        sw_lo  = float(win_16["Low"].min())
        sw_rng = sw_hi - sw_lo

        # 1H Bullish Trend + 5M Pullback to 5M EMA20 & Bullish Engulfing Continuation
        if h1_bias == "BULLISH" and sw_rng >= (atr_5m * 1.60):
            pb_low = min(p_low, float(m5_c1["Low"]), float(m5_c2["Low"]))
            if (sw_hi - pb_low) >= (atr_5m * 0.45) and (pb_low <= m5_ema20 + atr_5m * 0.20) and (p_close > m5_ema20 >= m5_ema50 * 0.9995):
                if (p_close > p_open) and (p_close > float(m5_c1["High"])) and (body_r >= 0.46) and (close_p >= 0.64) and ((u_wick / rng_5) <= 0.28):
                    if ignore_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]:
                        raw_sl  = pb_low - (atr_5m * 0.22)
                        sl_dist = abs(live_spot - raw_sl)
                        if sl_dist <= info["max_sl_dist"]:
                            sl_dist  = max(info["min_sl_dist"], sl_dist)
                            sl_price = round(live_spot - sl_dist, dec)
                            next_t   = upper_z["bot"] if upper_z is not None else upper_runner
                            room_up  = next_t - live_spot
                            if room_up >= (sl_dist * MIN_CORRIDOR_RR_RATIO):
                                tp2_dist  = min(room_up * 0.95, sl_dist * DEFAULT_TARGET_RR_RATIO)
                                tp2_price = round(live_spot + tp2_dist, dec)
                                tp1_price = round(live_spot + (tp2_dist * TP1_PARTIAL_RATIO), dec)
                                lock_lvl  = round(live_spot + (sl_dist * LOCK_PROFIT_R), dec)
                                candidates.append({
                                    "direction": "BUY",
                                    "action": "BUY (LONG) 🟢",
                                    "short_tag": "1H Bull Trend + 5M Pullback",
                                    "zone_idx": -1,
                                    "setup_id": f"1H_CORR_BUY_{df_5m.index[-2]}",
                                    "sl_price": sl_price,
                                    "sl_dist": round(sl_dist, dec),
                                    "lock_level": lock_lvl,
                                    "tp1_price": tp1_price,
                                    "tp2_price": tp2_price,
                                    "tp2_dist": round(tp2_dist, dec),
                                    "rr": round(tp2_dist / sl_dist, 2),
                                    "score": 83,
                                    "why_text": (
                                        f"• <b>1H Analysis:</b> Strong 1H Bullish trend with open corridor toward <code>{format_price(next_t, dec)}</code>.\n"
                                        f"• <b>5M Execution (⬆️):</b> 5M pullback completed with bullish engulfing breakout above <code>{format_price(float(m5_c1['High']), dec)}</code>."
                                    )
                                })

        # 1H Bearish Trend + 5M Pullback to 5M EMA20 & Bearish Engulfing Continuation
        if h1_bias == "BEARISH" and sw_rng >= (atr_5m * 1.60):
            pb_high = max(p_high, float(m5_c1["High"]), float(m5_c2["High"]))
            if (pb_high - sw_lo) >= (atr_5m * 0.45) and (pb_high >= m5_ema20 - atr_5m * 0.20) and (p_close < m5_ema20 <= m5_ema50 * 1.0005):
                if (p_close < p_open) and (p_close < float(m5_c1["Low"])) and (body_r >= 0.46) and (close_p <= 0.36) and ((l_wick / rng_5) <= 0.28):
                    if ignore_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]:
                        raw_sl  = pb_high + (atr_5m * 0.22)
                        sl_dist = abs(raw_sl - live_spot)
                        if sl_dist <= info["max_sl_dist"]:
                            sl_dist  = max(info["min_sl_dist"], sl_dist)
                            sl_price = round(live_spot + sl_dist, dec)
                            next_t   = lower_z["top"] if lower_z is not None else lower_runner
                            room_dn  = live_spot - next_t
                            if room_dn >= (sl_dist * MIN_CORRIDOR_RR_RATIO):
                                tp2_dist  = min(room_dn * 0.95, sl_dist * DEFAULT_TARGET_RR_RATIO)
                                tp2_price = round(live_spot - tp2_dist, dec)
                                tp1_price = round(live_spot - (tp2_dist * TP1_PARTIAL_RATIO), dec)
                                lock_lvl  = round(live_spot - (sl_dist * LOCK_PROFIT_R), dec)
                                candidates.append({
                                    "direction": "SELL",
                                    "action": "SELL (SHORT) 🔴",
                                    "short_tag": "1H Bear Trend + 5M Pullback",
                                    "zone_idx": -1,
                                    "setup_id": f"1H_CORR_SELL_{df_5m.index[-2]}",
                                    "sl_price": sl_price,
                                    "sl_dist": round(sl_dist, dec),
                                    "lock_level": lock_lvl,
                                    "tp1_price": tp1_price,
                                    "tp2_price": tp2_price,
                                    "tp2_dist": round(tp2_dist, dec),
                                    "rr": round(tp2_dist / sl_dist, 2),
                                    "score": 83,
                                    "why_text": (
                                        f"• <b>1H Analysis:</b> Strong 1H Bearish trend with open corridor toward <code>{format_price(next_t, dec)}</code>.\n"
                                        f"• <b>5M Execution (⬇️):</b> 5M pullback completed with bearish breakdown below <code>{format_price(float(m5_c1['Low']), dec)}</code>."
                                    )
                                })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)

    for cand in candidates:
        if not ignore_locks:
            if last_setup_traded.get(pair_name) == cand["setup_id"]:
                continue
            if not check_strict_usd_conflict_shield(info, cand["direction"]):
                continue
        return cand

    return None

# =====================================================================
# 5. TRADINGVIEW 1H-ZONES + 5M-EXECUTION CHART GENERATOR
# =====================================================================
def draw_tradingview_arrow(ax, x_pos, y_pos, y_span, is_up=True):
    h = y_span * 0.038
    w = 1.15
    if is_up:
        pts = [
            (x_pos, y_pos + h),
            (x_pos - w, y_pos + h * 0.38),
            (x_pos - w * 0.45, y_pos + h * 0.38),
            (x_pos - w * 0.45, y_pos),
            (x_pos + w * 0.45, y_pos),
            (x_pos + w * 0.45, y_pos + h * 0.38),
            (x_pos + w, y_pos + h * 0.38)
        ]
        col = "#089981"
    else:
        pts = [
            (x_pos, y_pos - h),
            (x_pos - w, y_pos - h * 0.38),
            (x_pos - w * 0.45, y_pos - h * 0.38),
            (x_pos - w * 0.45, y_pos),
            (x_pos + w * 0.45, y_pos),
            (x_pos + w * 0.45, y_pos - h * 0.38),
            (x_pos + w, y_pos - h * 0.38)
        ]
        col = "#f23645"

    poly = Polygon(pts, closed=True, facecolor=col, edgecolor=col, linewidth=0.6, zorder=8)
    ax.add_patch(poly)

def generate_1h_zones_on_5m_chart(df_1h, df_5m, info, live_spot, active_trade=None):
    """
    Renders the exact institutional TradingView chart:
    - Maps the 1H Institutional Zones (solid black borders, dashed red 50% line, soft pink fill)
    - Displays the 5-Minute (5M) Execution Candlesticks interacting with those 1H Zones
    - Places the Green (⬆️) and Red (⬇️) Breakout/Entry Arrows and active SL/TP Position Box
    """
    try:
        sub = df_5m.iloc[-76:].copy()
        if sub.empty or len(sub) < 30:
            return None

        dec     = info["decimals"]
        mt5_sym = info["mt5_name"]
        hdr_nam = info.get("tv_header_name", mt5_sym)

        zones, atr_1h, h1_bias, lower_runner, upper_runner = build_1h_institutional_analysis(df_1h, live_spot, dec)

        fig = plt.figure(figsize=(11.2, 6.4), dpi=130, facecolor="#ffffff")
        ax  = fig.add_axes([0.015, 0.08, 0.885, 0.85], facecolor="#ffffff")

        n_bars = len(sub)
        x_vals = np.arange(n_bars)
        right_edge_idx = n_bars + 24

        ax.grid(True, which="major", linestyle="-", linewidth=0.45, color="#f0f3fa", zorder=1)
        for spine in ["top", "left"]:
            ax.spines[spine].set_visible(False)
        ax.spines["right"].set_color("#e0e3eb")
        ax.spines["bottom"].set_color("#e0e3eb")

        # Center Watermark ("XAUUSD, 1H -> 5m")
        ax.text(
            0.46, 0.50, f"{mt5_sym}, 1H ➔ 5m", transform=ax.transAxes,
            fontsize=38, fontweight="bold", color="#131722", alpha=0.09,
            ha="center", va="center", zorder=1
        )

        opens  = sub["Open"].values.astype(float)
        highs  = sub["High"].values.astype(float)
        lows   = sub["Low"].values.astype(float)
        closes = sub["Close"].values.astype(float)

        # Keep chart vertically focused on the 5M action + nearest 1H zones
        c_min, c_max = float(np.min(lows)), float(np.max(highs))
        c_span = max(c_max - c_min, atr_1h * 0.8)

        visible_zones = [
            z for z in zones
            if (c_min - c_span * 0.85) <= z["center"] <= (c_max + c_span * 0.85)
        ]
        if not visible_zones:
            visible_zones = zones[:3]

        all_y = list(highs) + list(lows) + [live_spot]
        for z in visible_zones:
            all_y.extend([z["top"], z["bot"]])
        if active_trade is not None:
            all_y.extend([active_trade["sl_price"], active_trade["tp2_price"]])

        y_min, y_max = float(np.nanmin(all_y)), float(np.nanmax(all_y))
        total_span   = max(y_max - y_min, 1e-6)
        min_body_h   = total_span * 0.0032

        bull_mask    = closes >= opens
        bear_mask    = ~bull_mask
        bot_bodies   = np.minimum(opens, closes)
        body_heights = np.maximum(np.abs(closes - opens), min_body_h)

        ax.vlines(x_vals[bull_mask], lows[bull_mask], highs[bull_mask], color="#089981", linewidth=1.0, zorder=4)
        ax.vlines(x_vals[bear_mask], lows[bear_mask], highs[bear_mask], color="#f23645", linewidth=1.0, zorder=4)
        ax.bar(x_vals[bull_mask], body_heights[bull_mask], bottom=bot_bodies[bull_mask], width=0.66, color="#089981", edgecolor="#089981", linewidth=0.35, zorder=4)
        ax.bar(x_vals[bear_mask], body_heights[bear_mask], bottom=bot_bodies[bear_mask], width=0.66, color="#f23645", edgecolor="#f23645", linewidth=0.35, zorder=4)

        arrow_base_x = n_bars + 5
        for idx, z in enumerate(visible_zones):
            z_top = z["top"]
            z_mid = z["mid"]
            z_bot = z["bot"]
            s_idx = 4

            ax.add_patch(mpatches.Rectangle(
                (s_idx, z_bot),
                right_edge_idx - s_idx,
                z_top - z_bot,
                facecolor="#f23645", alpha=0.09, edgecolor="none", zorder=2
            ))
            ax.plot([s_idx, right_edge_idx], [z_top, z_top], color="#1e222d", linewidth=0.95, zorder=3)
            ax.plot([s_idx, right_edge_idx], [z_bot, z_bot], color="#1e222d", linewidth=0.95, zorder=3)
            ax.plot([s_idx, right_edge_idx], [z_mid, z_mid], color="#f23645", linewidth=0.80, linestyle="--", alpha=0.75, zorder=3)

            up_x = arrow_base_x + (idx % 2) * 3.5
            dn_x = arrow_base_x + 5.5 + (idx % 2) * 3.0
            if active_trade is not None:
                if active_trade["direction"] == "BUY" and abs(live_spot - z_top) <= total_span * 0.25:
                    draw_tradingview_arrow(ax, n_bars + 2.5, z_top + total_span * 0.004, total_span, is_up=True)
                elif active_trade["direction"] == "SELL" and abs(live_spot - z_bot) <= total_span * 0.25:
                    draw_tradingview_arrow(ax, n_bars + 2.5, z_bot - total_span * 0.004, total_span, is_up=False)
            else:
                draw_tradingview_arrow(ax, up_x, z_top + total_span * 0.004, total_span, is_up=True)
                draw_tradingview_arrow(ax, dn_x, z_bot - total_span * 0.004, total_span, is_up=False)

        last_bull = closes[-1] >= opens[-1]
        live_col  = "#089981" if last_bull else "#f23645"
        ax.axhline(live_spot, color=live_col, linewidth=0.80, linestyle=":", alpha=0.85, zorder=5)

        if active_trade is not None:
            sl_p  = active_trade["sl_price"]
            tp1_p = active_trade["tp1_price"]
            tp2_p = active_trade["tp2_price"]
            p_x0  = n_bars - 1
            p_w   = 18

            ax.add_patch(mpatches.Rectangle(
                (p_x0, min(live_spot, sl_p)), p_w, abs(live_spot - sl_p),
                facecolor="#f23645", alpha=0.16, edgecolor="#f23645", linewidth=0.9, zorder=5
            ))
            ax.add_patch(mpatches.Rectangle(
                (p_x0, min(live_spot, tp2_p)), p_w, abs(tp2_p - live_spot),
                facecolor="#089981", alpha=0.16, edgecolor="#089981", linewidth=0.9, zorder=5
            ))
            ax.plot([p_x0, p_x0 + p_w], [tp1_p, tp1_p], color="#089981", linestyle="--", linewidth=1.0, zorder=6)
            ax.plot([p_x0, p_x0 + p_w], [tp2_p, tp2_p], color="#089981", linewidth=1.4, zorder=6)
            ax.plot([p_x0, p_x0 + p_w], [sl_p, sl_p], color="#f23645", linewidth=1.4, zorder=6)

        y_pad = total_span * 0.10
        ax.set_ylim(y_min - y_pad, y_max + y_pad)
        ax.set_xlim(-1.0, right_edge_idx)
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", labelsize=8.8, colors="#131722")
        ax.tick_params(axis="x", labelsize=8.5, colors="#131722")

        y_ticks = ax.get_yticks()
        ax.set_yticks(y_ticks)
        ax.set_yticklabels([format_tv_comma_price(yt, dec) for yt in y_ticks], fontsize=8.8, color="#131722")

        lowest_z = visible_zones[0]
        for b_y, b_txt in [
            (lowest_z["top"], format_tv_comma_price(lowest_z["top"], dec)),
            (lowest_z["bot"], format_tv_comma_price(lowest_z["bot"], dec))
        ]:
            if abs(b_y - live_spot) > (total_span * 0.035):
                ax.text(
                    right_edge_idx + 0.3, b_y, f" {b_txt} ",
                    fontsize=8.0, fontweight="bold", color="#ffffff", va="center", ha="left",
                    bbox=dict(boxstyle="square,pad=0.18", facecolor="#131722", edgecolor="none"),
                    clip_on=False, zorder=9
                )

        if active_trade is not None:
            for t_y, t_lbl, t_bg in [
                (active_trade["tp2_price"], f" TP2 {format_tv_comma_price(active_trade['tp2_price'], dec)} ", "#089981"),
                (active_trade["sl_price"], f" SL {format_tv_comma_price(active_trade['sl_price'], dec)} ", "#f23645")
            ]:
                ax.text(
                    right_edge_idx + 0.3, t_y, t_lbl,
                    fontsize=7.8, fontweight="bold", color="#ffffff", va="center", ha="left",
                    bbox=dict(boxstyle="square,pad=0.18", facecolor=t_bg, edgecolor="none"),
                    clip_on=False, zorder=9
                )

        now_utc = get_utc_now()
        rem_sec = max(0, (5 - (now_utc.minute % 5)) * 60 - now_utc.second)
        mm, ss  = divmod(rem_sec, 60)
        live_tag_txt = f" {format_tv_comma_price(live_spot, dec)} \n {mm:02d}:{ss:02d} "
        ax.text(
            right_edge_idx + 0.3, live_spot, live_tag_txt,
            fontsize=8.0, fontweight="bold", color="#ffffff", va="center", ha="left",
            bbox=dict(boxstyle="square,pad=0.20", facecolor=live_col, edgecolor="none"),
            clip_on=False, zorder=10
        )

        tick_indices = [6, int(n_bars * 0.32), int(n_bars * 0.60), int(n_bars * 0.88)]
        x_labels = []
        for ti in tick_indices:
            dt_ist = to_utc_timestamp(sub.index[ti]) + timedelta(hours=5, minutes=30)
            x_labels.append(dt_ist.strftime("%d %b %I:%M %p"))
        ax.set_xticks(tick_indices)
        ax.set_xticklabels(x_labels, fontsize=8.4, color="#131722")

        last_bar = sub.iloc[-1]
        prev_bar = sub.iloc[-2]
        o_val    = float(last_bar["Open"])
        h_val    = float(last_bar["High"])
        l_val    = float(last_bar["Low"])
        c_val    = float(last_bar["Close"])
        chg_val  = c_val - float(prev_bar["Close"])
        chg_pct  = (chg_val / max(float(prev_bar["Close"]), 1e-9)) * 100.0
        ohlc_col = "#089981" if chg_val >= 0 else "#f23645"

        top_ax = fig.add_axes([0.015, 0.935, 0.96, 0.055], facecolor="#ffffff")
        top_ax.axis("off")
        top_ax.text(
            0.0, 0.55,
            f"{hdr_nam} · 1H Zones on 5m · Spot",
            fontsize=9.5, fontweight="bold", color="#131722", va="center", ha="left"
        )
        top_ax.text(
            0.27, 0.55,
            f"O{format_tv_comma_price(o_val, dec)}  H{format_tv_comma_price(h_val, dec)}  L{format_tv_comma_price(l_val, dec)}  C{format_tv_comma_price(c_val, dec)}  {chg_val:+.{dec}f} ({chg_pct:+.2f}%)",
            fontsize=8.8, fontweight="bold", color=ohlc_col, va="center", ha="left"
        )
        top_ax.text(
            0.985, 0.55, f"1H: {h1_bias}",
            fontsize=8.2, fontweight="bold", color="#131722", va="center", ha="right",
            bbox=dict(boxstyle="round,pad=0.22", facecolor="#f8fafc", edgecolor="#e0e3eb", linewidth=0.7)
        )

        fig.text(0.015, 0.018, "TradingView", fontsize=9.5, fontweight="bold", color="#131722")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, facecolor="#ffffff")
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Chart Error: {e}")
        return None

def send_telegram_signal_with_chart(caption_html, chart_buf, target_chat_id=CHAT_ID):
    if chart_buf is not None and BOT_TOKEN and BOT_TOKEN != "YOUR_TELEGRAM_BOT_TOKEN":
        photo_bytes = chart_buf.getvalue()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        try:
            if len(caption_html) <= 980:
                files = {"photo": ("chart.png", photo_bytes, "image/png")}
                data  = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                res   = HTTP_SESSION.post(url, data=data, files=files, timeout=15)
                if res.status_code == 200 and res.json().get("ok"):
                    return
            files = {"photo": ("chart.png", photo_bytes, "image/png")}
            data  = {"chat_id": target_chat_id, "caption": "", "parse_mode": "HTML"}
            HTTP_SESSION.post(url, data=data, files=files, timeout=15)
            send_telegram_message(caption_html, target_chat_id)
            return
        except Exception:
            pass
    send_telegram_message(caption_html, target_chat_id)

def send_instant_preview_chart(pair_key=None):
    global chart_rotation_index
    pair_names_list = list(FOREX_PAIRS.keys())
    if pair_key is None:
        pair_key = pair_names_list[chart_rotation_index % len(pair_names_list)]
        chart_rotation_index += 1

    info = FOREX_PAIRS.get(pair_key, FOREX_PAIRS["XAU/USD"])
    df_1h, df_5m, live_spot = get_1h_and_5m_spot_data(pair_key, info, use_fast_cache=True)
    if df_1h is None or df_5m is None or live_spot is None:
        send_telegram_message("⚠️ Data loading, please try again in a few seconds.")
        return

    df_1h = enrich_price_action_metrics(df_1h)
    df_5m = enrich_price_action_metrics(df_5m)
    dec   = info["decimals"]

    zones, _, h1_bias, lower_runner, upper_runner = build_1h_institutional_analysis(df_1h, live_spot, dec)
    cand = evaluate_1h_to_5m_setup(pair_key, info, df_1h, df_5m, live_spot, ignore_locks=True)

    chart_buf = generate_1h_zones_on_5m_chart(df_1h, df_5m, info, live_spot, active_trade=cand)

    upper_z  = next((z for z in zones if z["bot"] > live_spot), zones[-1])
    lower_z  = next((z for z in reversed(zones) if z["top"] < live_spot), zones[0])
    inside_z = next((z for z in zones if z["bot"] <= live_spot <= z["top"]), None)

    if cand is not None:
        lot_size = calculate_exact_mt5_lot(pair_key, info, cand["sl_dist"], BASE_RISK_PER_TRADE_USD)
        caption = (
            f"📊 <b>{info['mt5_name']} • 1H ANALYSIS ➔ 5M ENTRY SETUP</b>\n\n"
            f"🧭 <b>1H Trend:</b> {h1_bias} | 📢 <b>Action:</b> <b>{cand['action']}</b>\n"
            f"🏷️ <b>Trigger:</b> {cand['short_tag']}\n"
            f"📍 <b>Spot Price:</b> <code>{format_price(live_spot, dec)}</code> | 💼 <b>Lot:</b> <code>{lot_size:.2f}</code>\n"
            f"🛑 <b>Stop-Loss:</b> <code>{format_price(cand['sl_price'], dec)}</code>\n"
            f"🎯 <b>Target 1:</b> <code>{format_price(cand['tp1_price'], dec)}</code> | 🎯 <b>Target 2:</b> <code>{format_price(cand['tp2_price'], dec)}</code>"
        )
    else:
        if inside_z is not None:
            state_txt = (
                f"🧭 <b>1H Trend:</b> <b>{h1_bias}</b>\n"
                f"📦 <b>1H Zone Active:</b> Price is inside <code>{format_price(inside_z['bot'], dec)} – {format_price(inside_z['top'], dec)}</code>.\n"
                f"⬆️ <b>5M BUY Trigger:</b> 5M close above <code>{format_price(inside_z['top'], dec)}</code>\n"
                f"⬇️ <b>5M SELL Trigger:</b> 5M close below <code>{format_price(inside_z['bot'], dec)}</code>"
            )
        else:
            state_txt = (
                f"🧭 <b>1H Trend:</b> <b>{h1_bias}</b>\n"
                f"⬆️ <b>Upper 1H Zone:</b> <code>{format_price(upper_z['bot'], dec)} – {format_price(upper_z['top'], dec)}</code> (5M Break above <code>{format_price(upper_z['top'], dec)}</code>)\n"
                f"⬇️ <b>Lower 1H Zone:</b> <code>{format_price(lower_z['bot'], dec)} – {format_price(lower_z['top'], dec)}</code> (5M Break below <code>{format_price(lower_z['bot'], dec)}</code>)"
            )

        caption = (
            f"📊 <b>{info['mt5_name']} • 1H ANALYSIS ➔ 5M CHART</b>\n"
            f"📍 <b>Live Price:</b> <code>{format_price(live_spot, dec)}</code>\n\n"
            f"{state_txt}"
        )

    send_telegram_signal_with_chart(caption, chart_buf)

def send_live_status_summary():
    bal, eq, day_pnl = get_live_account_metrics()
    report = (
        f"📊 <b>1H ANALYSIS ➔ 5M EXECUTION SCANNER</b>\n\n"
        f"💰 <b>Equity:</b> <b>${eq:.2f}</b> | 📈 <b>Today's PnL:</b> <b>${day_pnl:+.2f}</b>\n"
        f"🔢 <b>Trades Today:</b> {trades_today}/{MAX_DAILY_TRADES_CAP}\n\n"
    )
    for name, info in FOREX_PAIRS.items():
        try:
            df_1h, df_5m, live_spot = get_1h_and_5m_spot_data(name, info, use_fast_cache=True)
            if df_1h is not None and live_spot is not None:
                df_1h = enrich_price_action_metrics(df_1h)
                zones, _, h1_bias, _, _ = build_1h_institutional_analysis(df_1h, live_spot, info["decimals"])
                inside_z = next((z for z in zones if z["bot"] <= live_spot <= z["top"]), None)
                z_status = "At 1H Zone" if inside_z else "1H Corridor"
                report += f"🔹 <b>{info['mt5_name']}:</b> <code>{format_price(live_spot, info['decimals'])}</code> | 1H: {h1_bias} ({z_status})\n"
        except Exception:
            pass
    send_telegram_message(report)

def check_telegram_commands():
    global last_update_id
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 0}
    try:
        response = HTTP_SESSION.get(url, params=params, timeout=3).json()
        if response.get("ok"):
            for result in response.get("result", []):
                last_update_id = result["update_id"]
                message  = result.get("message", {})
                chat_id  = str(message.get("chat", {}).get("id", ""))
                msg_text = message.get("text", "").strip().lower()
                if chat_id != str(CHAT_ID):
                    continue
                if msg_text in ["/status", "status", "/start", "start"]:
                    send_live_status_summary()
                elif msg_text in ["/chart", "chart"]:
                    send_instant_preview_chart(None)
                else:
                    for p_name, p_info in FOREX_PAIRS.items():
                        if msg_text in p_info.get("cmd_keys", []) or msg_text == f"/chart {p_info['mt5_name'].lower()}":
                            send_instant_preview_chart(p_name)
                            break
    except Exception:
        pass

# =====================================================================
# 6. ACTIVE TRADE MANAGEMENT
# =====================================================================
def manage_open_trades(pair_name, candle_time, live_spot, df_5m, decimals):
    global daily_realized_pnl, total_account_pnl, pair_cooldown_until, sl_direction_lock
    global consecutive_losses, consecutive_wins, global_pause_until

    now_utc   = get_utc_now()
    latest_5m = df_5m.iloc[-1]
    closed_5m = df_5m.iloc[-2]
    prev_5m   = df_5m.iloc[-3]

    current_high = max(live_spot, float(latest_5m["High"]))
    current_low  = min(live_spot, float(latest_5m["Low"]))

    for trade in daily_trades_log:
        if trade["pair"] == pair_name and trade["is_open"]:
            if to_utc_timestamp(candle_time) <= trade["entry_candle_time"]:
                continue

            entry_str = format_price(trade["entry"], decimals)
            sl_str    = format_price(trade["sl"], decimals)
            price_str = format_price(live_spot, decimals)

            # STEP 1: BREAKEVEN LOCK
            if not trade["trailed_to_cost"]:
                half_reached = (trade["direction"] == "BUY" and current_high >= trade["half_target"]) or \
                               (trade["direction"] == "SELL" and current_low <= trade["half_target"])
                if half_reached:
                    trade["trailed_to_cost"] = True
                    trade["sl"] = trade["entry"]
                    send_telegram_message(
                        f"🛡️ <b>BREAKEVEN UPDATE — {trade['mt5_name']}</b>\n"
                        f"📍 <b>Live Price:</b> <code>{price_str}</code>\n"
                        f"👉 Move Stop-Loss to Entry (<code>{entry_str}</code>)."
                    )

            # STEP 2: TARGET 1 PARTIAL BOOK
            if not trade.get("near_tp_alerted", False):
                near_hit = (trade["direction"] == "BUY" and current_high >= trade["near_target"] and current_high < trade["target"]) or \
                           (trade["direction"] == "SELL" and current_low <= trade["near_target"] and current_low > trade["target"])
                if near_hit:
                    trade["near_tp_alerted"] = True
                    trade["trailed_to_cost"] = True
                    trade["sl"]              = trade["half_target"]
                    lock_s = format_price(trade["half_target"], decimals)
                    send_telegram_message(
                        f"💰 <b>TARGET 1 REACHED — {trade['mt5_name']}</b>\n"
                        f"📍 <b>Live Price:</b> <code>{price_str}</code>\n"
                        f"👉 Book 50% profit and trail SL to <code>{lock_s}</code>."
                    )

            # STEP 3: STRUCTURE INVALIDATION EXIT
            mins_open = (now_utc - trade["entry_candle_time"]).total_seconds() / 60.0
            c_open, c_close, c_disp = float(closed_5m["Open"]), float(closed_5m["Close"]), float(closed_5m["Disp_Power"])
            p_open, p_close = float(prev_5m["Open"]), float(prev_5m["Close"])

            opposite_danger = False
            if trade["direction"] == "BUY" and (c_close < c_open) and (c_disp >= 1.65) and (c_close < min(p_open, p_close)):
                opposite_danger = True
            elif trade["direction"] == "SELL" and (c_close > c_open) and (c_disp >= 1.65) and (c_close > max(p_open, p_close)):
                opposite_danger = True

            if opposite_danger and not trade["trailed_to_cost"]:
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=25)
                saved_loss = round(trade["max_risk"] * 0.45, 1)
                daily_realized_pnl -= saved_loss
                total_account_pnl  -= saved_loss
                send_telegram_message(
                    f"⚠️ <b>EARLY EXIT ALERT — {trade['mt5_name']}</b>\n"
                    f"📍 <b>Live Price:</b> <code>{price_str}</code>\n"
                    f"👉 Opposite momentum detected on 5M. Close trade now to protect capital."
                )
                continue

            if mins_open >= MAX_TRADE_HOLD_MINUTES and not trade["trailed_to_cost"]:
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=15)
                send_telegram_message(
                    f"⏳ <b>TIME-STOP EXIT — {trade['mt5_name']}</b>\n"
                    f"👉 No follow-through after 55 minutes. Close trade near cost."
                )
                continue

            # STEP 4: FINAL TARGET OR SL
            hit_type = None
            if trade["direction"] == "BUY":
                if current_high >= trade["target"]:
                    hit_type = "TARGET"
                elif current_low <= trade["sl"]:
                    hit_type = "TRAILING_SL" if trade["trailed_to_cost"] else "SL"
            else:
                if current_low <= trade["target"]:
                    hit_type = "TARGET"
                elif current_high >= trade["sl"]:
                    hit_type = "TRAILING_SL" if trade["trailed_to_cost"] else "SL"

            if hit_type == "TARGET":
                trade["is_open"] = False
                consecutive_losses = 0
                consecutive_wins  += 1
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=20)
                net_win = trade["expected_profit"]
                daily_realized_pnl += net_win
                total_account_pnl  += net_win
                send_telegram_message(f"🎯 <b>FULL TARGET 2 HIT (+${net_win:.0f}) — {trade['mt5_name']}</b>")
            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                consecutive_losses = 0
                consecutive_wins  += 1
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=15)
                locked_win = round(trade["expected_profit"] * 0.55, 1) if trade.get("near_tp_alerted") else 0.0
                daily_realized_pnl += locked_win
                total_account_pnl  += locked_win
                send_telegram_message(f"🛡️ <b>TRAILING SL HIT (+${locked_win:.0f}) — {trade['mt5_name']}</b>")
            elif hit_type == "SL":
                trade["is_open"] = False
                consecutive_wins    = 0
                consecutive_losses += 1
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=30)
                sl_direction_lock[pair_name][trade["direction"]] = now_utc + timedelta(minutes=45)
                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_account_pnl  -= loss_amt
                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES_HALT:
                    global_pause_until = now_utc + timedelta(minutes=90)
                send_telegram_message(f"🛑 <b>STOP-LOSS HIT (-${loss_amt:.0f}) — {trade['mt5_name']}</b> (<code>{sl_str}</code>)")

# =====================================================================
# 7. MASTER SCANNER LOOP
# =====================================================================
def scan_markets():
    global trades_today, current_day, last_candle_traded, last_setup_traded
    global daily_trades_log, daily_realized_pnl, last_global_trade_time, daily_halt_announced
    global consecutive_losses, consecutive_wins

    today = get_ist_now().date()
    if today != current_day:
        current_day = today
        trades_today = 0
        consecutive_losses = 0
        consecutive_wins   = 0
        daily_trades_log = []
        daily_realized_pnl = 0.0
        daily_halt_announced = False
        last_candle_traded = {name: None for name in FOREX_PAIRS}
        for k in live_bars_memory:
            live_bars_memory[k] = {}

    forex_open  = is_forex_market_open()
    usdjpy_spot = fetch_tradingview_median_spot(["FX:USDJPY", "OANDA:USDJPY"], "forex") or 152.0
    usdcad_spot = fetch_tradingview_median_spot(["FX:USDCAD", "OANDA:USDCAD"], "forex") or 1.39

    for name, info in FOREX_PAIRS.items():
        check_telegram_commands()

        if not forex_open and not info.get("is_crypto", False):
            continue
        try:
            df_1h, df_5m, live_spot = get_1h_and_5m_spot_data(name, info, use_fast_cache=False)
            if df_1h is None or df_5m is None or live_spot is None:
                continue

            if (get_utc_now() - to_utc_timestamp(df_5m.index[-1])).total_seconds() > 1500:
                continue

            df_1h = enrich_price_action_metrics(df_1h)
            df_5m = enrich_price_action_metrics(df_5m)

            closed_time = to_utc_timestamp(df_5m.index[-2])
            dec         = info["decimals"]

            manage_open_trades(name, closed_time, live_spot, df_5m, dec)

            can_trade, dynamic_risk_usd = pre_flight_risk_check()
            if not can_trade:
                continue

            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            open_trades_count = sum(1 for t in daily_trades_log if t["is_open"])
            if open_trades_count >= MAX_CONCURRENT_TRADES:
                continue

            if (get_utc_now() - last_global_trade_time).total_seconds() < (MIN_GAP_BETWEEN_TRADES_MIN * 60):
                continue

            if last_candle_traded[name] == closed_time:
                continue

            cand = evaluate_1h_to_5m_setup(name, info, df_1h, df_5m, live_spot, ignore_locks=False)

            if cand is not None:
                direction         = cand["direction"]
                signal            = cand["action"]
                short_tag         = cand["short_tag"]
                why_text          = cand["why_text"]
                sl_level          = cand["sl_price"]
                sl_dist           = cand["sl_dist"]
                half_target_level = cand["lock_level"]
                near_tp_level     = cand["tp1_price"]
                target_level      = cand["tp2_price"]
                target_dist       = cand["tp2_dist"]
                actual_rr         = cand["rr"]

                exact_lot = calculate_exact_mt5_lot(name, info, sl_dist, dynamic_risk_usd, usdjpy_spot, usdcad_spot)

                max_chase_buf = round(sl_dist * 0.22, dec)
                if direction == "BUY":
                    max_safe_entry = round(live_spot + max_chase_buf, dec)
                    entry_zone_str = f"<code>{format_price(live_spot, dec)} – {format_price(max_safe_entry, dec)}</code>"
                else:
                    max_safe_entry = round(live_spot - max_chase_buf, dec)
                    entry_zone_str = f"<code>{format_price(max_safe_entry, dec)} – {format_price(live_spot, dec)}</code>"

                last_candle_traded[name] = closed_time
                last_setup_traded[name]  = cand["setup_id"]
                last_global_trade_time   = get_utc_now()
                trades_today += 1

                expected_prof = round(dynamic_risk_usd * actual_rr, 1)
                tp1_dist      = abs(near_tp_level - live_spot)

                daily_trades_log.append({
                    "id": trades_today, "pair": name, "mt5_name": info["mt5_name"],
                    "action": signal, "direction": direction, "strategy": short_tag, "lot_size": exact_lot,
                    "entry": live_spot, "sl": sl_level, "sl_dist": sl_dist, "half_target": half_target_level,
                    "near_target": near_tp_level, "target": target_level, "max_risk": dynamic_risk_usd,
                    "expected_profit": expected_prof, "entry_candle_time": closed_time,
                    "trailed_to_cost": False, "near_tp_alerted": False, "is_open": True, "status": "RUNNING"
                })

                msg = (
                    f"📊 <b>{info['mt5_name']} • 1H ANALYSIS ➔ 5M SIGNAL</b>\n\n"
                    f"📢 <b>Action:</b> <b>{signal}</b>\n"
                    f"🏷️ <b>Setup:</b> {short_tag}\n"
                    f"💼 <b>Lot Size:</b> <b><code>{exact_lot:.2f} Lot</code></b>\n\n"
                    f"📍 <b>Entry Zone:</b> {entry_zone_str}\n"
                    f"🛑 <b>Stop-Loss:</b> <code>{format_price(sl_level, dec)}</code>  <i>({calculate_pips_or_points(name, sl_dist, dec)})</i>\n"
                    f"🛡️ <b>Breakeven Trigger:</b> <code>{format_price(half_target_level, dec)}</code>\n"
                    f"🎯 <b>Target 1 (50%):</b> <code>{format_price(near_tp_level, dec)}</code>  <i>({calculate_pips_or_points(name, tp1_dist, dec)})</i>\n"
                    f"🎯 <b>Target 2 (Full):</b> <code>{format_price(target_level, dec)}</code>  <i>({calculate_pips_or_points(name, target_dist, dec)})</i>\n\n"
                    f"🔍 <b>Technical Basis:</b>\n{why_text}\n"
                    f"⚖️ <b>Risk:</b> ${dynamic_risk_usd:.0f}  |  <b>Reward:</b> +${expected_prof:.0f} (1:{actual_rr:.2f})"
                )

                chart_buf = generate_1h_zones_on_5m_chart(df_1h, df_5m, info, live_spot, active_trade=cand)
                send_telegram_signal_with_chart(msg, chart_buf)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    fetch_live_economic_calendar()
    send_telegram_message(
        "📊 <b>1H Analysis ➔ 5M Execution Scanner Active</b>\n"
        "• 1H Institutional Zones & Structure Analysis\n"
        "• 5M Sniper Entry Triggers (Up to 14 Setups/Day)\n"
        "• Strict USD Conflict Protection Enabled"
    )

    start_run      = time.time()
    last_scan_time = 0
    is_github_ci   = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    max_run_secs   = 21000 if is_github_ci else 999999999

    while time.time() - start_run < max_run_secs:
        try:
            check_telegram_commands()
            if time.time() - last_scan_time >= 15:
                scan_markets()
                last_scan_time = time.time()
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("Stopped manually.")
            break
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(3)
