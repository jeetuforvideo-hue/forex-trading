import os
import io
import sys
import time
import subprocess
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

try:
    import matplotlib
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "matplotlib"])
    import matplotlib

matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
CHART_AVAILABLE = True

# =====================================================================
# 1. CORE TRADING ENGINE SETTINGS
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "5610520408"

TOTAL_CAPITAL_USD          = 5000.0
RISK_PER_TRADE_USD         = 25.0      # $5,000 अकाउंट पर $25 रिस्क
DAILY_PROTECTION_CAP       = 125.0

BASE_EASY_RR_RATIO         = 1.45      # Easy-Reach Target
LOCK_PROFIT_R              = 0.65      # 0.65R पर Breakeven Lock
NEAR_TARGET_ALERT_RATIO    = 0.82      # 82% Target पर Instant Book Alert
SL_STOP_HUNT_BUFFER_ATR    = 0.55      # Stop-Hunt से बचने के लिए एक्स्ट्रा बफर

MAX_CONCURRENT_TRADES      = 2
MIN_GAP_BETWEEN_TRADES_MIN = 12

FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "mt5_name": "XAUUSD",
        "sub_title": "Gold vs US Dollar",
        "cmd_keys": ["/xauusd", "xauusd", "/gold", "gold"],
        "tv_ticker": "OANDA:XAUUSD",
        "tv_screener": "cfd",
        "binance_symbol": None,
        "yf_symbol": "GC=F",
        "currencies": ["USD"],
        "decimals": 2,
        "unit_name": "Points",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 6.20,
        "max_sl_dist": 11.50,
        "max_lot_cap": 0.15,
        "is_crypto": False
    },
    "EUR/USD": {
        "mt5_name": "EURUSD",
        "sub_title": "Euro vs US Dollar",
        "cmd_keys": ["/eurusd", "eurusd", "/eur", "eur"],
        "tv_ticker": "OANDA:EURUSD",
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbol": "EURUSD=X",
        "currencies": ["EUR", "USD"],
        "decimals": 5,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0016,
        "max_sl_dist": 0.0028,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "GBP/USD": {
        "mt5_name": "GBPUSD",
        "sub_title": "Great Britain Pound vs US Dollar",
        "cmd_keys": ["/gbpusd", "gbpusd", "/gbp", "gbp"],
        "tv_ticker": "OANDA:GBPUSD",
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbol": "GBPUSD=X",
        "currencies": ["GBP", "USD"],
        "decimals": 5,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0018,
        "max_sl_dist": 0.0032,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "USD/JPY": {
        "mt5_name": "USDJPY",
        "sub_title": "US Dollar vs Japanese Yen",
        "cmd_keys": ["/usdjpy", "usdjpy", "/jpy", "jpy"],
        "tv_ticker": "OANDA:USDJPY",
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbol": "JPY=X",
        "currencies": ["USD", "JPY"],
        "decimals": 3,
        "unit_name": "Pips",
        "usd_exposure": "LONG_USD_ON_BUY",
        "min_sl_dist": 0.180,
        "max_sl_dist": 0.320,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "GBP/JPY": {
        "mt5_name": "GBPJPY",
        "sub_title": "Great Britain Pound vs Japanese Yen",
        "cmd_keys": ["/gbpjpy", "gbpjpy", "/gj", "gj"],
        "tv_ticker": "OANDA:GBPJPY",
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbol": "GBPJPY=X",
        "currencies": ["GBP", "JPY"],
        "decimals": 3,
        "unit_name": "Pips",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 0.240,
        "max_sl_dist": 0.400,
        "max_lot_cap": 0.25,
        "is_crypto": False
    },
    "AUD/USD": {
        "mt5_name": "AUDUSD",
        "sub_title": "Australian Dollar vs US Dollar",
        "cmd_keys": ["/audusd", "audusd", "/aud", "aud"],
        "tv_ticker": "OANDA:AUDUSD",
        "tv_screener": "forex",
        "binance_symbol": None,
        "yf_symbol": "AUDUSD=X",
        "currencies": ["AUD", "USD"],
        "decimals": 5,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0014,
        "max_sl_dist": 0.0025,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "BTC/USD (BITCOIN)": {
        "mt5_name": "BTCUSD",
        "sub_title": "Bitcoin vs US Dollar",
        "cmd_keys": ["/btcusd", "btcusd", "/btc", "btc"],
        "tv_ticker": "BITSTAMP:BTCUSD",
        "tv_screener": "crypto",
        "binance_symbol": "BTCUSDT",
        "yf_symbol": "BTC-USD",
        "currencies": ["USD"],
        "decimals": 2,
        "unit_name": "Points",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 320.0,
        "max_sl_dist": 600.0,
        "max_lot_cap": 0.15,
        "is_crypto": True
    }
}

last_candle_traded     = {name: None for name in FOREX_PAIRS}
last_structure_traded  = {name: None for name in FOREX_PAIRS}
pair_cooldown_until    = {name: datetime.min.replace(tzinfo=timezone.utc) for name in FOREX_PAIRS}
sl_direction_lock      = {name: {"BUY": datetime.min.replace(tzinfo=timezone.utc), "SELL": datetime.min.replace(tzinfo=timezone.utc)} for name in FOREX_PAIRS}
last_global_trade_time = datetime.min.replace(tzinfo=timezone.utc)

cached_news_events     = []
last_news_fetch_time   = 0
announced_news_ids     = set()
chart_rotation_index   = 0

trades_today       = 0
last_update_id     = 0
daily_trades_log   = []
daily_realized_pnl = 0.0
total_account_pnl  = 0.0

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
    if wd == 6:
        return False
    if wd == 5 and now.hour >= 4:
        return False
    return True

def get_trading_session(is_crypto=False):
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)
    if is_crypto:
        return True, "Crypto 24/7 Session"
    if 3.25 <= hr <= 5.0:
        return False, "Rollover Pause"
    elif 5.0 < hr < 12.0:
        return True, "Asian Session"
    elif 12.0 <= hr < 18.0:
        return True, "London Session"
    elif 18.0 <= hr < 22.5:
        return True, "London + NY Overlap"
    else:
        return True, "New York Session"

def send_telegram_message(message, target_chat_id=CHAT_ID):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def format_price(price, decimals):
    return f"{price:.{decimals}f}"

def calculate_pips_or_points(pair_name, price_diff, decimals):
    if "GOLD" in pair_name or "BTC" in pair_name:
        return f"${price_diff:.2f} ({price_diff * 10:.0f} Points)"
    elif decimals == 3:
        return f"{price_diff * 100:.1f} Pips"
    else:
        return f"{price_diff * 10000:.1f} Pips"

def calculate_exact_mt5_lot(pair_name, pair_info, sl_dist, risk_usd, live_usdjpy_rate=152.0):
    if sl_dist <= 0:
        return 0.02

    if "GOLD" in pair_name:
        loss_per_1_lot = sl_dist * 100.0
    elif "BTC" in pair_name:
        loss_per_1_lot = sl_dist * 1.0
    elif pair_name.endswith("JPY"):
        usdjpy = live_usdjpy_rate if live_usdjpy_rate > 80.0 else 152.0
        loss_per_1_lot = (sl_dist * 100000.0) / usdjpy
    else:
        pips = sl_dist * 10000.0
        loss_per_1_lot = pips * 10.0

    if loss_per_1_lot <= 0:
        return 0.02

    raw_lot = risk_usd / loss_per_1_lot
    max_cap = pair_info.get("max_lot_cap", 0.30)
    return max(0.01, min(round(raw_lot, 2), max_cap))

# =====================================================================
# 2. LIVE HIGH-IMPACT ECONOMIC NEWS & CRASH DETECTOR
# =====================================================================
def fetch_live_economic_calendar():
    global cached_news_events, last_news_fetch_time
    now_ts = time.time()
    if (now_ts - last_news_fetch_time) < 1800 and cached_news_events:
        return cached_news_events

    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=8)
        if res.status_code == 200:
            data = res.json()
            high_events = []
            for item in data:
                impact = str(item.get("impact", "")).strip()
                if impact in ["High", "Holiday"]:
                    title = str(item.get("title", "Macro Event"))
                    country = str(item.get("country", "USD")).upper()
                    date_str = str(item.get("date", ""))
                    try:
                        evt_dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                        high_events.append({
                            "id": f"{country}_{title}_{evt_dt.strftime('%Y%m%d%H%M')}",
                            "title": title,
                            "country": country,
                            "time_utc": evt_dt,
                            "impact": impact
                        })
                    except Exception:
                        pass
            cached_news_events = high_events
            last_news_fetch_time = now_ts
    except Exception:
        pass
    return cached_news_events

def check_news_window_for_pair(pair_info):
    global announced_news_ids
    events = fetch_live_economic_calendar()
    now_utc = get_utc_now()
    pair_ccy = pair_info.get("currencies", ["USD"])

    for ev in events:
        if ev["impact"] != "High":
            continue
        if ev["country"] not in pair_ccy and "USD" not in ev["country"]:
            continue

        diff_mins = (ev["time_utc"] - now_utc).total_seconds() / 60.0
        if -6.0 <= diff_mins <= 15.0:
            if ev["id"] not in announced_news_ids:
                announced_news_ids.add(ev["id"])
                ist_time = (ev["time_utc"] + timedelta(hours=5, minutes=30)).strftime("%I:%M %p IST")
                send_telegram_message(
                    f"🚨 <b>HIGH-IMPACT NEWS ALERT ({ev['country']})</b> 🚨\n\n"
                    f"📰 <b>Event:</b> <b>{ev['title']}</b>\n"
                    f"⏰ <b>Time:</b> {ist_time}\n"
                    f"🛡️ <b>Action:</b> न्यूज़ स्पाइक से बचाव के लिए 15 मिनट का News Shield एक्टिव है।"
                )
            return False, f"News Pause: {ev['title']}"

    return True, "Clear"

def detect_live_market_crash_or_shock(df_5m):
    if df_5m is None or len(df_5m) < 20:
        return "NORMAL", None

    recent_4 = df_5m.iloc[-5:-1]
    atr_5m   = float(df_5m.iloc[-2]["ATR"])
    if np.isnan(atr_5m) or atr_5m <= 0:
        return "NORMAL", None

    max_bar_rng = float(recent_4["Range"].max())
    net_move    = float(recent_4.iloc[-1]["Close"]) - float(recent_4.iloc[0]["Open"])

    if max_bar_rng >= (atr_5m * 2.1) or abs(net_move) >= (atr_5m * 2.8):
        if net_move < 0:
            return "BEARISH_CRASH", "Heavy Sell-Off Active"
        elif net_move > 0:
            return "BULLISH_SURGE", "Heavy Bullish Surge Active"

    return "NORMAL", None

# =====================================================================
# 3. CONTINUOUS DENSE CANDLE ENGINE + LIVE OANDA SPOT CALIBRATION
# =====================================================================
def fetch_tradingview_live_spot(tv_ticker, screener="forex"):
    url = f"https://scanner.tradingview.com/{screener}/scan"
    payload = {
        "symbols": {"tickers": [tv_ticker], "query": {"types": []}},
        "columns": ["close", "high", "low", "Recommend.Other", "RSI"]
    }
    headers = {"User-Agent": "Mozilla/5.0", "Cache-Control": "no-cache"}
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=6)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data and data[0].get("d"):
                spot_close = float(data[0]["d"][0])
                if spot_close > 0:
                    return spot_close
    except Exception:
        pass
    return None

def fetch_binance_vision_ohlc(binance_symbol, interval_str="5m", limit=120):
    if not binance_symbol:
        return None
    url = "https://data-api.binance.vision/api/v3/klines"
    params = {"symbol": binance_symbol, "interval": interval_str, "limit": limit}
    try:
        r = requests.get(url, params=params, timeout=7).json()
        if isinstance(r, list) and len(r) >= 50:
            df = pd.DataFrame(r, columns=[
                "open_time", "Open", "High", "Low", "Close", "Volume",
                "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"
            ])
            df["Date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df.set_index("Date", inplace=True)
            for col in ["Open", "High", "Low", "Close", "Volume"]:
                df[col] = df[col].astype(float)
            return df[["Open", "High", "Low", "Close", "Volume"]]
    except Exception:
        pass
    return None

def clean_and_densify_candles(df):
    """
    खाली/फ्लैट या टूटी हुई कैंडल्स को हटाता है ताकि चार्ट पर एक भी कैंडल Skip या दूर-दूर न दिखे
    और हर कैंडल के बीच कंटिन्यूटी (Continuity) बनी रहे।
    """
    if df is None or df.empty:
        return df
    df = df.copy()
    df = df[~df.index.duplicated(keep="last")]
    df.dropna(subset=["Open", "High", "Low", "Close"], inplace=True)

    # ऐसी खाली कैंडल्स हटाएं जहाँ Open == High == Low == Close हो और कोई मूवमेंट न हुआ हो
    active_mask = (df["High"] > df["Low"]) | (df["Open"] != df["Close"])
    if active_mask.sum() >= 45:
        df = df[active_mask]

    return df

def get_clean_spot_data(pair_name, info):
    """
    हाई-वॉल्यूम 5M और 15M कैंडल्स लाता है और उन्हें सीधे OANDA Live Spot Price
    पर सिंक करता है, साथ ही आखिरी चलती कैंडल (Live Running Candle) को भी अपडेट करता है।
    """
    df_5m  = None
    df_15m = None
    source_used = "OANDA Live Spot Sync"

    # 1. Crypto के लिए Binance Spot Continuous Feed
    if info.get("binance_symbol"):
        df_5m  = fetch_binance_vision_ohlc(info["binance_symbol"], "5m", 120)
        df_15m = fetch_binance_vision_ohlc(info["binance_symbol"], "15m", 100)
        if df_5m is not None and df_15m is not None:
            source_used = "Binance + Bitstamp Live Sync"

    # 2. Gold & Forex के लिए Continuous Institutional Candle Structure
    if df_5m is None or df_15m is None:
        try:
            tkr = yf.Ticker(info["yf_symbol"])
            df_5m  = tkr.history(period="5d", interval="5m", auto_adjust=False)
            df_15m = tkr.history(period="5d", interval="15m", auto_adjust=False)
            source_used = "OANDA Live Spot Sync"
        except Exception:
            return None, None, None, ""

    df_5m  = clean_and_densify_candles(df_5m)
    df_15m = clean_and_densify_candles(df_15m)

    if df_5m is None or df_5m.empty or len(df_5m) < 45 or df_15m is None or df_15m.empty or len(df_15m) < 35:
        return None, None, None, ""

    # 3. Real-Time OANDA Spot Calibration + Live Forming Candle Update
    tv_spot = fetch_tradingview_live_spot(info["tv_ticker"], info["tv_screener"])
    raw_last_close = float(df_5m.iloc[-1]["Close"])

    if tv_spot is not None and tv_spot > 0:
        basis_diff = tv_spot - raw_last_close
        if abs(basis_diff) < (tv_spot * 0.04):
            for col in ["Open", "High", "Low", "Close"]:
                df_5m[col]  = df_5m[col] + basis_diff
                df_15m[col] = df_15m[col] + basis_diff

        # आखिरी चलती कैंडल को वर्तमान लाइव टिक से अपडेट करें ताकि हर बार नया मूव दिखे
        last_idx = df_5m.index[-1]
        df_5m.loc[last_idx, "Close"] = tv_spot
        if tv_spot > float(df_5m.loc[last_idx, "High"]):
            df_5m.loc[last_idx, "High"] = tv_spot
        if tv_spot < float(df_5m.loc[last_idx, "Low"]):
            df_5m.loc[last_idx, "Low"] = tv_spot

        live_spot_price = tv_spot
    else:
        live_spot_price = raw_last_close

    return df_15m, df_5m, live_spot_price, source_used

# =====================================================================
# 4. SMART TARGET (FRONT-RUN TP) & ANTI-STOP-HUNT BUFFERED SL ENGINE
# =====================================================================
def calculate_smart_sl_and_easy_tp(df_5m, df_15m, info, direction, live_spot, raw_structure_sl, atr_5m):
    dec = info["decimals"]

    extra_wick_cushion = atr_5m * 0.30
    if direction == "BUY":
        buffered_sl = raw_structure_sl - extra_wick_cushion
    else:
        buffered_sl = raw_structure_sl + extra_wick_cushion

    raw_sl_dist = abs(live_spot - buffered_sl)
    min_sl      = max(atr_5m * 1.25, info.get("min_sl_dist", atr_5m * 1.25))
    max_sl      = max(atr_5m * 2.35, info.get("max_sl_dist", atr_5m * 2.35))
    sl_dist     = round(max(min_sl, min(raw_sl_dist, max_sl)), dec)

    recent_m5       = df_5m.iloc[-40:-2]
    major_high_wall = float(recent_m5["High"].max())
    major_low_wall  = float(recent_m5["Low"].min())

    default_tp_dist = sl_dist * BASE_EASY_RR_RATIO

    if direction == "BUY":
        room_to_wall = major_high_wall - live_spot
        if (sl_dist * 1.20) <= room_to_wall <= default_tp_dist:
            tp_dist = round(room_to_wall * 0.88, dec)
        else:
            tp_dist = round(default_tp_dist, dec)

        actual_rr    = round(tp_dist / max(sl_dist, 1e-9), 2)
        sl_level     = round(live_spot - sl_dist, dec)
        lock_level   = round(live_spot + (sl_dist * LOCK_PROFIT_R), dec)
        near_tp_lvl  = round(live_spot + (tp_dist * NEAR_TARGET_ALERT_RATIO), dec)
        target_level = round(live_spot + tp_dist, dec)
    else:
        room_to_wall = live_spot - major_low_wall
        if (sl_dist * 1.20) <= room_to_wall <= default_tp_dist:
            tp_dist = round(room_to_wall * 0.88, dec)
        else:
            tp_dist = round(default_tp_dist, dec)

        actual_rr    = round(tp_dist / max(sl_dist, 1e-9), 2)
        sl_level     = round(live_spot + sl_dist, dec)
        lock_level   = round(live_spot - (sl_dist * LOCK_PROFIT_R), dec)
        near_tp_lvl  = round(live_spot - (tp_dist * NEAR_TARGET_ALERT_RATIO), dec)
        target_level = round(live_spot - tp_dist, dec)

    return sl_level, sl_dist, lock_level, near_tp_lvl, target_level, tp_dist, actual_rr

# =====================================================================
# 5. TECHNICAL INDICATORS & 4 CORE PRICE-ACTION DETECTORS
# =====================================================================
def calculate_chart_indicators(df):
    df = df.copy()
    df["EMA9"]  = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA20"] = df["Close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["Close"].ewm(span=50, adjust=False).mean()

    hl = df["High"] - df["Low"]
    hc = np.abs(df["High"] - df["Close"].shift())
    lc = np.abs(df["Low"] - df["Close"].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    df["Body"]       = np.abs(df["Close"] - df["Open"])
    df["Range"]      = (df["High"] - df["Low"]).replace(0, 1e-9)
    df["Body_Ratio"] = df["Body"] / df["Range"]
    df["Upper_Wick"] = df["High"] - df[["Open", "Close"]].max(axis=1)
    df["Lower_Wick"] = df[["Open", "Close"]].min(axis=1) - df["Low"]
    df["Close_Pos"]  = (df["Close"] - df["Low"]) / df["Range"]

    avg_body = df["Body"].rolling(16).mean().replace(0, 1e-9)
    df["Disp_Power"] = df["Body"] / avg_body

    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - (100 / (1 + rs))).fillna(50.0)

    return df

def detect_fibonacci_swing_structure(df_5m, lookback=36):
    window = df_5m.iloc[-(lookback + 2):-2]
    if len(window) < 20:
        return None

    highs = window["High"].values
    lows  = window["Low"].values
    times = window.index

    max_idx = int(np.argmax(highs))
    min_idx = int(np.argmin(lows))

    swing_high = float(highs[max_idx])
    swing_low  = float(lows[min_idx])
    swing_high_time = times[max_idx]
    swing_low_time  = times[min_idx]

    swing_range = swing_high - swing_low
    atr_5m = float(df_5m.iloc[-2]["ATR"])
    if np.isnan(atr_5m) or atr_5m <= 0:
        return None

    if swing_range < (atr_5m * 2.1):
        return None

    if min_idx < max_idx and (len(window) - max_idx) >= 2:
        return {
            "type": "BULLISH_SWING",
            "swing_id": f"BULL_FIB_{swing_low_time}_{swing_high_time}",
            "swing_high": swing_high,
            "swing_low": swing_low,
            "fib_0": swing_high,
            "fib_382": swing_high - (swing_range * 0.382),
            "fib_500": swing_high - (swing_range * 0.500),
            "fib_618": swing_high - (swing_range * 0.618),
            "fib_786": swing_high - (swing_range * 0.786),
            "fib_100": swing_low
        }

    if max_idx < min_idx and (len(window) - min_idx) >= 2:
        return {
            "type": "BEARISH_SWING",
            "swing_id": f"BEAR_FIB_{swing_high_time}_{swing_low_time}",
            "swing_high": swing_high,
            "swing_low": swing_low,
            "fib_0": swing_low,
            "fib_382": swing_low + (swing_range * 0.382),
            "fib_500": swing_low + (swing_range * 0.500),
            "fib_618": swing_low + (swing_range * 0.618),
            "fib_786": swing_low + (swing_range * 0.786),
            "fib_100": swing_high
        }

    return None

def detect_reversal_neckline_breakout(df_5m, shock_state):
    if len(df_5m) < 40:
        return None

    c0 = df_5m.iloc[-2]
    price      = float(c0["Close"])
    open_0     = float(c0["Open"])
    high_0     = float(c0["High"])
    low_0      = float(c0["Low"])
    atr_5m     = float(c0["ATR"])
    ema9       = float(c0["EMA9"])
    ema20      = float(c0["EMA20"])
    rsi        = float(c0["RSI"])
    body_ratio = float(c0["Body_Ratio"])
    close_pos  = float(c0["Close_Pos"])
    disp_power = float(c0["Disp_Power"])

    if np.isnan(atr_5m) or atr_5m <= 0:
        return None

    win = df_5m.iloc[-30:-2]
    highs = win["High"].values
    lows  = win["Low"].values
    times = win.index
    n_len = len(win)

    min_idx = int(np.argmin(lows))
    max_idx = int(np.argmax(highs))
    head_low  = float(lows[min_idx])
    head_high = float(highs[max_idx])
    total_rng = head_high - head_low

    if total_rng < (atr_5m * 2.4):
        return None

    if shock_state != "BEARISH_CRASH" and 4 <= (n_len - min_idx) <= 23:
        post_head_highs = highs[min_idx + 1:]
        post_head_lows  = lows[min_idx + 2:] if (min_idx + 2) < n_len else lows[min_idx + 1:]

        if len(post_head_highs) >= 3 and len(post_head_lows) >= 2:
            neckline_res       = float(np.max(post_head_highs[:-1])) if len(post_head_highs) > 1 else float(np.max(post_head_highs))
            right_shoulder_low = float(np.min(post_head_lows))

            valid_structure = (right_shoulder_low > head_low) and ((neckline_res - head_low) >= atr_5m * 1.1) and ((neckline_res - head_low) <= atr_5m * 3.8)
            broke_neckline  = (price > neckline_res) and (float(df_5m.iloc[-3]["Close"]) <= neckline_res * 1.0003)
            strong_ignition = (price > open_0) and (price > ema9) and (price > ema20) and (body_ratio >= 0.48) and (close_pos >= 0.65) and (disp_power >= 0.95)

            if valid_structure and broke_neckline and strong_ignition and (42.0 <= rsi <= 72.0):
                sl_level = min(right_shoulder_low, low_0) - (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
                return {
                    "direction": "BUY",
                    "action": "BUY (LONG) 🟢",
                    "struct_id": f"IHNS_BUY_{times[min_idx]}",
                    "short_tag": "Inverse H&S Breakout",
                    "setup_text": f"Inverse H&S / Bottom Reversal Neckline Breakout ({neckline_res:.2f}) | RSI: {rsi:.0f}",
                    "sl_raw": sl_level
                }

    if shock_state != "BULLISH_SURGE" and 4 <= (n_len - max_idx) <= 23:
        post_head_lows  = lows[max_idx + 1:]
        post_head_highs = highs[max_idx + 2:] if (max_idx + 2) < n_len else highs[max_idx + 1:]

        if len(post_head_lows) >= 3 and len(post_head_highs) >= 2:
            neckline_sup        = float(np.min(post_head_lows[:-1])) if len(post_head_lows) > 1 else float(np.min(post_head_lows))
            right_shoulder_high = float(np.max(post_head_highs))

            valid_structure = (right_shoulder_high < head_high) and ((head_high - neckline_sup) >= atr_5m * 1.1) and ((head_high - neckline_sup) <= atr_5m * 3.8)
            broke_neckline  = (price < neckline_sup) and (float(df_5m.iloc[-3]["Close"]) >= neckline_sup * 0.9997)
            strong_collapse = (price < open_0) and (price < ema9) and (price < ema20) and (body_ratio >= 0.48) and (close_pos <= 0.35) and (disp_power >= 0.95)

            if valid_structure and broke_neckline and strong_collapse and (28.0 <= rsi <= 58.0):
                sl_level = max(right_shoulder_high, high_0) + (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
                return {
                    "direction": "SELL",
                    "action": "SELL (SHORT) 🔴",
                    "struct_id": f"HNS_SELL_{times[max_idx]}",
                    "short_tag": "Double Top Breakdown",
                    "setup_text": f"H&S / Top Reversal Neckline Breakdown ({neckline_sup:.2f}) | RSI: {rsi:.0f}",
                    "sl_raw": sl_level
                }

    return None

def check_usd_exposure_filter(pair_info):
    exp_type = pair_info.get("usd_exposure", "NEUTRAL")
    if exp_type != "NEUTRAL":
        for t in daily_trades_log:
            if t["is_open"]:
                t_info = FOREX_PAIRS.get(t["pair"], {})
                if t_info.get("usd_exposure", "NEUTRAL") != "NEUTRAL":
                    return False
    return True

# =====================================================================
# 6. MASTER DECISION ENGINE (NEWS FILTER + ALL 4 SETUPS)
# =====================================================================
def evaluate_all_market_setups(pair_name, pair_info, df_15m, df_5m):
    now_utc = get_utc_now()
    if now_utc < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
        return None, None, None, None, None, None

    session_ok, _ = get_trading_session(pair_info.get("is_crypto", False))
    if not session_ok:
        return None, None, None, None, None, None

    news_ok, _ = check_news_window_for_pair(pair_info)
    if not news_ok:
        return None, None, None, None, None, None

    if not check_usd_exposure_filter(pair_info):
        return None, None, None, None, None, None

    shock_state, _ = detect_live_market_crash_or_shock(df_5m)

    c0 = df_5m.iloc[-2]
    c1 = df_5m.iloc[-3]
    c2 = df_5m.iloc[-4]

    price      = float(c0["Close"])
    open_0     = float(c0["Open"])
    high_0     = float(c0["High"])
    low_0      = float(c0["Low"])
    atr_5m     = float(c0["ATR"])
    ema9       = float(c0["EMA9"])
    ema20      = float(c0["EMA20"])
    ema50      = float(c0["EMA50"])
    rsi        = float(c0["RSI"])
    close_pos  = float(c0["Close_Pos"])
    body_ratio = float(c0["Body_Ratio"])
    disp_power = float(c0["Disp_Power"])
    l_wick     = float(c0["Lower_Wick"])
    u_wick     = float(c0["Upper_Wick"])

    if np.isnan(atr_5m) or atr_5m <= 0:
        return None, None, None, None, None, None

    fib = detect_fibonacci_swing_structure(df_5m, lookback=36)

    # SETUP A: REVERSAL NECKLINE BREAKOUT
    rev = detect_reversal_neckline_breakout(df_5m, shock_state)
    if rev is not None:
        r_dir = rev["direction"]
        if now_utc >= sl_direction_lock[pair_name][r_dir] and last_structure_traded.get(pair_name) != rev["struct_id"]:
            if fib is None:
                fib = {"swing_id": rev["struct_id"], "fib_500": price, "fib_618": price, "fib_786": rev["sl_raw"]}
            else:
                fib["swing_id"] = rev["struct_id"]
            return rev["action"], r_dir, rev["setup_text"], rev["short_tag"], rev["sl_raw"], fib

    m15_c     = df_15m.iloc[-2]
    m15_close = float(m15_c["Close"])
    m15_ema20 = float(m15_c["EMA20"])
    m15_ema50 = float(m15_c["EMA50"])

    htf_bullish = ((m15_close >= m15_ema20) or (ema9 > ema20 > ema50)) and (shock_state != "BEARISH_CRASH")
    htf_bearish = ((m15_close <= m15_ema20) or (ema9 < ema20 < ema50)) and (shock_state != "BULLISH_SURGE")

    # SETUP B: FIBONACCI (0.382-0.618) + EMA 9/20 TREND PULLBACK
    if fib is not None and last_structure_traded.get(pair_name) != fib["swing_id"]:
        ema_separation = abs(ema9 - ema20)
        if ema_separation >= (atr_5m * 0.10):
            if htf_bullish and fib["type"] == "BULLISH_SWING" and now_utc >= sl_direction_lock[pair_name]["BUY"]:
                recent_3_low  = min(low_0, float(c1["Low"]), float(c2["Low"]))
                in_fib_zone   = (recent_3_low <= fib["fib_382"]) and (recent_3_low >= fib["fib_786"])
                ema_support   = (price > ema9) and (ema9 >= ema20 * 0.9995)
                green_trigger = (price > open_0) and (close_pos >= 0.58) and (body_ratio >= 0.42) and (price > float(c1["High"]) or price > float(c1["Close"]))
                if in_fib_zone and ema_support and green_trigger and (38.0 <= rsi <= 66.0):
                    sl_raw = min(recent_3_low, fib["fib_786"]) - (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
                    setup_text = f"Fibonacci Golden Zone (0.50-0.618) + EMA 9/20 Bounce | RSI: {rsi:.0f}"
                    return "BUY (LONG) 🟢", "BUY", setup_text, "Fib 0.618 + EMA Bounce", sl_raw, fib

            if htf_bearish and fib["type"] == "BEARISH_SWING" and now_utc >= sl_direction_lock[pair_name]["SELL"]:
                recent_3_high = max(high_0, float(c1["High"]), float(c2["High"]))
                in_fib_zone   = (recent_3_high >= fib["fib_382"]) and (recent_3_high <= fib["fib_786"])
                ema_resist    = (price < ema9) and (ema9 <= ema20 * 1.0005)
                red_trigger   = (price < open_0) and (close_pos <= 0.42) and (body_ratio >= 0.42) and (price < float(c1["Low"]) or price < float(c1["Close"]))
                if in_fib_zone and ema_resist and red_trigger and (34.0 <= rsi <= 62.0):
                    sl_raw = max(recent_3_high, fib["fib_786"]) + (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
                    setup_text = f"Fibonacci Golden Zone (0.50-0.618) + EMA 9/20 Reject | RSI: {rsi:.0f}"
                    return "SELL (SHORT) 🔴", "SELL", setup_text, "Fib 0.618 + EMA Reject", sl_raw, fib

    # SETUP C: 10-BAR STOP-HUNT LIQUIDITY SWEEP & RECLAIM
    pool_10   = df_5m.iloc[-13:-3]
    pool_high = float(pool_10["High"].max())
    pool_low  = float(pool_10["Low"].min())
    sweep_id  = f"SWEEP_{pool_low}_{pool_high}"

    if last_structure_traded.get(pair_name) != sweep_id:
        swept_below  = (low_0 < pool_low and price > pool_low) or (float(c1["Low"]) < pool_low and price > float(c1["High"]) and price > pool_low)
        bull_reclaim = (price > open_0) and (price > ema9) and (close_pos >= 0.62) and (l_wick >= float(c0["Range"]) * 0.28 or disp_power >= 0.95)
        if shock_state != "BEARISH_CRASH" and swept_below and bull_reclaim and (35.0 <= rsi <= 65.0) and now_utc >= sl_direction_lock[pair_name]["BUY"]:
            sl_raw = min(low_0, float(c1["Low"])) - (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
            setup_text = f"Liquidity Sweep Stop-Hunt & Bullish Reclaim ({pool_low:.2f}) | RSI: {rsi:.0f}"
            if fib is None:
                fib = {"swing_id": sweep_id, "fib_500": pool_low, "fib_618": pool_low, "fib_786": sl_raw}
            else:
                fib["swing_id"] = sweep_id
            return "BUY (LONG) 🟢", "BUY", setup_text, "Liquidity Sweep Reclaim", sl_raw, fib

        swept_above  = (high_0 > pool_high and price < pool_high) or (float(c1["High"]) > pool_high and price < float(c1["Low"]) and price < pool_high)
        bear_reclaim = (price < open_0) and (price < ema9) and (close_pos <= 0.38) and (u_wick >= float(c0["Range"]) * 0.28 or disp_power >= 0.95)
        if shock_state != "BULLISH_SURGE" and swept_above and bear_reclaim and (35.0 <= rsi <= 65.0) and now_utc >= sl_direction_lock[pair_name]["SELL"]:
            sl_raw = max(high_0, float(c1["High"])) + (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
            setup_text = f"Liquidity Sweep Stop-Hunt & Bearish Breakdown ({pool_high:.2f}) | RSI: {rsi:.0f}"
            if fib is None:
                fib = {"swing_id": sweep_id, "fib_500": pool_high, "fib_618": pool_high, "fib_786": sl_raw}
            else:
                fib["swing_id"] = sweep_id
            return "SELL (SHORT) 🔴", "SELL", setup_text, "Resistance Sweep Reject", sl_raw, fib

    # SETUP D: 6-BAR CONSOLIDATION BOX BREAKOUT
    box_6    = df_5m.iloc[-8:-2]
    box_high = float(box_6["High"].max())
    box_low  = float(box_6["Low"].min())
    box_rng  = box_high - box_low
    box_id   = f"BOX_{box_low}_{box_high}"

    if box_rng <= (atr_5m * 2.2) and last_structure_traded.get(pair_name) != box_id:
        if htf_bullish and price > box_high and price > open_0 and ema9 > ema20 and body_ratio >= 0.55 and close_pos >= 0.68 and disp_power >= 1.15 and (45.0 <= rsi <= 72.0):
            if now_utc >= sl_direction_lock[pair_name]["BUY"]:
                sl_raw = max(box_low, low_0) - (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
                setup_text = f"Consolidation Box Breakout Above {box_high:.2f} | RSI: {rsi:.0f}"
                if fib is None:
                    fib = {"swing_id": box_id, "fib_500": box_high, "fib_618": box_low, "fib_786": sl_raw}
                else:
                    fib["swing_id"] = box_id
                return "BUY (LONG) 🟢", "BUY", setup_text, "Range Box Breakout", sl_raw, fib

        if htf_bearish and price < box_low and price < open_0 and ema9 < ema20 and body_ratio >= 0.55 and close_pos <= 0.32 and disp_power >= 1.15 and (28.0 <= rsi <= 55.0):
            if now_utc >= sl_direction_lock[pair_name]["SELL"]:
                sl_raw = min(box_high, high_0) + (atr_5m * SL_STOP_HUNT_BUFFER_ATR)
                setup_text = f"Consolidation Box Breakdown Below {box_low:.2f} | RSI: {rsi:.0f}"
                if fib is None:
                    fib = {"swing_id": box_id, "fib_500": box_low, "fib_618": box_high, "fib_786": sl_raw}
                else:
                    fib["swing_id"] = box_id
                return "SELL (SHORT) 🔴", "SELL", setup_text, "Support Box Breakdown", sl_raw, fib

    return None, None, None, None, None, None

# =====================================================================
# 7. DENSE, NON-SKIPPING MT5 CHART GENERATOR
# =====================================================================
def generate_mt5_mobile_style_chart(df_5m, info, direction, entry_p, sl_p, tp_p, near_tp_p, short_tag, lot_size):
    """
    बिना किसी गैप या स्किप के (Tight-Packed 34 Candles) साफ चार्ट बनाता है:
    - हर कैंडल चौड़ी (width=0.78) और एक-दूसरे के बिल्कुल पास होती है।
    - आखिरी लाइव कैंडल तक पूरा ताज़ा प्राइस मूवमेंट दिखता है।
    """
    try:
        sub = df_5m.iloc[-34:].copy()
        if sub.empty or len(sub) < 16:
            return None

        dec      = info["decimals"]
        mt5_sym  = info["mt5_name"]
        sub_Desc = info.get("sub_title", "Spot vs US Dollar")

        fig = plt.figure(figsize=(6.8, 10.0), dpi=130, facecolor="#ffffff")
        ax  = fig.add_axes([0.04, 0.08, 0.80, 0.78], facecolor="#ffffff")

        n_bars = len(sub)
        x_vals = np.arange(n_bars)
        zone_start_idx = max(8, int(n_bars * 0.38))
        right_edge_idx = n_bars + 3

        all_y = list(sub["High"].values) + list(sub["Low"].values) + [tp_p, sl_p, entry_p]
        y_min, y_max = min(all_y), max(all_y)
        total_y_span = max(y_max - y_min, 1e-6)

        # 1. MT5 White Dashed Grid
        ax.grid(True, linestyle="--", linewidth=0.75, color="#d1d5db", alpha=0.85, zorder=1)
        for spine in ["top", "left"]:
            ax.spines[spine].set_visible(False)
        ax.spines["right"].set_color("#9ca3af")
        ax.spines["bottom"].set_color("#9ca3af")

        # 2. Plot Smooth EMA 9 & EMA 20 Lines
        if "EMA9" in sub.columns and "EMA20" in sub.columns:
            ax.plot(x_vals, sub["EMA9"].values, color="#16a34a", linewidth=1.25, alpha=0.80, zorder=3)
            ax.plot(x_vals, sub["EMA20"].values, color="#dc2626", linewidth=1.25, alpha=0.80, zorder=3)

        # 3. Plot Tightly-Packed Candlesticks (width=0.78 so zero skipping/gaps)
        min_visible_body = total_y_span * 0.0055
        for idx in range(n_bars):
            row = sub.iloc[idx]
            o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
            is_bull = c >= o
            wick_col = "#0d9488" if is_bull else "#e11d48"
            body_col = "#10b981" if is_bull else "#ef4444"

            ax.plot([idx, idx], [l, h], color=wick_col, linewidth=1.3, zorder=4)
            body_low = min(o, c)
            body_h   = max(abs(c - o), min_visible_body)
            ax.add_patch(mpatches.Rectangle(
                (idx - 0.39, body_low), 0.78, body_h,
                facecolor=body_col, edgecolor=wick_col, linewidth=0.7, zorder=4
            ))

        # 4. Red STOP-LOSS Zone Box
        sl_band_h = abs(sl_p - entry_p) * 0.35
        if direction == "SELL":
            red_rect_y = sl_p - sl_band_h
            red_rect_h = sl_band_h
        else:
            red_rect_y = sl_p
            red_rect_h = sl_band_h

        ax.add_patch(mpatches.Rectangle(
            (zone_start_idx, red_rect_y), right_edge_idx - zone_start_idx, red_rect_h,
            facecolor="#ef4444", alpha=0.23, edgecolor="#b91c1c", linewidth=1.2, zorder=2
        ))
        ax.plot([zone_start_idx, right_edge_idx], [sl_p, sl_p], color="#991b1b", linewidth=1.6, zorder=5)

        # 5. Green TARGET Zone Box + Directional Arrow
        tp_box_low  = min(entry_p, tp_p)
        tp_box_high = max(entry_p, tp_p)
        ax.add_patch(mpatches.Rectangle(
            (zone_start_idx + 3, tp_box_low), (right_edge_idx - zone_start_idx - 3), (tp_box_high - tp_box_low),
            facecolor="#22c55e", alpha=0.22, edgecolor="#16a34a", linewidth=1.1, zorder=2
        ))

        arrow_x       = n_bars + 0.8
        arrow_start_y = entry_p + (tp_p - entry_p) * 0.12
        arrow_end_y   = entry_p + (tp_p - entry_p) * 0.88
        ax.annotate(
            "", xy=(arrow_x, arrow_end_y), xytext=(arrow_x, arrow_start_y),
            arrowprops=dict(arrowstyle="->,head_width=0.55,head_length=0.55", color="#15803d", lw=2.5),
            zorder=6
        )

        # 6. Yellow Horizontal ENTRY Line
        ax.axhline(entry_p, color="#eab308", linewidth=2.0, zorder=5)

        entry_s   = format_price(entry_p, dec)
        sl_s      = format_price(sl_p, dec)
        near_tp_s = format_price(near_tp_p, dec)
        tp_s      = format_price(tp_p, dec)

        mid_zone_x = (zone_start_idx + right_edge_idx) / 2.0

        # STOP-LOSS Label
        sl_va     = "bottom" if direction == "SELL" else "top"
        sl_offset = abs(sl_p - entry_p) * (0.08 if direction == "SELL" else -0.08)
        ax.text(
            mid_zone_x, sl_p + sl_offset,
            f"STOP-LOSS (SL): {sl_s} ({short_tag})",
            fontsize=8.6, fontweight="bold", color="#111827", ha="center", va=sl_va,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#fecaca", edgecolor="#f87171", alpha=0.93),
            zorder=8
        )

        # ENTRY Label
        entry_va     = "bottom" if direction == "SELL" else "top"
        entry_offset = abs(tp_p - entry_p) * (0.06 if direction == "SELL" else -0.06)
        ax.text(
            right_edge_idx - 0.3, entry_p + entry_offset,
            f"{direction} ENTRY: {entry_s}\n({short_tag})",
            fontsize=8.5, fontweight="bold", color="#111827", ha="right", va=entry_va,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#fef08a", edgecolor="#eab308", alpha=0.94),
            zorder=8
        )

        # TARGET 1 & TARGET 2 Label
        tp_va     = "top" if direction == "SELL" else "bottom"
        tp_offset = abs(tp_p - entry_p) * (-0.06 if direction == "SELL" else 0.06)
        ax.text(
            mid_zone_x + 0.5, tp_p + tp_offset,
            f"TARGET 1 (Easy TP): {near_tp_s} | TARGET 2: {tp_s}",
            fontsize=8.5, fontweight="bold", color="#111827", ha="center", va=tp_va,
            bbox=dict(boxstyle="round,pad=0.25", facecolor="#bbf7d0", edgecolor="#22c55e", alpha=0.94),
            zorder=8
        )

        # 7. Right Axis & Live Price Tag
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", labelsize=9.5, colors="#111827")
        ax.set_xlim(-0.8, right_edge_idx)

        y_pad = total_y_span * 0.22
        ax.set_ylim(y_min - y_pad, y_max + y_pad)

        now_ist_str = get_ist_now().strftime("%H:%M:%S")
        ax.text(
            right_edge_idx + 0.2, entry_p,
            f" {entry_s} \n {now_ist_str} ",
            fontsize=8.2, fontweight="bold", color="white", va="center", ha="left",
            bbox=dict(boxstyle="square,pad=0.28", facecolor="#0d9488", edgecolor="none"),
            clip_on=False, zorder=9
        )

        tick_indices = [1, int(n_bars * 0.48), n_bars - 1]
        tick_labels  = []
        for ti in tick_indices:
            ts_ist = to_utc_timestamp(sub.index[ti]) + timedelta(hours=5, minutes=30)
            tick_labels.append(ts_ist.strftime("%d %b %H:%M"))
        ax.set_xticks(tick_indices)
        ax.set_xticklabels(tick_labels, fontsize=9, color="#374151")

        ax.text(0.02, 0.97, f"{mt5_sym} • M5", transform=ax.transAxes, fontsize=12, fontweight="bold", color="#0284c7", va="top")
        ax.text(0.02, 0.935, sub_Desc, transform=ax.transAxes, fontsize=9.5, color="#1f2937", va="top")

        header_ax = fig.add_axes([0.04, 0.88, 0.92, 0.08], facecolor="#ffffff")
        header_ax.axis("off")
        header_ax.add_patch(mpatches.Rectangle((0.0, 0.1), 0.32, 0.8, facecolor="#2563eb", edgecolor="none"))
        header_ax.text(0.16, 0.5, f"SELL\n{entry_s}", color="white", fontweight="bold", fontsize=11, ha="center", va="center")

        header_ax.add_patch(mpatches.Rectangle((0.33, 0.1), 0.34, 0.8, facecolor="#f9fafb", edgecolor="#d1d5db"))
        header_ax.text(0.50, 0.5, f"Lot: {lot_size:.2f}", color="#111827", fontweight="bold", fontsize=11, ha="center", va="center")

        header_ax.add_patch(mpatches.Rectangle((0.68, 0.1), 0.32, 0.8, facecolor="#ef4444", edgecolor="none"))
        header_ax.text(0.84, 0.5, f"BUY\n{entry_s}", color="white", fontweight="bold", fontsize=11, ha="center", va="center")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=130, facecolor="#ffffff")
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Chart Generation Error: {e}")
        return None

def send_telegram_signal_with_chart(caption_html, chart_buf, target_chat_id=CHAT_ID):
    if chart_buf is not None:
        photo_bytes = chart_buf.getvalue()
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        try:
            if len(caption_html) <= 950:
                files = {"photo": ("trade_setup.png", photo_bytes, "image/png")}
                data  = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                res   = requests.post(url, data=data, files=files, timeout=20)
                if res.status_code == 200 and res.json().get("ok"):
                    return

            files = {"photo": ("trade_setup.png", photo_bytes, "image/png")}
            data  = {"chat_id": target_chat_id, "caption": "📊 <b>Live M5 Trade Setup</b>", "parse_mode": "HTML"}
            requests.post(url, data=data, files=files, timeout=20)
            send_telegram_message(caption_html, target_chat_id)
            return
        except Exception as e:
            print(f"Telegram Photo Send Error: {e}")

    send_telegram_message(caption_html, target_chat_id)

# =====================================================================
# 8. MULTI-PAIR CHART COMMANDS & ACTIVE TRADE MANAGEMENT
# =====================================================================
def send_instant_preview_chart(pair_key=None):
    """
    अगर कोई खास पेयर पूछा गया है (जैसे /eurusd, /gbpusd, /btc, /gold) तो उसका चार्ट भेजता है।
    अगर सिर्फ /chart लिखा गया है, तो सभी 7 पेयर्स में से सबसे मजबूत एक्टिव सेटअप या अगले पेयर का चार्ट भेजता है (सिर्फ Gold नहीं)।
    """
    global chart_rotation_index
    pair_names_list = list(FOREX_PAIRS.keys())

    if pair_key is None:
        # पहले चेक करें कि किस पेयर में अभी लाइव सिग्नल बन रहा है
        selected_key = None
        for name, info in FOREX_PAIRS.items():
            df_15m, df_5m, live_spot, _ = get_clean_spot_data(name, info)
            if df_5m is not None and df_15m is not None:
                df_15m = calculate_chart_indicators(df_15m)
                df_5m  = calculate_chart_indicators(df_5m)
                sig, _, _, _, _, _ = evaluate_all_market_setups(name, info, df_15m, df_5m)
                if sig:
                    selected_key = name
                    break

        # अगर अभी किसी में तुरंत सिग्नल नहीं है, तो बारी-बारी से अलग-अलग पेयर का ताज़ा चार्ट दिखाएं
        if selected_key is None:
            selected_key = pair_names_list[chart_rotation_index % len(pair_names_list)]
            chart_rotation_index += 1
        pair_key = selected_key

    info = FOREX_PAIRS.get(pair_key, FOREX_PAIRS["XAU/USD (GOLD)"])
    df_15m, df_5m, live_spot, src = get_clean_spot_data(pair_key, info)
    if df_5m is None or live_spot is None:
        send_telegram_message("⚠️ डेटा लोड हो रहा है, कृपया कुछ सेकंड बाद प्रयास करें।")
        return

    df_5m  = calculate_chart_indicators(df_5m)
    df_15m = calculate_chart_indicators(df_15m)

    sig, direction, setup_text, short_tag, custom_sl, _ = evaluate_all_market_setups(pair_key, info, df_15m, df_5m)
    c0     = df_5m.iloc[-2]
    atr_5m = float(c0["ATR"])
    ema9   = float(c0["EMA9"])
    ema20  = float(c0["EMA20"])

    if direction is None:
        direction  = "BUY" if ema9 >= ema20 else "SELL"
        short_tag  = "EMA 9/20 + Structure"
        setup_text = f"Live M5 Structure ({direction} Bias) | RSI: {float(c0['RSI']):.0f}"
        custom_sl  = (live_spot - atr_5m * 1.4) if direction == "BUY" else (live_spot + atr_5m * 1.4)

    sl_level, sl_dist, lock_level, near_tp_lvl, target_level, tp_dist, actual_rr = calculate_smart_sl_and_easy_tp(
        df_5m, df_15m, info, direction, live_spot, custom_sl, atr_5m
    )
    lot_size = calculate_exact_mt5_lot(pair_key, info, sl_dist, RISK_PER_TRADE_USD)

    chart_buf = generate_mt5_mobile_style_chart(
        df_5m, info, direction, live_spot, sl_level, target_level, near_tp_lvl, short_tag, lot_size
    )
    caption = (
        f"📊 <b>{pair_key} ({info['mt5_name']}) — LIVE M5 ANALYSIS</b>\n\n"
        f"🧠 <b>Structure:</b> {setup_text}\n"
        f"📍 <b>Live Spot:</b> <code>{format_price(live_spot, info['decimals'])}</code>\n"
        f"🛑 <b>Buffered SL:</b> <code>{format_price(sl_level, info['decimals'])}</code>\n"
        f"🎯 <b>Target 1 (Easy TP):</b> <code>{format_price(near_tp_lvl, info['decimals'])}</code> | <b>Target 2:</b> <code>{format_price(target_level, info['decimals'])}</code>\n"
        f"💼 <b>Lot Size:</b> <code>{lot_size:.2f} Lot</code>"
    )
    send_telegram_signal_with_chart(caption, chart_buf)

def send_live_status_summary():
    forex_open = is_forex_market_open()
    market_state = "🟢 ALL MARKETS ACTIVE" if forex_open else "🟡 WEEKEND: BTC/USD ACTIVE"
    _, session_name = get_trading_session(True)

    report = (
        f"📊 <b>LIVE MARKET SCANNER STATUS</b> 📊\n\n"
        f"📢 <b>Session:</b> {market_state} ({session_name})\n\n"
    )

    for name, info in FOREX_PAIRS.items():
        try:
            df_15m, df_5m, live_spot, src = get_clean_spot_data(name, info)
            if df_5m is None or df_15m is None:
                continue
            df_5m = calculate_chart_indicators(df_5m)
            c = df_5m.iloc[-1]
            rsi = float(c["RSI"])
            p_str = format_price(live_spot, info["decimals"])
            report += f"🔹 <b>{name}:</b> <code>{p_str}</code> | RSI: {rsi:.0f}\n"
        except Exception:
            pass

    send_telegram_message(report)

def check_telegram_commands():
    global last_update_id
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 2}
    try:
        response = requests.get(url, params=params, timeout=5).json()
        if response.get("ok"):
            for result in response.get("result", []):
                last_update_id = result["update_id"]
                message = result.get("message", {})
                chat_id = str(message.get("chat", {}).get("id", ""))
                msg_text = message.get("text", "").strip().lower()

                if chat_id != CHAT_ID:
                    continue

                if msg_text in ["/status", "status", "/start", "start"]:
                    send_live_status_summary()
                elif msg_text in ["/chart", "chart"]:
                    send_instant_preview_chart(None)
                else:
                    # चेक करें कि यूजर ने किसी खास पेयर का नाम लिखा है क्या (जैसे /eurusd, /gbpusd, /usdjpy, /btc, /gold)
                    for p_name, p_info in FOREX_PAIRS.items():
                        if msg_text in p_info.get("cmd_keys", []) or msg_text == f"/chart {p_info['mt5_name'].lower()}":
                            send_instant_preview_chart(p_name)
                            break
    except Exception:
        pass

def manage_open_trades(pair_name, candle_time, live_spot, latest_5m, decimals):
    global daily_realized_pnl, total_account_pnl, pair_cooldown_until, sl_direction_lock

    now_utc = get_utc_now()
    current_high = max(live_spot, float(latest_5m["High"]))
    current_low  = min(live_spot, float(latest_5m["Low"]))

    for trade in daily_trades_log:
        if trade["pair"] == pair_name and trade["is_open"]:
            if to_utc_timestamp(candle_time) <= trade["entry_candle_time"]:
                continue

            entry_str  = format_price(trade["entry"], decimals)
            target_str = format_price(trade["target"], decimals)
            sl_str     = format_price(trade["sl"], decimals)
            price_str  = format_price(live_spot, decimals)

            if not trade["trailed_to_cost"]:
                half_reached = False
                if trade["direction"] == "BUY" and current_high >= trade["half_target"]:
                    half_reached = True
                elif trade["direction"] == "SELL" and current_low <= trade["half_target"]:
                    half_reached = True

                if half_reached:
                    trade["trailed_to_cost"] = True
                    trade["sl"]              = trade["entry"]
                    half_prof                = round(trade["max_risk"] * 0.5, 1)
                    trade["status"]          = "0.65R LOCKED"
                    send_telegram_message(
                        f"🛡️ <b>0.65R SAFE LOCK REACHED — MOVE SL TO ENTRY</b> 🛡️\n\n"
                        f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                        f"📍 <b>Entry:</b> {entry_str}  |  <b>Live Spot:</b> {price_str}\n"
                        f"📢 <b>Action:</b> 50% लॉट बुक करें (<b>+${half_prof:.0f}</b>) और बचे हुए लॉट का SL <b>Entry ({entry_str})</b> पर लगा दें।"
                    )

            if not trade.get("near_tp_alerted", False):
                near_hit = False
                if trade["direction"] == "BUY" and current_high >= trade["near_target"] and current_high < trade["target"]:
                    near_hit = True
                elif trade["direction"] == "SELL" and current_low <= trade["near_target"] and current_low > trade["target"]:
                    near_hit = True

                if near_hit:
                    trade["near_tp_alerted"] = True
                    near_prof = round(trade["expected_profit"] * 0.85, 1)
                    send_telegram_message(
                        f"⚡ <b>TARGET 1 (Easy TP) HIT — BOOK / LOCK PROFIT NOW</b> ⚡\n\n"
                        f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                        f"📍 <b>Live Price:</b> {price_str}  |  <b>Target 2:</b> {target_str}\n"
                        f"💰 <b>Action:</b> Easy Target 1 हिट हो चुका है (<b>+${near_prof:.0f}</b>)। यहाँ 75-80% प्रॉफिट बुक कर लें!"
                    )

            hit_type = None
            if trade["direction"] == "BUY":
                if current_high >= trade["target"]:
                    hit_type = "TARGET"
                elif current_low <= trade["sl"]:
                    hit_type = "TRAILING_SL" if trade["trailed_to_cost"] else "SL"
            elif trade["direction"] == "SELL":
                if current_low <= trade["target"]:
                    hit_type = "TARGET"
                elif current_high >= trade["sl"]:
                    hit_type = "TRAILING_SL" if trade["trailed_to_cost"] else "SL"

            if hit_type == "TARGET":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=35)
                net_win = trade["expected_profit"]
                daily_realized_pnl += net_win
                total_account_pnl += net_win
                trade["status"] = "TARGET HIT"
                send_telegram_message(
                    f"🎯 <b>FULL TARGET HIT (+${net_win:.0f})</b> 🎯\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target:</b> {target_str}\n"
                    f"💰 <b>Net Profit:</b> <b>+${net_win:.0f}</b>"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=25)
                half_prof = round(trade["max_risk"] * 0.5, 1)
                daily_realized_pnl += half_prof
                total_account_pnl += half_prof
                trade["status"] = "BREAKEVEN EXIT"
                send_telegram_message(
                    f"🛡️ <b>SAFE BREAKEVEN EXIT (+${half_prof:.0f})</b> 🛡️\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                    f"📍 <b>Exit at Cost:</b> {entry_str}  |  <b>Locked Profit:</b> <b>+${half_prof:.0f}</b>"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=50)
                sl_direction_lock[pair_name][trade["direction"]] = now_utc + timedelta(minutes=90)

                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_account_pnl -= loss_amt
                trade["status"] = "SL HIT"
                send_telegram_message(
                    f"🛑 <b>STOP-LOSS HIT</b> 🛑\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>SL:</b> {sl_str}\n"
                    f"⚖️ <b>Risk:</b> -${loss_amt:.0f}"
                )
                continue

def scan_markets():
    global trades_today, current_day, last_candle_traded, last_structure_traded, daily_trades_log, daily_realized_pnl, last_global_trade_time

    today = get_ist_now().date()
    if today != current_day:
        current_day = today
        trades_today = 0
        daily_trades_log = []
        daily_realized_pnl = 0.0
        last_candle_traded = {name: None for name in FOREX_PAIRS}

    if daily_realized_pnl <= -DAILY_PROTECTION_CAP:
        return

    forex_open = is_forex_market_open()
    usdjpy_spot = fetch_tradingview_live_spot("OANDA:USDJPY", "forex") or 152.0

    for name, info in FOREX_PAIRS.items():
        if not forex_open and not info.get("is_crypto", False):
            continue

        try:
            df_15m, df_5m, live_spot, source_used = get_clean_spot_data(name, info)
            if df_5m is None or df_15m is None or live_spot is None:
                continue

            df_15m = calculate_chart_indicators(df_15m)
            df_5m  = calculate_chart_indicators(df_5m)

            latest_5m   = df_5m.iloc[-1]
            closed_5m   = df_5m.iloc[-2]
            closed_time = to_utc_timestamp(df_5m.index[-2])
            atr_5m      = float(closed_5m["ATR"])
            dec         = info["decimals"]

            manage_open_trades(name, closed_time, live_spot, latest_5m, dec)

            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            open_trades_count = sum(1 for t in daily_trades_log if t["is_open"])
            if open_trades_count >= MAX_CONCURRENT_TRADES:
                continue

            if (get_utc_now() - last_global_trade_time).total_seconds() < (MIN_GAP_BETWEEN_TRADES_MIN * 60):
                continue

            if last_candle_traded[name] == closed_time:
                continue

            signal, direction, setup_text, short_tag, custom_sl, struct_info = evaluate_all_market_setups(
                name, info, df_15m, df_5m
            )

            if signal and custom_sl is not None:
                if direction == "BUY" and custom_sl >= live_spot:
                    continue
                if direction == "SELL" and custom_sl <= live_spot:
                    continue

                sl_level, sl_dist, half_target_level, near_tp_level, target_level, target_dist, actual_rr = calculate_smart_sl_and_easy_tp(
                    df_5m, df_15m, info, direction, live_spot, custom_sl, atr_5m
                )

                if sl_dist <= 0 or target_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                if struct_info is not None and "swing_id" in struct_info:
                    last_structure_traded[name] = struct_info["swing_id"]
                last_global_trade_time = get_utc_now()
                trades_today += 1

                max_risk_usd  = RISK_PER_TRADE_USD
                expected_prof = round(max_risk_usd * actual_rr, 1)
                exact_lot     = calculate_exact_mt5_lot(name, info, sl_dist, max_risk_usd, usdjpy_spot)

                daily_trades_log.append({
                    "id": trades_today,
                    "pair": name,
                    "mt5_name": info["mt5_name"],
                    "action": signal,
                    "direction": direction,
                    "strategy": setup_text,
                    "lot_size": exact_lot,
                    "entry": live_spot,
                    "sl": sl_level,
                    "sl_dist": sl_dist,
                    "half_target": half_target_level,
                    "near_target": near_tp_level,
                    "target": target_level,
                    "max_risk": max_risk_usd,
                    "expected_profit": expected_prof,
                    "entry_candle_time": closed_time,
                    "trailed_to_cost": False,
                    "near_tp_alerted": False,
                    "is_open": True,
                    "status": "RUNNING"
                })

                entry_s   = format_price(live_spot, dec)
                sl_s      = format_price(sl_level, dec)
                half_s    = format_price(half_target_level, dec)
                near_tp_s = format_price(near_tp_level, dec)
                target_s  = format_price(target_level, dec)

                sl_pips_str     = calculate_pips_or_points(name, sl_dist, dec)
                target_pips_str = calculate_pips_or_points(name, target_dist, dec)

                msg = (
                    f"⚡ <b>LIVE MARKET SIGNAL — {info['mt5_name']}</b> ⚡\n\n"
                    f"🌍 <b>Asset:</b> <b>{name}</b>\n"
                    f"📢 <b>Action:</b> <b>{signal}</b>\n"
                    f"🧠 <b>Setup:</b> {setup_text}\n\n"
                    f"📍 <b>Entry Price:</b> <code>{entry_s}</code>\n"
                    f"🛑 <b>Stop-Loss (SL):</b> <code>{sl_s}</code>  <i>({sl_pips_str})</i>\n"
                    f"🎯 <b>Target 1 (Easy TP):</b> <code>{near_tp_s}</code>\n"
                    f"🎯 <b>Target 2 (Full TP):</b> <code>{target_s}</code>  <i>({target_pips_str})</i>\n"
                    f"🛡️ <b>0.65R Safe Lock:</b> <code>{half_s}</code>\n\n"
                    f"💼 <b>Lot Size:</b> <b><code>{exact_lot:.2f} Lot</code></b>\n"
                    f"💰 <b>Risk:</b> ${max_risk_usd:.0f}  ➡️  <b>Reward:</b> <b>+${expected_prof:.0f}</b>"
                )

                chart_buf = generate_mt5_mobile_style_chart(
                    df_5m, info, direction, live_spot, sl_level, target_level,
                    near_tp_level, short_tag, exact_lot
                )
                send_telegram_signal_with_chart(msg, chart_buf)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    fetch_live_economic_calendar()
    send_telegram_message("🟢 <b>Market Scanner Online & Active</b>")

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 25:
            scan_markets()
            last_scan_time = time.time()
        time.sleep(3)
