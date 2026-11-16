import os
import io
import sys
import time
import subprocess
from datetime import datetime, timedelta, timezone

# =====================================================================
# 0. AUTO-INSTALL REQUIRED PACKAGES (RUNS BEFORE IMPORTS)
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

# Persistent HTTP session for ultra-fast API & Telegram calls
HTTP_SESSION = requests.Session()
HTTP_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Cache-Control": "no-cache, no-store, must-revalidate",
    "Pragma": "no-cache"
})

# =====================================================================
# 1. MASTER SETTINGS ($5,000 -> $5,400 | GITHUB -> TELEGRAM MANUAL BOT)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID   = os.getenv("CHAT_ID") or "5610520408"

STARTING_CAPITAL_USD        = 5000.0
TARGET_EQUITY_USD           = 5400.0    # $5,400 Target (+$400 Goal)

# "My Own Money" Strict Survival Limits (FTM Rules: -$200 Daily / -$400 Max)
HUMAN_DAILY_STOP_LOSS_USD   = 72.0      # -$72 पर उस दिन के लिए सिग्नल बंद
HUMAN_DAILY_PROFIT_LOCK     = 125.0     # एक दिन में +$125 बनने पर मुनाफा लॉक
HARD_EQUITY_FLOOR_USD       = 4700.0    # $4,600 मैक्स लॉस से $100 ऊपर सुरक्षा फ्लोर

BASE_RISK_PER_TRADE_USD     = 24.0      # सामान्य रिस्क: $24
WIN_STREAK_RISK_USD         = 26.0      # पिछला ट्रेड जीतने के बाद रिस्क: $26
CAUTION_RISK_PER_TRADE_USD  = 14.0      # 1 SL लगने के बाद सावधानी रिस्क: $14
MIN_RISK_PER_TRADE_USD      = 8.0       # जब $5,400 टारगेट बहुत करीब हो

MAX_DAILY_TRADES_CAP        = 10        # पूरे दिन में अधिकतम 10 हाई-क्वालिटी ट्रेड्स
MAX_CONSECUTIVE_LOSSES_HALT = 3         # लगातार 3 SL पर 90 मिनट का कूलडाउन
MIN_HUMAN_SCORE_REQUIRED    = 78        # 100 में से कम से कम 78 अंक अनिवार्य

BASE_EASY_RR_RATIO          = 1.40      # Realistic Full Target (1:1.40 RR)
LOCK_PROFIT_R               = 0.55      # 0.55R (+$13) पर SL सीधा Entry (Cost) पर लॉक
NEAR_TARGET_ALERT_RATIO     = 0.78      # 78% (TP1) पर 50% प्रॉफिट बुक + SL 0.55R पर ट्रेल
SL_BUFFER_ATR               = 0.24      # असली Swing High/Low के पीछे सुरक्षित बफर
MAX_TRADE_HOLD_MINUTES      = 50        # 50 मिनट तक ट्रेड बिना मोमेंटम फँसी रहे तो बाहर

MAX_CONCURRENT_TRADES       = 2         # एक समय में अधिकतम 2 ओपन ट्रेड्स
MIN_GAP_BETWEEN_TRADES_MIN  = 6         # दो ट्रेड्स के बीच 6 मिनट का गैप

# 8 Liquid Pairs — Pure Spot Forex & Crypto Feeds
FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "mt5_name": "XAUUSD",
        "sub_title": "Gold Spot vs US Dollar",
        "cmd_keys": ["/xauusd", "xauusd", "/gold", "gold"],
        "tv_tickers": ["FOREXCOM:XAUUSD", "OANDA:XAUUSD", "FX_IDC:XAUUSD"],
        "tv_screener": "cfd",
        "binance_symbol": None,
        "yf_symbols": ["XAUUSD=X", "GC=F"],
        "currencies": ["USD"],
        "decimals": 2,
        "spread": 0.30,
        "unit_name": "Points",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 3.80,
        "max_sl_dist": 8.50,
        "max_lot_cap": 0.15,
        "is_crypto": False
    },
    "EUR/USD": {
        "mt5_name": "EURUSD",
        "sub_title": "Euro vs US Dollar",
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
        "min_sl_dist": 0.0009,
        "max_sl_dist": 0.0022,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "GBP/USD": {
        "mt5_name": "GBPUSD",
        "sub_title": "Great Britain Pound vs US Dollar",
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
        "min_sl_dist": 0.0010,
        "max_sl_dist": 0.0024,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "USD/JPY": {
        "mt5_name": "USDJPY",
        "sub_title": "US Dollar vs Japanese Yen",
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
        "min_sl_dist": 0.100,
        "max_sl_dist": 0.240,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "GBP/JPY": {
        "mt5_name": "GBPJPY",
        "sub_title": "Great Britain Pound vs Japanese Yen",
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
        "min_sl_dist": 0.120,
        "max_sl_dist": 0.250,
        "max_lot_cap": 0.25,
        "is_crypto": False
    },
    "AUD/USD": {
        "mt5_name": "AUDUSD",
        "sub_title": "Australian Dollar vs US Dollar",
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
        "min_sl_dist": 0.0008,
        "max_sl_dist": 0.0019,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "USD/CAD": {
        "mt5_name": "USDCAD",
        "sub_title": "US Dollar vs Canadian Dollar",
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
        "min_sl_dist": 0.0009,
        "max_sl_dist": 0.0021,
        "max_lot_cap": 0.30,
        "is_crypto": False
    },
    "BTC/USD (BITCOIN)": {
        "mt5_name": "BTCUSD",
        "sub_title": "Bitcoin Spot vs US Dollar",
        "cmd_keys": ["/btcusd", "btcusd", "/btc", "btc"],
        "tv_tickers": ["BITSTAMP:BTCUSD", "COINBASE:BTCUSD", "BINANCE:BTCUSDT"],
        "tv_screener": "crypto",
        "binance_symbol": "BTCUSDT",
        "yf_symbols": ["BTC-USD"],
        "currencies": ["USD"],
        "decimals": 2,
        "spread": 25.00,
        "unit_name": "Points",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 210.0,
        "max_sl_dist": 460.0,
        "max_lot_cap": 0.12,
        "is_crypto": True
    }
}

last_candle_traded     = {name: None for name in FOREX_PAIRS}
last_structure_traded  = {name: None for name in FOREX_PAIRS}
pair_cooldown_until    = {name: datetime.min.replace(tzinfo=timezone.utc) for name in FOREX_PAIRS}
sl_direction_lock      = {name: {"BUY": datetime.min.replace(tzinfo=timezone.utc), "SELL": datetime.min.replace(tzinfo=timezone.utc)} for name in FOREX_PAIRS}
last_global_trade_time = datetime.min.replace(tzinfo=timezone.utc)
global_pause_until     = datetime.min.replace(tzinfo=timezone.utc)
live_bars_memory       = {name: {} for name in FOREX_PAIRS}

# Stores only base historical candles; live price is ALWAYS fetched fresh in real-time!
ohlc_history_cache     = {}

cached_news_events     = []
last_news_fetch_time   = 0
announced_news_ids     = set()
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
        return True, "Crypto 24/7 Session"
    if not is_forex_market_open():
        return False, "Weekend Market Closed"
    if 3.0 <= hr <= 5.25:
        return False, "Rollover Spread Pause"
    elif 5.25 < hr < 12.0:
        return True, "Asian Session"
    elif 12.0 <= hr < 18.0:
        return True, "London Session"
    elif 18.0 <= hr < 22.5:
        return True, "London + NY Overlap"
    else:
        return True, "New York Session"

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

def calculate_pips_or_points(pair_name, price_diff, decimals):
    price_diff = abs(float(price_diff))
    if "GOLD" in pair_name or "BTC" in pair_name:
        return f"${price_diff:.2f} ({price_diff * 10:.0f} Pts)"
    elif decimals == 3:
        return f"{price_diff * 100:.1f} Pips"
    else:
        return f"{price_diff * 10000:.1f} Pips"

def calculate_exact_mt5_lot(pair_name, pair_info, sl_dist, risk_usd, live_usdjpy_rate=152.0, live_usdcad_rate=1.39):
    if sl_dist <= 0:
        return 0.02
    if "GOLD" in pair_name:
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
        return 0.02
    raw_lot = risk_usd / loss_per_1_lot
    max_cap = pair_info.get("max_lot_cap", 0.30)
    return max(0.01, min(round(raw_lot, 2), max_cap))

def get_live_account_metrics():
    est_eq = STARTING_CAPITAL_USD + total_account_pnl
    return est_eq, est_eq, daily_realized_pnl

# =====================================================================
# 2. OWN-MONEY CAPITAL SHIELD & CORRELATION / NEWS GUARDS
# =====================================================================
def human_pre_flight_safety_and_risk_calculator():
    global challenge_completed, daily_halt_announced
    now_utc = get_utc_now()
    bal, eq, day_pnl = get_live_account_metrics()

    if eq >= TARGET_EQUITY_USD:
        if not challenge_completed:
            challenge_completed = True
            send_telegram_message(
                f"🏆 <b>MISSION COMPLETE: $5,400 TARGET ACHIEVED!</b> 🏆\n\n"
                f"💰 <b>Projected Equity:</b> <b>${eq:.2f}</b>\n"
                f"🧠 <b>Action:</b> $5,400 का टारगेट पूरा हो चुका है, ओवर-ट्रेडिंग से बचने के लिए बॉट ने सिग्नल लॉक कर दिए हैं।"
            )
        return False, 0.0, "Target Achieved"

    if eq <= HARD_EQUITY_FLOOR_USD:
        if not daily_halt_announced:
            daily_halt_announced = True
            send_telegram_message(f"🛑 <b>EMERGENCY EQUITY SHIELD (${eq:.2f})</b>: कैपिटल सुरक्षा के लिए आज के सिग्नल बंद कर दिए गए हैं।")
        return False, 0.0, "Hard Equity Floor"

    if day_pnl <= -HUMAN_DAILY_STOP_LOSS_USD:
        if not daily_halt_announced:
            daily_halt_announced = True
            send_telegram_message(f"🛡️ <b>HUMAN DAILY LOSS BRAKE (-${abs(day_pnl):.2f})</b>: -$72 पर आज के लिए ट्रेडिंग रोक दी गई है।")
        return False, 0.0, "Daily Loss Limit"

    if day_pnl >= HUMAN_DAILY_PROFIT_LOCK:
        if not daily_halt_announced:
            daily_halt_announced = True
            send_telegram_message(f"✅ <b>GREEN DAY PROFIT LOCKED (+${day_pnl:.2f} TODAY!)</b>: आज का मुनाफा लॉक कर दिया गया है।")
        return False, 0.0, "Daily Profit Locked"

    if trades_today >= MAX_DAILY_TRADES_CAP:
        return False, 0.0, "Max 10 Daily Trades Reached"

    if now_utc < global_pause_until:
        return False, 0.0, "Cooldown Active"

    remaining_to_goal = TARGET_EQUITY_USD - eq
    if consecutive_losses >= 1 or day_pnl <= -18.0 or eq < 4960.0:
        risk_usd = CAUTION_RISK_PER_TRADE_USD
    elif consecutive_wins >= 1 and day_pnl > 0:
        risk_usd = WIN_STREAK_RISK_USD
    else:
        risk_usd = BASE_RISK_PER_TRADE_USD

    if remaining_to_goal < (risk_usd * 1.2):
        risk_usd = max(MIN_RISK_PER_TRADE_USD, round(remaining_to_goal * 0.85, 1))

    max_allowed_now = HUMAN_DAILY_STOP_LOSS_USD - abs(min(0.0, day_pnl))
    risk_usd = max(MIN_RISK_PER_TRADE_USD, min(risk_usd, max_allowed_now))

    return True, round(risk_usd, 1), "Clear"

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
        if -8.0 <= diff_mins <= 12.0:
            if ev["id"] not in announced_news_ids:
                announced_news_ids.add(ev["id"])
                ist_time = (ev["time_utc"] + timedelta(hours=5, minutes=30)).strftime("%I:%M %p IST")
                send_telegram_message(
                    f"🚨 <b>HIGH-IMPACT NEWS SHIELD ({ev['country']})</b> 🚨\n"
                    f"📰 <b>Event:</b> {ev['title']} ({ist_time})\n"
                    f"🛡️ <b>Action:</b> न्यूज़ स्पाइक से बचाव के लिए 12 मिनट का पॉज़ एक्टिव है।"
                )
            return False, f"News Pause: {ev['title']}"
    return True, "Clear"

def check_dxy_extreme_spike_guard(pair_info, direction):
    exp = pair_info.get("usd_exposure", "NEUTRAL")
    if exp == "NEUTRAL":
        return True
    url = f"https://scanner.tradingview.com/cfd/scan?cb={time.time_ns()}"
    payload = {"symbols": {"tickers": ["TVC:DXY"], "query": {"types": []}}, "columns": ["Recommend.Other", "RSI"]}
    try:
        res = HTTP_SESSION.post(url, json=payload, timeout=4)
        if res.status_code == 200:
            data = res.json().get("data", [])
            if data and data[0].get("d"):
                dxy_rec = float(data[0]["d"][0] or 0.0)
                dxy_rsi = float(data[0]["d"][1] or 50.0)
                if exp == "SHORT_USD_ON_BUY":
                    if direction == "BUY" and dxy_rec > 0.55 and dxy_rsi > 67.0:
                        return False
                    if direction == "SELL" and dxy_rec < -0.55 and dxy_rsi < 33.0:
                        return False
                elif exp == "LONG_USD_ON_BUY":
                    if direction == "BUY" and dxy_rec < -0.55 and dxy_rsi < 33.0:
                        return False
                    if direction == "SELL" and dxy_rec > 0.55 and dxy_rsi > 67.0:
                        return False
    except Exception:
        pass
    return True

def check_smart_usd_correlation_shield(pair_info, candidate_direction):
    exp_type = pair_info.get("usd_exposure", "NEUTRAL")
    if exp_type == "NEUTRAL":
        return True

    cand_usd_dir = "SHORT_USD" if (
        (exp_type == "SHORT_USD_ON_BUY" and candidate_direction == "BUY") or
        (exp_type == "LONG_USD_ON_BUY" and candidate_direction == "SELL")
    ) else "LONG_USD"

    for t in daily_trades_log:
        if t["is_open"] and not t.get("trailed_to_cost", False):
            t_info = FOREX_PAIRS.get(t["pair"], {})
            t_exp  = t_info.get("usd_exposure", "NEUTRAL")
            if t_exp == "NEUTRAL":
                continue
            open_usd_dir = "SHORT_USD" if (
                (t_exp == "SHORT_USD_ON_BUY" and t["direction"] == "BUY") or
                (t_exp == "LONG_USD_ON_BUY" and t["direction"] == "SELL")
            ) else "LONG_USD"
            if open_usd_dir == cand_usd_dir:
                return False
    return True

# =====================================================================
# 3. REAL-TIME TICK + HISTORY CACHE ENGINE (FRESH PRICE EVERY SECOND!)
# =====================================================================
def fetch_tradingview_median_spot(tv_tickers, screener="forex"):
    """Fetches un-cached, millisecond-fresh live spot price from TradingView"""
    if isinstance(tv_tickers, str):
        tv_tickers = [tv_tickers]
    # Unique nanosecond timestamp prevents any CDN/Proxy from returning a cached price!
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
    """
    Fetches the exact sub-second live market price right now (~0.15s):
    - For Crypto (BTCUSD): Queries live Binance real-time trade ticker first, then TradingView.
    - For Forex/Gold: Queries live TradingView multi-broker spot feed with cache-buster.
    """
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

def fetch_binance_vision_ohlc(binance_symbol, interval_str="5m", limit=120):
    if not binance_symbol:
        return None
    for base_url in ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]:
        params = {"symbol": binance_symbol, "interval": interval_str, "limit": limit}
        try:
            r = HTTP_SESSION.get(base_url, params=params, timeout=5).json()
            if isinstance(r, list) and len(r) >= 60:
                df = pd.DataFrame(r, columns=["open_time", "Open", "High", "Low", "Close", "Volume", "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"])
                df["Date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                df.set_index("Date", inplace=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = df[col].astype(float)
                return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            pass
    return None

def sanitize_ohlc_dataframe(df):
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
    if len(df) < 40:
        return None

    top_body = df[["Open", "Close"]].max(axis=1)
    bot_body = df[["Open", "Close"]].min(axis=1)
    df["High"] = np.maximum(df["High"], top_body)
    df["Low"]  = np.minimum(df["Low"], bot_body)

    if "Volume" not in df.columns:
        df["Volume"] = 1.0
    return df[["Open", "High", "Low", "Close", "Volume"]]

def get_clean_spot_data(pair_name, info, use_fast_cache=False):
    """
    Smart Hybrid Engine:
    1. Reuses historical closed candles for up to 60s (within the same 5m bar) so there is ZERO download delay.
    2. ALWAYS fetches a 100% brand-new live tick right at that millisecond and updates the running candle!
       So if price moves from 65367 to 65380 in 1 second, the next /chart immediately shows 65380!
    """
    now_ts     = time.time()
    now_utc    = get_utc_now()
    bucket_min = (now_utc.minute // 5) * 5
    bucket_ts  = now_utc.replace(minute=bucket_min, second=0, microsecond=0)

    raw_df_5m, raw_df_15m, source_used = None, None, "Institutional Spot Feed"

    # Check if we have valid historical candles for the current 5-minute bucket
    if use_fast_cache and pair_name in ohlc_history_cache:
        cached = ohlc_history_cache[pair_name]
        if (now_ts - cached["ts"]) <= 60.0 and cached.get("bucket_ts") == bucket_ts:
            raw_df_15m  = cached["raw_df_15m"].copy()
            raw_df_5m   = cached["raw_df_5m"].copy()
            source_used = cached["src"]

    # Download historical candles if cache expired or a new 5-minute candle started
    if raw_df_5m is None or raw_df_15m is None:
        if info.get("is_crypto") and info.get("binance_symbol"):
            raw_df_5m  = fetch_binance_vision_ohlc(info["binance_symbol"], "5m", 120)
            raw_df_15m = fetch_binance_vision_ohlc(info["binance_symbol"], "15m", 100)
            if raw_df_5m is not None and raw_df_15m is not None:
                source_used = "Live Crypto Spot Feed"

        if raw_df_5m is None or raw_df_15m is None:
            for yf_sym in info.get("yf_symbols", []):
                try:
                    tkr    = yf.Ticker(yf_sym)
                    r_5m   = tkr.history(period="5d", interval="5m", auto_adjust=False)
                    r_15m  = tkr.history(period="5d", interval="15m", auto_adjust=False)
                    c_5m   = sanitize_ohlc_dataframe(r_5m)
                    c_15m  = sanitize_ohlc_dataframe(r_15m)
                    if c_5m is not None and c_15m is not None:
                        raw_df_5m, raw_df_15m = c_5m, c_15m
                        break
                except Exception:
                    continue

        raw_df_5m  = sanitize_ohlc_dataframe(raw_df_5m)
        raw_df_15m = sanitize_ohlc_dataframe(raw_df_15m)
        if raw_df_5m is None or raw_df_15m is None:
            return None, None, None, ""

        ohlc_history_cache[pair_name] = {
            "ts": now_ts,
            "bucket_ts": bucket_ts,
            "raw_df_15m": raw_df_15m.copy(),
            "raw_df_5m": raw_df_5m.copy(),
            "src": source_used
        }

    df_5m  = raw_df_5m.copy()
    df_15m = raw_df_15m.copy()

    # ALWAYS fetch a 100% fresh real-time tick right now (never cached!)
    tv_spot = fetch_instant_live_tick(info)
    raw_last_close = float(df_5m.iloc[-1]["Close"])

    if tv_spot is not None and tv_spot > 0:
        basis_diff = tv_spot - raw_last_close
        if abs(basis_diff) <= (tv_spot * 0.05):
            # If external history has a futures/spot basis offset (like GC=F vs XAUUSD), shift history uniformly,
            # while keeping the running candle anchored to real-time ticks in this 5m bucket
            mem = live_bars_memory[pair_name]
            if bucket_ts not in mem:
                for col in ["Open", "High", "Low", "Close"]:
                    df_5m[col]  = df_5m[col] + basis_diff
                    df_15m[col] = df_15m[col] + basis_diff
                mem[bucket_ts] = {
                    "basis_at_open": basis_diff,
                    "Open": tv_spot,
                    "High": float(df_5m.iloc[-1]["High"]),
                    "Low": float(df_5m.iloc[-1]["Low"])
                }
            else:
                base_shift = mem[bucket_ts]["basis_at_open"]
                for col in ["Open", "High", "Low", "Close"]:
                    df_5m[col]  = df_5m[col] + base_shift
                    df_15m[col] = df_15m[col] + base_shift

            if info.get("is_crypto", False) or is_forex_market_open():
                mem[bucket_ts]["High"] = max(mem[bucket_ts]["High"], tv_spot)
                mem[bucket_ts]["Low"]  = min(mem[bucket_ts]["Low"], tv_spot)

                last_idx_5m = df_5m.index[-1]
                df_5m.loc[last_idx_5m, "Close"] = tv_spot
                df_5m.loc[last_idx_5m, "High"]  = max(float(df_5m.loc[last_idx_5m, "High"]), mem[bucket_ts]["High"])
                df_5m.loc[last_idx_5m, "Low"]   = min(float(df_5m.loc[last_idx_5m, "Low"]), mem[bucket_ts]["Low"])

                last_idx_15m = df_15m.index[-1]
                df_15m.loc[last_idx_15m, "Close"] = tv_spot
                df_15m.loc[last_idx_15m, "High"]  = max(float(df_15m.loc[last_idx_15m, "High"]), tv_spot)
                df_15m.loc[last_idx_15m, "Low"]   = min(float(df_15m.loc[last_idx_15m, "Low"]), tv_spot)

            live_spot_price = tv_spot
        else:
            live_spot_price = raw_last_close
    else:
        live_spot_price = raw_last_close

    return df_15m, df_5m, live_spot_price, source_used

# =====================================================================
# 4. HUMAN BRAIN INDICATORS, 7 NEGATIVE VETOES & 6 POSITIVE SETUPS
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
    df["ATR"] = tr.rolling(14).mean().bfill()

    df["Body"]       = np.abs(df["Close"] - df["Open"])
    df["Range"]      = (df["High"] - df["Low"]).replace(0, 1e-9)
    df["Body_Ratio"] = df["Body"] / df["Range"]
    df["Upper_Wick"] = df["High"] - df[["Open", "Close"]].max(axis=1)
    df["Lower_Wick"] = df[["Open", "Close"]].min(axis=1) - df["Low"]
    df["Close_Pos"]  = (df["Close"] - df["Low"]) / df["Range"]

    avg_body = df["Body"].rolling(16).mean().replace(0, 1e-9).bfill()
    df["Disp_Power"] = df["Body"] / avg_body

    delta = df["Close"].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["RSI"] = (100 - (100 / (1 + rs))).fillna(50.0)
    return df

def extract_fractal_swings(df, left=2, right=2):
    highs = df["High"].values
    lows  = df["Low"].values
    times = df.index
    swing_highs, swing_lows = [], []
    end_idx = len(df) - 1 - right
    for i in range(left, end_idx):
        is_sh = all(highs[i] >= highs[j] for j in range(i - left, i + right + 1) if j != i)
        is_sl = all(lows[i] <= lows[j] for j in range(i - left, i + right + 1) if j != i)
        if is_sh:
            swing_highs.append({"idx": i, "price": float(highs[i]), "time": times[i]})
        if is_sl:
            swing_lows.append({"idx": i, "price": float(lows[i]), "time": times[i]})
    return swing_highs, swing_lows

def human_negative_aspect_veto_audit(df_15m, df_5m, direction, live_spot, setup_type, atr_5m):
    c0     = df_5m.iloc[-2]
    price  = float(c0["Close"])
    ema20  = float(c0["EMA20"])
    ema50  = float(c0["EMA50"])
    rsi    = float(c0["RSI"])
    u_wick = float(c0["Upper_Wick"])
    l_wick = float(c0["Lower_Wick"])
    rng_0  = float(c0["Range"])

    # VETO 1: RUBBER-BAND OVEREXTENSION
    max_ema20_dist = atr_5m * (1.55 if setup_type in ["PRESSURE_BREAKOUT", "MOMENTUM_IGNITION"] else 1.28)
    if direction == "BUY" and ((live_spot - ema20) > max_ema20_dist or (live_spot - ema50) > (atr_5m * 2.75)):
        return False, "Overextended Above EMA20/50 (Top-Buying Blocked)"
    if direction == "SELL" and ((ema20 - live_spot) > max_ema20_dist or (ema50 - live_spot) > (atr_5m * 2.75)):
        return False, "Overextended Below EMA20/50 (Bottom-Selling Blocked)"

    # VETO 2: PARABOLIC ROCKET CHASING
    win_10  = df_5m.iloc[-12:-2]
    low_10  = float(win_10["Low"].min())
    high_10 = float(win_10["High"].max())
    span_10 = high_10 - low_10
    if direction == "BUY":
        pb_from_high = high_10 - float(df_5m.iloc[-5:-2]["Low"].min())
        if span_10 >= (atr_5m * 2.85) and pb_from_high < (span_10 * 0.24):
            return False, "Vertical Parabolic Pump (No Pullback Yet)"
    else:
        pb_from_low = float(df_5m.iloc[-5:-2]["High"].max()) - low_10
        if span_10 >= (atr_5m * 2.85) and pb_from_low < (span_10 * 0.24):
            return False, "Vertical Parabolic Dump (No Pullback Yet)"

    # VETO 3: 35-BAR RANGE ROOF / BASEMENT GUARD
    win_35   = df_5m.iloc[-37:-2]
    r35_hi   = float(win_35["High"].max())
    r35_lo   = float(win_35["Low"].min())
    r35_span = max(r35_hi - r35_lo, 1e-6)
    pos_35   = (live_spot - r35_lo) / r35_span
    if setup_type not in ["PRESSURE_BREAKOUT", "MOMENTUM_IGNITION"]:
        if direction == "BUY" and pos_35 > 0.85:
            return False, "Price in Top 15% Roof of 35-Bar Range"
        if direction == "SELL" and pos_35 < 0.15:
            return False, "Price in Bottom 15% Basement of 35-Bar Range"

    # VETO 4: CLIMAX CANDLE & OPPOSING REJECTION WICK ON TRIGGER CANDLE
    if rng_0 >= (atr_5m * 1.85):
        return False, "Trigger Candle Too Huge (Exhaustion Risk)"
    if direction == "BUY" and (u_wick / rng_0) > 0.32:
        return False, "Heavy Upper Seller Wick on Buy Candle"
    if direction == "SELL" and (l_wick / rng_0) > 0.32:
        return False, "Heavy Lower Buyer Wick on Sell Candle"

    # VETO 5: RSI EXTREME CEILING/FLOOR
    if direction == "BUY" and rsi > 66.0:
        return False, f"RSI Overbought ({rsi:.0f})"
    if direction == "SELL" and rsi < 34.0:
        return False, f"RSI Oversold ({rsi:.0f})"

    # VETO 6: DEAD / BARCODE CHOPPY MARKET
    recent_8 = df_5m.iloc[-10:-2]
    if float(recent_8["High"].max() - recent_8["Low"].min()) < (atr_5m * 1.15) or float(recent_8["Body_Ratio"].mean()) < 0.24:
        return False, "Dead Barcode Choppy Market"

    # VETO 7: UNRECOVERED GIANT OPPOSITE SHOCK CANDLE IN LAST 3 BARS
    for idx in range(-4, -1):
        bar = df_5m.iloc[idx]
        b_open, b_close, b_rng = float(bar["Open"]), float(bar["Close"]), float(bar["Range"])
        if direction == "BUY" and (b_close < b_open) and b_rng >= (atr_5m * 1.65) and price < b_open:
            return False, "Unrecovered Bearish Crash Bar Present"
        if direction == "SELL" and (b_close > b_open) and b_rng >= (atr_5m * 1.65) and price > b_open:
            return False, "Unrecovered Bullish Surge Bar Present"

    return True, "All 7 Negative Vetoes Passed"

def analyze_human_confluence_setups(pair_name, pair_info, df_15m, df_5m, live_spot, ignore_session_and_locks=False):
    now_utc = get_utc_now()
    if not ignore_session_and_locks:
        if now_utc < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
            return None
        session_ok, _ = get_trading_session(pair_info.get("is_crypto", False))
        if not session_ok:
            return None
        news_ok, _ = check_news_window_for_pair(pair_info)
        if not news_ok:
            return None

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
    dec        = pair_info["decimals"]

    if np.isnan(atr_5m) or atr_5m <= 0:
        return None

    m15_c1    = df_15m.iloc[-2]
    m15_close = float(m15_c1["Close"])
    m15_ema9  = float(m15_c1["EMA9"])
    m15_ema20 = float(m15_c1["EMA20"])
    m15_rsi   = float(m15_c1["RSI"])

    htf_bull = ((m15_close >= m15_ema20) or (m15_ema9 >= m15_ema20)) and (m15_rsi <= 67.0)
    htf_bear = ((m15_close <= m15_ema20) or (m15_ema9 <= m15_ema20)) and (m15_rsi >= 33.0)

    m5_sh, m5_sl = extract_fractal_swings(df_5m, left=2, right=2)
    candidates = []

    # SETUP 1: TREND PULLBACK + SELLER/BUYER EXHAUSTION
    win_24 = df_5m.iloc[-26:-2]
    hi_idx = int(np.argmax(win_24["High"].values))
    lo_idx = int(np.argmin(win_24["Low"].values))
    sw_hi  = float(win_24["High"].values[hi_idx])
    sw_lo  = float(win_24["Low"].values[lo_idx])
    sw_rng = sw_hi - sw_lo

    if sw_rng >= (atr_5m * 1.80):
        bars_since_peak = (len(win_24) - 1) - hi_idx
        if lo_idx < hi_idx and 2 <= bars_since_peak <= 12:
            pb_low     = min(low_0, float(c1["Low"]), float(c2["Low"]))
            drop_dist  = sw_hi - pb_low
            retrace_rt = drop_dist / sw_rng
            if drop_dist >= (atr_5m * 0.40) and (0.25 <= retrace_rt <= 0.75) and (pb_low <= ema20 + atr_5m * 0.22) and (pb_low >= ema50 - atr_5m * 0.35):
                bull_reclaim = (price > open_0) and (price > ema9) and (close_pos >= 0.62) and (body_ratio >= 0.44) and (price > float(c1["Close"]))
                if htf_bull and bull_reclaim and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]):
                    sl_raw = pb_low - (atr_5m * SL_BUFFER_ATR)
                    pct    = int(retrace_rt * 100)
                    candidates.append({
                        "setup_type": "EXHAUSTION_PULLBACK",
                        "direction": "BUY",
                        "action": "BUY (LONG) 🟢",
                        "short_tag": f"Exhaustion Pullback ({pct}% Fib)",
                        "struct_id": f"EX_PB_BUY_{win_24.index[hi_idx]}",
                        "sl_raw": sl_raw,
                        "score": 85 + (4 if pb_low <= sw_hi - sw_rng * 0.382 else 0),
                        "why_text": (
                            f"1️⃣ <b>No Top Buying:</b> हाई (<code>{format_price(sw_hi, dec)}</code>) पर खरीदने के बजाय <b>{bars_since_peak} कैंडल्स</b> तक {pct}% पुलबैक का इंतजार किया।\n"
                            f"2️⃣ <b>Seller Exhaustion:</b> EMA 20/50 सपोर्ट ज़ोन पर सेलर्स थक गए और मजबूत हरी कैंडल ने रिक्लेम किया।"
                        )
                    })

        bars_since_low = (len(win_24) - 1) - lo_idx
        if hi_idx < lo_idx and 2 <= bars_since_low <= 12:
            pb_high    = max(high_0, float(c1["High"]), float(c2["High"]))
            pull_dist  = pb_high - sw_lo
            retrace_rt = pull_dist / sw_rng
            if pull_dist >= (atr_5m * 0.40) and (0.25 <= retrace_rt <= 0.75) and (pb_high >= ema20 - atr_5m * 0.22) and (pb_high <= ema50 + atr_5m * 0.35):
                bear_reclaim = (price < open_0) and (price < ema9) and (close_pos <= 0.38) and (body_ratio >= 0.44) and (price < float(c1["Close"]))
                if htf_bear and bear_reclaim and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]):
                    sl_raw = pb_high + (atr_5m * SL_BUFFER_ATR)
                    pct    = int(retrace_rt * 100)
                    candidates.append({
                        "setup_type": "EXHAUSTION_PULLBACK",
                        "direction": "SELL",
                        "action": "SELL (SHORT) 🔴",
                        "short_tag": f"Exhaustion Pullback ({pct}% Fib)",
                        "struct_id": f"EX_PB_SELL_{win_24.index[lo_idx]}",
                        "sl_raw": sl_raw,
                        "score": 85 + (4 if pb_high >= sw_lo + sw_rng * 0.382 else 0),
                        "why_text": (
                            f"1️⃣ <b>No Bottom Selling:</b> बॉटम (<code>{format_price(sw_lo, dec)}</code>) पर बेचने के बजाय <b>{bars_since_low} कैंडल्स</b> तक {pct}% रिट्रेसमेंट का इंतजार किया।\n"
                            f"2️⃣ <b>Buyer Exhaustion:</b> EMA 20/50 सप्लाई ज़ोन पर खरीदारों की ताकत खत्म हुई और सेलर्स ने कंट्रोल ले लिया।"
                        )
                    })

    # SETUP 2: BREAK & RETEST (S/R FLIP)
    for sh in reversed(m5_sh[:-1]):
        lvl = sh["price"]
        bars_ago = (len(df_5m) - 2) - sh["idx"]
        if 4 <= bars_ago <= 26:
            post_bars = df_5m.iloc[sh["idx"] + 1 : -2]
            if len(post_bars) >= 2 and float(post_bars["High"].max()) >= (lvl + atr_5m * 0.65):
                recent_pb_low = min(low_0, float(c1["Low"]), float(c2["Low"]))
                if abs(recent_pb_low - lvl) <= (atr_5m * 0.38) and (price > lvl) and (price > open_0) and (close_pos >= 0.60) and (body_ratio >= 0.42):
                    if htf_bull and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]):
                        sl_raw = min(recent_pb_low, lvl - atr_5m * 0.15) - (atr_5m * SL_BUFFER_ATR)
                        candidates.append({
                            "setup_type": "SR_FLIP_RETEST",
                            "direction": "BUY",
                            "action": "BUY (LONG) 🟢",
                            "short_tag": "S/R Flip Break & Retest",
                            "struct_id": f"SR_BUY_{sh['time']}",
                            "sl_raw": sl_raw,
                            "score": 88,
                            "why_text": (
                                f"1️⃣ <b>Break & Retest:</b> कीमत ने पहले <code>{format_price(lvl, dec)}</code> के रेजिस्टेंस को तोड़ा और वापस नीचे आकर उसे सपोर्ट (फर्श) बनाया।\n"
                                f"2️⃣ <b>Confirmation:</b> सफल रीटेस्ट के बाद मजबूत बाउंस कैंडल क्लोज हुई।"
                            )
                        })
                        break

    for sl_piv in reversed(m5_sl[:-1]):
        lvl = sl_piv["price"]
        bars_ago = (len(df_5m) - 2) - sl_piv["idx"]
        if 4 <= bars_ago <= 26:
            post_bars = df_5m.iloc[sl_piv["idx"] + 1 : -2]
            if len(post_bars) >= 2 and float(post_bars["Low"].min()) <= (lvl - atr_5m * 0.65):
                recent_pb_high = max(high_0, float(c1["High"]), float(c2["High"]))
                if abs(recent_pb_high - lvl) <= (atr_5m * 0.38) and (price < lvl) and (price < open_0) and (close_pos <= 0.40) and (body_ratio >= 0.42):
                    if htf_bear and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]):
                        sl_raw = max(recent_pb_high, lvl + atr_5m * 0.15) + (atr_5m * SL_BUFFER_ATR)
                        candidates.append({
                            "setup_type": "SR_FLIP_RETEST",
                            "direction": "SELL",
                            "action": "SELL (SHORT) 🔴",
                            "short_tag": "S/R Flip Break & Retest",
                            "struct_id": f"SR_SELL_{sl_piv['time']}",
                            "sl_raw": sl_raw,
                            "score": 88,
                            "why_text": (
                                f"1️⃣ <b>Break & Retest:</b> कीमत ने पहले <code>{format_price(lvl, dec)}</code> के सपोर्ट को तोड़ा और वापस ऊपर आकर उसे रेजिस्टेंस (छत) बनाया।\n"
                                f"2️⃣ <b>Confirmation:</b> रीटेस्ट से रिजेक्ट होकर मजबूत बेयरिश कैंडल क्लोज हुई।"
                            )
                        })
                        break

    # SETUP 3: PRE-BREAKOUT PRESSURE BUILD-UP (TIGHT BOX BREAKOUT)
    box_7    = df_5m.iloc[-9:-2]
    box_high = float(box_7["High"].max())
    box_low  = float(box_7["Low"].min())
    box_rng  = box_high - box_low
    box_mid  = (box_high + box_low) / 2.0
    box_id   = f"PRESS_BOX_{box_low}_{box_high}"

    if (atr_5m * 0.65) <= box_rng <= (atr_5m * 2.15):
        held_upper_half = (float(c1["Low"]) >= box_mid - atr_5m * 0.18) and (open_0 >= box_mid - atr_5m * 0.10)
        if htf_bull and held_upper_half and (price > box_high) and (price > open_0) and (body_ratio >= 0.48) and (close_pos >= 0.64) and (0.90 <= disp_power <= 1.75):
            if ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]:
                sl_raw = max(box_low, min(low_0, float(c1["Low"]), ema20)) - (atr_5m * SL_BUFFER_ATR)
                candidates.append({
                    "setup_type": "PRESSURE_BREAKOUT",
                    "direction": "BUY",
                    "action": "BUY (LONG) 🟢",
                    "short_tag": "Box Pressure Breakout",
                    "struct_id": box_id,
                    "sl_raw": sl_raw,
                    "score": 83,
                    "why_text": (
                        f"1️⃣ <b>Tight Box Build-Up:</b> कीमत ने <code>{format_price(box_low, dec)} - {format_price(box_high, dec)}</code> के बॉक्स में रुककर रेजिस्टेंस पर दबाव बनाया।\n"
                        f"2️⃣ <b>Breakout Ignition:</b> बॉक्स के ऊपरी लेवल को ताज़ा मोमेंटम कैंडल ने तोड़ा।"
                    )
                })

        held_lower_half = (float(c1["High"]) <= box_mid + atr_5m * 0.18) and (open_0 <= box_mid + atr_5m * 0.10)
        if htf_bear and held_lower_half and (price < box_low) and (price < open_0) and (body_ratio >= 0.48) and (close_pos <= 0.36) and (0.90 <= disp_power <= 1.75):
            if ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]:
                sl_raw = min(box_high, max(high_0, float(c1["High"]), ema20)) + (atr_5m * SL_BUFFER_ATR)
                candidates.append({
                    "setup_type": "PRESSURE_BREAKOUT",
                    "direction": "SELL",
                    "action": "SELL (SHORT) 🔴",
                    "short_tag": "Box Pressure Breakdown",
                    "struct_id": box_id,
                    "sl_raw": sl_raw,
                    "score": 83,
                    "why_text": (
                        f"1️⃣ <b>Tight Box Build-Up:</b> कीमत ने <code>{format_price(box_low, dec)} - {format_price(box_high, dec)}</code> के बॉक्स में सपोर्ट पर दबाव बनाया।\n"
                        f"2️⃣ <b>Breakdown Ignition:</b> सपोर्ट बॉक्स को मजबूत बेयरिश कैंडल ने नीचे तोड़ा।"
                    )
                })

    # SETUP 4: INSTITUTIONAL LIQUIDITY SWEEP (STOP-HUNT REVERSAL)
    pool_12   = df_5m.iloc[-15:-3]
    pool_high = float(pool_12["High"].max())
    pool_low  = float(pool_12["Low"].min())
    sweep_id  = f"SWEEP_{pool_low}_{pool_high}"

    swept_below = (low_0 < pool_low and price > pool_low) or (float(c1["Low"]) < pool_low and price > float(c1["High"]) and price > pool_low)
    bull_trap   = (price > open_0) and (price > ema9) and (close_pos >= 0.62) and (l_wick >= float(c0["Range"]) * 0.28 or body_ratio >= 0.50)
    if swept_below and bull_trap and (m15_rsi >= 36.0) and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]):
        sweep_low = min(low_0, float(c1["Low"]))
        sl_raw    = sweep_low - (atr_5m * SL_BUFFER_ATR)
        candidates.append({
            "setup_type": "LIQUIDITY_SWEEP",
            "direction": "BUY",
            "action": "BUY (LONG) 🟢",
            "short_tag": "Liquidity Stop-Hunt Reclaim",
            "struct_id": sweep_id,
            "sl_raw": sl_raw,
            "score": 87,
            "why_text": (
                f"1️⃣ <b>Stop-Hunt Trap:</b> ऑपरेटर ने <code>{format_price(pool_low, dec)}</code> के नीचे रिटेल ट्रेडर्स के स्टॉप-लॉस काटे और तुरंत वापस ऊपर क्लोजिंग दी।\n"
                f"2️⃣ <b>Protected SL:</b> स्टॉप-लॉस सीधा उस Stop-Hunt विक (<code>{format_price(sweep_low, dec)}</code>) के नीचे सुरक्षित है।"
            )
        })

    swept_above = (high_0 > pool_high and price < pool_high) or (float(c1["High"]) > pool_high and price < float(c1["Low"]) and price < pool_high)
    bear_trap   = (price < open_0) and (price < ema9) and (close_pos <= 0.38) and (u_wick >= float(c0["Range"]) * 0.28 or body_ratio >= 0.50)
    if swept_above and bear_trap and (m15_rsi <= 64.0) and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]):
        sweep_high = max(high_0, float(c1["High"]))
        sl_raw     = sweep_high + (atr_5m * SL_BUFFER_ATR)
        candidates.append({
            "setup_type": "LIQUIDITY_SWEEP",
            "direction": "SELL",
            "action": "SELL (SHORT) 🔴",
            "short_tag": "Liquidity Stop-Hunt Reject",
            "struct_id": sweep_id,
            "sl_raw": sl_raw,
            "score": 87,
            "why_text": (
                f"1️⃣ <b>Bull-Trap Sweep:</b> ऑपरेटर ने <code>{format_price(pool_high, dec)}</code> के ऊपर नकली ब्रेकआउट दिखाकर तुरंत वापस नीचे क्लोजिंग दी।\n"
                f"2️⃣ <b>Protected SL:</b> स्टॉप-लॉस सीधा उस ट्रैप विक (<code>{format_price(sweep_high, dec)}</code>) के ऊपर सुरक्षित है।"
            )
        })

    # SETUP 5: DOUBLE-BOTTOM (W) / DOUBLE-TOP (M) & HIGHER-LOW IGNITION
    if len(m5_sl) >= 2:
        sl1, sl2 = m5_sl[-2], m5_sl[-1]
        bars_since_sl2 = (len(df_5m) - 2) - sl2["idx"]
        if 1 <= bars_since_sl2 <= 5 and (sl2["idx"] - sl1["idx"]) >= 4:
            is_w_or_hl = (sl2["price"] >= sl1["price"] - atr_5m * 0.22) and (abs(sl2["price"] - sl1["price"]) <= atr_5m * 0.85)
            mid_neckline = float(df_5m.iloc[sl1["idx"]:sl2["idx"] + 1]["High"].max())
            if is_w_or_hl and (price > ema9) and (price > ema20) and (price > open_0) and (close_pos >= 0.62) and (price >= mid_neckline - atr_5m * 0.15):
                if htf_bull and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]):
                    sl_raw = min(sl2["price"], low_0) - (atr_5m * SL_BUFFER_ATR)
                    candidates.append({
                        "setup_type": "DOUBLE_BOTTOM_TOP",
                        "direction": "BUY",
                        "action": "BUY (LONG) 🟢",
                        "short_tag": "W-Bottom / Higher-Low Break",
                        "struct_id": f"W_BOT_{sl2['time']}",
                        "sl_raw": sl_raw,
                        "score": 84,
                        "why_text": (
                            f"1️⃣ <b>W / Higher-Low Base:</b> कीमत ने <code>{format_price(sl2['price'], dec)}</code> पर दूसरा मजबूत बॉटम बनाया (सेलर्स पिछला Low नहीं तोड़ पाए)।\n"
                            f"2️⃣ <b>Neckline Shift:</b> EMA 9/20 और नेकलाइन के ऊपर मजबूत कैंडल ने ट्रेंड को ऊपर मोड़ दिया।"
                        )
                    })

    if len(m5_sh) >= 2:
        sh1, sh2 = m5_sh[-2], m5_sh[-1]
        bars_since_sh2 = (len(df_5m) - 2) - sh2["idx"]
        if 1 <= bars_since_sh2 <= 5 and (sh2["idx"] - sh1["idx"]) >= 4:
            is_m_or_lh = (sh2["price"] <= sh1["price"] + atr_5m * 0.22) and (abs(sh2["price"] - sh1["price"]) <= atr_5m * 0.85)
            mid_neckline = float(df_5m.iloc[sh1["idx"]:sh2["idx"] + 1]["Low"].min())
            if is_m_or_lh and (price < ema9) and (price < ema20) and (price < open_0) and (close_pos <= 0.38) and (price <= mid_neckline + atr_5m * 0.15):
                if htf_bear and (ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]):
                    sl_raw = max(sh2["price"], high_0) + (atr_5m * SL_BUFFER_ATR)
                    candidates.append({
                        "setup_type": "DOUBLE_BOTTOM_TOP",
                        "direction": "SELL",
                        "action": "SELL (SHORT) 🔴",
                        "short_tag": "M-Top / Lower-High Break",
                        "struct_id": f"M_TOP_{sh2['time']}",
                        "sl_raw": sl_raw,
                        "score": 84,
                        "why_text": (
                            f"1️⃣ <b>M / Lower-High Ceiling:</b> कीमत ने <code>{format_price(sh2['price'], dec)}</code> पर Lower-High बनाया (बायर्स पिछला High नहीं तोड़ पाए)।\n"
                            f"2️⃣ <b>Neckline Breakdown:</b> EMA 9/20 के नीचे मजबूत बेयरिश शिफ्ट कन्फर्म हुआ।"
                        )
                    })

    # SETUP 6: M15 + M5 MOMENTUM IGNITION
    ema_cluster = abs(float(c1["EMA9"]) - float(c1["EMA20"])) <= (atr_5m * 0.32)
    if htf_bull and ema_cluster and (ema9 > ema20 >= ema50 * 0.9997) and (price > open_0) and (price > float(c1["High"])) and (body_ratio >= 0.52) and (close_pos >= 0.66) and (0.95 <= disp_power <= 1.70):
        if ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["BUY"]:
            sl_raw = min(low_0, float(c1["Low"]), ema20) - (atr_5m * SL_BUFFER_ATR)
            candidates.append({
                "setup_type": "MOMENTUM_IGNITION",
                "direction": "BUY",
                "action": "BUY (LONG) 🟢",
                "short_tag": "M15+M5 Momentum Ignition",
                "struct_id": f"MOM_IG_BUY_{df_5m.index[-2]}",
                "sl_raw": sl_raw,
                "score": 81,
                "why_text": (
                    f"1️⃣ <b>EMA Compression Expansion:</b> शांत रहने के बाद M15 और M5 दोनों टाइमफ्रेम एक साथ बुलिश दिशा में खुले।\n"
                    f"2️⃣ <b>Fresh Momentum:</b> मजबूत कैंडल ने पिछला High और EMA 9/20 क्लस्टर तोड़ा।"
                )
            })

    if htf_bear and ema_cluster and (ema9 < ema20 <= ema50 * 1.0003) and (price < open_0) and (price < float(c1["Low"])) and (body_ratio >= 0.52) and (close_pos <= 0.34) and (0.95 <= disp_power <= 1.70):
        if ignore_session_and_locks or now_utc >= sl_direction_lock[pair_name]["SELL"]:
            sl_raw = max(high_0, float(c1["High"]), ema20) + (atr_5m * SL_BUFFER_ATR)
            candidates.append({
                "setup_type": "MOMENTUM_IGNITION",
                "direction": "SELL",
                "action": "SELL (SHORT) 🔴",
                "short_tag": "M15+M5 Momentum Ignition",
                "struct_id": f"MOM_IG_SELL_{df_5m.index[-2]}",
                "sl_raw": sl_raw,
                "score": 81,
                "why_text": (
                    f"1️⃣ <b>EMA Compression Expansion:</b> शांत रहने के बाद M15 और M5 दोनों टाइमफ्रेम एक साथ बेयरिश दिशा में खुले।\n"
                    f"2️⃣ <b>Fresh Momentum:</b> मजबूत बेयरिश कैंडल ने पिछला Low और EMA 9/20 क्लस्टर तोड़ा।"
                )
            })

    if not candidates:
        return None

    candidates.sort(key=lambda x: x["score"], reverse=True)

    for cand in candidates:
        c_dir = cand["direction"]
        if not ignore_session_and_locks:
            if last_structure_traded.get(pair_name) == cand["struct_id"]:
                continue
            if not check_smart_usd_correlation_shield(pair_info, c_dir):
                continue
            if not check_dxy_extreme_spike_guard(pair_info, c_dir):
                continue

        veto_ok, veto_reason = human_negative_aspect_veto_audit(
            df_15m, df_5m, c_dir, live_spot, cand["setup_type"], atr_5m
        )
        if not veto_ok:
            continue

        if cand["score"] >= MIN_HUMAN_SCORE_REQUIRED:
            return cand

    return None

# =====================================================================
# 5. DYNAMIC REAL-TIME CHART DIAGNOSIS ENGINE
# =====================================================================
def diagnose_live_market_state(pair_name, info, df_15m, df_5m, live_spot):
    dec    = info["decimals"]
    c0     = df_5m.iloc[-2]
    c1     = df_5m.iloc[-3]
    atr_5m = float(c0["ATR"])
    ema9   = float(c0["EMA9"])
    ema20  = float(c0["EMA20"])
    ema50  = float(c0["EMA50"])
    rsi    = float(c0["RSI"])
    body_r = float(c0["Body_Ratio"])

    m15_c1    = df_15m.iloc[-2]
    m15_close = float(m15_c1["Close"])
    m15_ema20 = float(m15_c1["EMA20"])
    m15_rsi   = float(m15_c1["RSI"])

    win_30   = df_5m.iloc[-32:-1]
    res_wall = float(win_30["High"].max())
    sup_wall = float(win_30["Low"].min())
    rng_30   = max(res_wall - sup_wall, 1e-6)
    pos_30   = (live_spot - sup_wall) / rng_30

    box_8    = df_5m.iloc[-9:-1]
    box_hi   = float(box_8["High"].max())
    box_lo   = float(box_8["Low"].min())
    box_span = box_hi - box_lo

    active_cand = analyze_human_confluence_setups(pair_name, info, df_15m, df_5m, live_spot, ignore_session_and_locks=True)
    if active_cand is not None:
        return {
            "is_live_signal": True,
            "score": active_cand["score"],
            "direction": active_cand["direction"],
            "short_tag": active_cand["short_tag"],
            "state_headline": f"✅ <b>A+ Trade Setup Ready ({active_cand['short_tag']})</b>",
            "detail_line": active_cand["why_text"],
            "trigger_line_price": live_spot,
            "res_wall": res_wall,
            "sup_wall": sup_wall,
            "custom_sl": active_cand["sl_raw"],
            "setup_type": active_cand["setup_type"]
        }

    m15_bull = m15_close >= m15_ema20
    m5_bull  = ema9 >= ema20
    direction = "BUY" if m5_bull else "SELL"
    if m15_bull == m5_bull:
        score_htf = 16 if abs(ema20 - ema50) > (atr_5m * 0.25) else 12
    else:
        score_htf = 6

    dist_to_sup = abs(live_spot - sup_wall) / max(atr_5m, 1e-6)
    dist_to_res = abs(res_wall - live_spot) / max(atr_5m, 1e-6)
    dist_to_ema = abs(live_spot - ema20) / max(atr_5m, 1e-6)

    if min(dist_to_sup, dist_to_res) <= 0.65:
        score_loc = 18
    elif dist_to_ema <= 0.45:
        score_loc = 15
    elif 0.38 <= pos_30 <= 0.62:
        score_loc = 7
    else:
        score_loc = 11

    recent_body_avg = float(box_8["Body_Ratio"].mean())
    score_candle = int(min(15.0, max(3.0, recent_body_avg * 22.0 + (4.0 if body_r > 0.50 else 0.0))))

    if dist_to_ema > 1.45 or rsi > 68.0 or rsi < 32.0:
        score_mom = 3
    elif 42.0 <= rsi <= 58.0:
        score_mom = 11
    else:
        score_mom = 8

    room_atr = (res_wall - live_spot) / max(atr_5m, 1e-6) if direction == "BUY" else (live_spot - sup_wall) / max(atr_5m, 1e-6)
    score_room = 10 if room_atr >= 1.8 else (6 if room_atr >= 1.0 else 2)

    dynamic_score = int(max(18, min(75, score_htf + score_loc + score_candle + score_mom + score_room)))

    if box_span <= (atr_5m * 1.55):
        short_tag   = "Tight Box Consolidation"
        trig_p      = box_hi if direction == "BUY" else box_lo
        headline    = f"📦 <b>Range Box Consolidation (Score: {dynamic_score}/100)</b>"
        detail_line = (
            f"कीमत अभी <code>{format_price(box_lo, dec)} – {format_price(box_hi, dec)}</code> के छोटे बॉक्स में बंद है। "
            f"ट्रेड तभी बनेगी जब कैंडल <code>{format_price(box_hi, dec)}</code> के ऊपर या <code>{format_price(box_lo, dec)}</code> के नीचे मजबूत ब्रेकआउट दे।"
        )
    elif dist_to_ema > 1.35 or (direction == "BUY" and pos_30 > 0.85) or (direction == "SELL" and pos_30 < 0.15):
        short_tag   = "Overextended (Anti-FOMO Wait)"
        trig_p      = ema20
        headline    = f"🚀 <b>Overextended Move — Top/Bottom Chasing Blocked ({dynamic_score}/100)</b>"
        detail_line = (
            f"कीमत अभी अपने वैल्यू ज़ोन (<code>{format_price(ema20, dec)}</code>) से काफी दूर निकल चुकी है (RSI: {rsi:.0f})। "
            f"अभी कूदने पर SL हिट होने का खतरा है; कीमत के <code>{format_price(ema20, dec)}</code> या स्ट्रक्चर लेवल के पास शांत होने का इंतजार है।"
        )
    elif dist_to_sup <= 0.65:
        short_tag   = "Testing Support Floor"
        trig_p      = float(c0["High"])
        headline    = f"🧲 <b>Testing Major Support Floor ({dynamic_score}/100)</b>"
        detail_line = (
            f"कीमत मुख्य सपोर्ट (<code>{format_price(sup_wall, dec)}</code>) की परीक्षा ले रही है। "
            f"यहाँ से BUY या तो <code>{format_price(trig_p, dec)}</code> के ऊपर बुलिश इंगल्फिंग क्लोज होने पर बनेगा, या सपोर्ट टूटने पर SELL बनेगा।"
        )
    elif dist_to_res <= 0.65:
        short_tag   = "Testing Resistance Wall"
        trig_p      = float(c0["Low"])
        headline    = f"🧱 <b>Testing Major Resistance Wall ({dynamic_score}/100)</b>"
        detail_line = (
            f"कीमत ठीक रेजिस्टेंस की दीवार (<code>{format_price(res_wall, dec)}</code>) के पास खड़ी है। "
            f"दीवार के पास बिना ब्रेकआउट BUY करना खतरनाक है; <code>{format_price(trig_p, dec)}</code> टूटने पर रिजेक्शन SELL या दीवार के ऊपर रीटेस्ट का इंतजार है।"
        )
    elif m15_bull != m5_bull:
        short_tag   = "M15 vs M5 Timeframe Conflict"
        trig_p      = m15_ema20
        headline    = f"⚖️ <b>Timeframe Conflict — M15 & M5 Opposite ({dynamic_score}/100)</b>"
        detail_line = (
            f"15-मिनट का चार्ट ({'Bullish' if m15_bull else 'Bearish'}) और 5-मिनट का चार्ट ({'Bullish' if m5_bull else 'Bearish'}) एक-दूसरे के खिलाफ चल रहे हैं। "
            f"दोनों टाइमफ्रेम के <code>{format_price(trig_p, dec)}</code> के पार एक दिशा में आने का इंतजार है।"
        )
    elif 0.36 <= pos_30 <= 0.64 and dist_to_ema > 0.35:
        short_tag   = "Mid-Range No-Trade Zone"
        trig_p      = ema20
        headline    = f"📉 <b>Mid-Range Indecision Zone ({dynamic_score}/100)</b>"
        detail_line = (
            f"कीमत सपोर्ट (<code>{format_price(sup_wall, dec)}</code>) और रेजिस्टेंस (<code>{format_price(res_wall, dec)}</code>) के बिल्कुल बीच हवा में है। "
            f"यहाँ रिस्क-रिवॉर्ड खराब है; कीमत के किसी मुख्य लेवल या <code>{format_price(ema20, dec)}</code> पर आने का इंतजार है।"
        )
    else:
        short_tag   = "Pullback / Structure Forming"
        trig_p      = float(c0["High"]) if direction == "BUY" else float(c0["Low"])
        headline    = f"⏳ <b>Structure Forming Near Value Zone ({dynamic_score}/100)</b>"
        detail_line = (
            f"कीमत मुख्य ज़ोन (<code>{format_price(ema20, dec)}</code>) के पास सेटअप बना रही है, लेकिन अभी कन्फर्मेशन कैंडल अधूरी है। "
            f"मजबूत कैंडल के <code>{format_price(trig_p, dec)}</code> के पार क्लोज होने का इंतजार है।"
        )

    custom_sl = (min(float(box_8["Low"].min()), ema20) - atr_5m * SL_BUFFER_ATR) if direction == "BUY" else (max(float(box_8["High"].max()), ema20) + atr_5m * SL_BUFFER_ATR)
    return {
        "is_live_signal": False,
        "score": dynamic_score,
        "direction": direction,
        "short_tag": short_tag,
        "state_headline": headline,
        "detail_line": detail_line,
        "trigger_line_price": trig_p,
        "res_wall": res_wall,
        "sup_wall": sup_wall,
        "custom_sl": custom_sl,
        "setup_type": "WATCH"
    }

def calculate_smart_sl_and_easy_tp(df_5m, df_15m, info, direction, live_spot, raw_structure_sl, atr_5m, setup_type="PULLBACK", allow_wall_block=True):
    dec = info["decimals"]
    raw_sl_dist = abs(live_spot - raw_structure_sl)
    min_sl_cap  = info.get("min_sl_dist", atr_5m * 0.85)
    max_sl_cap  = info.get("max_sl_dist", atr_5m * 1.85)

    if allow_wall_block and raw_sl_dist > (max_sl_cap * 1.30):
        return None, 0, None, None, None, 0, 0

    sl_dist = round(max(min_sl_cap, min(raw_sl_dist, max_sl_cap)), dec)

    recent_m5       = df_5m.iloc[-36:-2]
    major_high_wall = float(recent_m5["High"].max())
    major_low_wall  = float(recent_m5["Low"].min())

    default_tp2_dist = round(sl_dist * BASE_EASY_RR_RATIO, dec)
    default_tp1_dist = round(default_tp2_dist * NEAR_TARGET_ALERT_RATIO, dec)

    if direction == "BUY":
        room_to_wall = major_high_wall - live_spot
        if allow_wall_block and setup_type not in ["PRESSURE_BREAKOUT", "MOMENTUM_IGNITION"] and (atr_5m * 0.15) < room_to_wall < (sl_dist * 0.90):
            return None, 0, None, None, None, 0, 0
        if setup_type not in ["PRESSURE_BREAKOUT", "MOMENTUM_IGNITION"] and (sl_dist * 0.90) <= room_to_wall <= (sl_dist * 1.60):
            tp1_dist = round(room_to_wall * 0.85, dec)
            tp2_dist = round(max(room_to_wall * 1.05, sl_dist * 1.30), dec)
        else:
            tp1_dist, tp2_dist = default_tp1_dist, default_tp2_dist

        actual_rr    = round(tp2_dist / max(sl_dist, 1e-9), 2)
        sl_level     = round(live_spot - sl_dist, dec)
        lock_level   = round(live_spot + (sl_dist * LOCK_PROFIT_R), dec)
        near_tp_lvl  = round(live_spot + tp1_dist, dec)
        target_level = round(live_spot + tp2_dist, dec)
    else:
        room_to_wall = live_spot - major_low_wall
        if allow_wall_block and setup_type not in ["PRESSURE_BREAKOUT", "MOMENTUM_IGNITION"] and (atr_5m * 0.15) < room_to_wall < (sl_dist * 0.90):
            return None, 0, None, None, None, 0, 0
        if setup_type not in ["PRESSURE_BREAKOUT", "MOMENTUM_IGNITION"] and (sl_dist * 0.90) <= room_to_wall <= (sl_dist * 1.60):
            tp1_dist = round(room_to_wall * 0.85, dec)
            tp2_dist = round(max(room_to_wall * 1.05, sl_dist * 1.30), dec)
        else:
            tp1_dist, tp2_dist = default_tp1_dist, default_tp2_dist

        actual_rr    = round(tp2_dist / max(sl_dist, 1e-9), 2)
        sl_level     = round(live_spot + sl_dist, dec)
        lock_level   = round(live_spot - (sl_dist * LOCK_PROFIT_R), dec)
        near_tp_lvl  = round(live_spot - tp1_dist, dec)
        target_level = round(live_spot - tp2_dist, dec)

    return sl_level, sl_dist, lock_level, near_tp_lvl, target_level, tp2_dist, actual_rr

# =====================================================================
# 6. EXPERIENCED TRADER TRADINGVIEW PRO CHART ENGINE (FAST & REALISTIC!)
# =====================================================================
def generate_mt5_mobile_style_chart(
    df_5m, info, direction, entry_p, sl_p, tp_p, near_tp_p,
    short_tag, lot_size, human_score=85, is_live_signal=True, trigger_watch_price=None
):
    try:
        sub = df_5m.iloc[-62:].copy()
        if sub.empty or len(sub) < 25:
            return None

        dec     = info["decimals"]
        mt5_sym = info["mt5_name"]
        sub_ttl = info.get("sub_title", "Spot vs US Dollar")
        atr_val = float(sub.iloc[-2]["ATR"]) if "ATR" in sub.columns else float((sub["High"] - sub["Low"]).mean())

        fig = plt.figure(figsize=(10.6, 6.2), dpi=125, facecolor="#ffffff")
        ax  = fig.add_axes([0.015, 0.08, 0.875, 0.83], facecolor="#ffffff")

        n_bars = len(sub)
        x_vals = np.arange(n_bars)
        proj_start_idx = n_bars - 2
        right_edge_idx = n_bars + 11

        ax.grid(True, which="major", linestyle="-", linewidth=0.55, color="#f0f3fa", zorder=1)
        for spine in ["top", "left"]:
            ax.spines[spine].set_visible(False)
        ax.spines["right"].set_color("#d1d4dc")
        ax.spines["bottom"].set_color("#d1d4dc")

        ax.text(
            0.46, 0.52, f"{mt5_sym}, 5m", transform=ax.transAxes,
            fontsize=38, fontweight="bold", color="#131722", alpha=0.04,
            ha="center", va="center", zorder=1
        )
        ax.text(
            0.46, 0.41, f"{sub_ttl}", transform=ax.transAxes,
            fontsize=15, fontweight="bold", color="#131722", alpha=0.04,
            ha="center", va="center", zorder=1
        )

        win_hist   = sub.iloc[-42:-2]
        res_hi_idx = int(np.argmax(win_hist["High"].values)) + (n_bars - 42)
        sup_lo_idx = int(np.argmin(win_hist["Low"].values)) + (n_bars - 42)
        res_line   = float(win_hist["High"].max())
        sup_line   = float(win_hist["Low"].min())
        zone_thick = max(atr_val * 0.22, (res_line - sup_line) * 0.035)

        supply_bot = res_line - zone_thick
        ax.add_patch(mpatches.Rectangle(
            (max(0, res_hi_idx - 1), supply_bot),
            right_edge_idx - max(0, res_hi_idx - 1),
            zone_thick,
            facecolor="#f23645", alpha=0.11, edgecolor="none", zorder=2
        ))
        ax.plot([max(0, res_hi_idx - 1), right_edge_idx], [res_line, res_line], color="#f23645", linewidth=1.1, alpha=0.80, zorder=3)
        ax.plot([max(0, res_hi_idx - 1), right_edge_idx], [supply_bot, supply_bot], color="#f23645", linewidth=0.7, linestyle="--", alpha=0.55, zorder=3)
        ax.text(
            right_edge_idx - 0.4, res_line - (zone_thick * 0.5),
            f"Supply / Resistance Zone ({format_price(res_line, dec)})",
            fontsize=7.5, fontweight="bold", color="#b91c1c", ha="right", va="center", zorder=6
        )

        demand_top = sup_line + zone_thick
        ax.add_patch(mpatches.Rectangle(
            (max(0, sup_lo_idx - 1), sup_line),
            right_edge_idx - max(0, sup_lo_idx - 1),
            zone_thick,
            facecolor="#089981", alpha=0.11, edgecolor="none", zorder=2
        ))
        ax.plot([max(0, sup_lo_idx - 1), right_edge_idx], [sup_line, sup_line], color="#089981", linewidth=1.1, alpha=0.80, zorder=3)
        ax.plot([max(0, sup_lo_idx - 1), right_edge_idx], [demand_top, demand_top], color="#089981", linewidth=0.7, linestyle="--", alpha=0.55, zorder=3)
        ax.text(
            right_edge_idx - 0.4, sup_line + (zone_thick * 0.5),
            f"Demand / Support Zone ({format_price(sup_line, dec)})",
            fontsize=7.5, fontweight="bold", color="#047857", ha="right", va="center", zorder=6
        )

        if "EMA20" in sub.columns and "EMA50" in sub.columns:
            ax.plot(x_vals, sub["EMA20"].values, color="#2962ff", linewidth=1.25, alpha=0.88, zorder=3)
            ax.plot(x_vals, sub["EMA50"].values, color="#ff9800", linewidth=1.15, linestyle="--", alpha=0.82, zorder=3)

        opens  = sub["Open"].values.astype(float)
        highs  = sub["High"].values.astype(float)
        lows   = sub["Low"].values.astype(float)
        closes = sub["Close"].values.astype(float)

        if is_live_signal:
            all_y = list(highs) + list(lows) + [float(tp_p), float(sl_p), float(entry_p), float(near_tp_p), res_line, sup_line]
        else:
            all_y = list(highs) + list(lows) + [res_line, sup_line, float(entry_p)]
            if trigger_watch_price is not None:
                all_y.append(float(trigger_watch_price))

        y_min, y_max = float(np.nanmin(all_y)), float(np.nanmax(all_y))
        total_span   = max(y_max - y_min, 1e-6)
        min_body_h   = total_span * 0.0038

        bull_mask    = closes >= opens
        bear_mask    = ~bull_mask
        bot_bodies   = np.minimum(opens, closes)
        body_heights = np.maximum(np.abs(closes - opens), min_body_h)

        ax.vlines(x_vals[bull_mask], lows[bull_mask], highs[bull_mask], color="#089981", linewidth=1.05, zorder=4)
        ax.vlines(x_vals[bear_mask], lows[bear_mask], highs[bear_mask], color="#f23645", linewidth=1.05, zorder=4)
        ax.bar(x_vals[bull_mask], body_heights[bull_mask], bottom=bot_bodies[bull_mask], width=0.68, color="#089981", edgecolor="#089981", linewidth=0.4, zorder=4)
        ax.bar(x_vals[bear_mask], body_heights[bear_mask], bottom=bot_bodies[bear_mask], width=0.68, color="#f23645", edgecolor="#f23645", linewidth=0.4, zorder=4)

        sh_list, sl_list = extract_fractal_swings(sub, left=3, right=3)
        prev_sh_val = None
        for sh_item in sh_list[-3:]:
            lbl = "HH" if (prev_sh_val is None or sh_item["price"] >= prev_sh_val) else "LH"
            prev_sh_val = sh_item["price"]
            ax.text(
                sh_item["idx"], sh_item["price"] + (total_span * 0.015), lbl,
                fontsize=7.2, fontweight="bold", color="#475569", ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.14", facecolor="#f8fafc", edgecolor="#cbd5e1", linewidth=0.6),
                zorder=6
            )

        prev_sl_val = None
        for sl_item in sl_list[-3:]:
            lbl = "HL" if (prev_sl_val is None or sl_item["price"] >= prev_sl_val) else "LL"
            prev_sl_val = sl_item["price"]
            ax.text(
                sl_item["idx"], sl_item["price"] - (total_span * 0.015), lbl,
                fontsize=7.2, fontweight="bold", color="#475569", ha="center", va="top",
                bbox=dict(boxstyle="round,pad=0.14", facecolor="#f8fafc", edgecolor="#cbd5e1", linewidth=0.6),
                zorder=6
            )

        proj_width = right_edge_idx - proj_start_idx - 0.5
        entry_s    = format_price(entry_p, dec)
        res_s      = format_price(res_line, dec)
        sup_s      = format_price(sup_line, dec)

        ax.axhline(entry_p, color="#2962ff", linewidth=0.85, linestyle=":", alpha=0.85, zorder=5)

        if is_live_signal:
            sl_s      = format_price(sl_p, dec)
            near_tp_s = format_price(near_tp_p, dec)
            tp_s      = format_price(tp_p, dec)
            rr_val    = abs(tp_p - entry_p) / max(abs(entry_p - sl_p), 1e-9)

            ax.add_patch(mpatches.Rectangle(
                (proj_start_idx, min(entry_p, sl_p)), proj_width, abs(entry_p - sl_p),
                facecolor="#f23645", alpha=0.18, edgecolor="#f23645", linewidth=1.0, zorder=5
            ))
            ax.add_patch(mpatches.Rectangle(
                (proj_start_idx, min(entry_p, tp_p)), proj_width, abs(tp_p - entry_p),
                facecolor="#089981", alpha=0.18, edgecolor="#089981", linewidth=1.0, zorder=5
            ))
            ax.plot([proj_start_idx, proj_start_idx + proj_width], [entry_p, entry_p], color="#475569", linewidth=1.4, zorder=6)
            ax.plot([proj_start_idx, proj_start_idx + proj_width], [near_tp_p, near_tp_p], color="#089981", linestyle="--", linewidth=1.1, zorder=6)
            ax.plot([proj_start_idx, proj_start_idx + proj_width], [tp_p, tp_p], color="#089981", linewidth=1.5, zorder=6)
            ax.plot([proj_start_idx, proj_start_idx + proj_width], [sl_p, sl_p], color="#f23645", linewidth=1.5, zorder=6)

            mid_box_x = proj_start_idx + (proj_width * 0.5)
            ax.text(
                mid_box_x, entry_p,
                f" {direction} | RR 1:{rr_val:.2f} ({lot_size:.2f} Lot) ",
                fontsize=7.5, fontweight="bold", color="#ffffff", ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.20", facecolor="#1e293b", edgecolor="#334155", alpha=0.92),
                clip_on=True, zorder=8
            )
            ax.text(
                mid_box_x, near_tp_p, " TP1 (50% Book) ",
                fontsize=6.8, fontweight="bold", color="#065f46", ha="center", va="bottom",
                clip_on=True, zorder=7
            )

            right_tags = [
                (tp_p, f" TP2 {tp_s} ", "#089981"),
                (near_tp_p, f" TP1 {near_tp_s} ", "#059669"),
                (entry_p, f" {entry_s} ", "#2962ff"),
                (sl_p, f" SL {sl_s} ", "#f23645")
            ]
        else:
            right_tags = [
                (res_line, f" {res_s} ", "#f23645"),
                (sup_line, f" {sup_s} ", "#089981"),
                (entry_p, f" {entry_s} ", "#2962ff")
            ]
            if trigger_watch_price is not None and abs(trigger_watch_price - entry_p) > (total_span * 0.02):
                trig_s = format_price(trigger_watch_price, dec)
                ax.plot([n_bars - 14, right_edge_idx], [trigger_watch_price, trigger_watch_price], color="#6366f1", linestyle="--", linewidth=1.15, zorder=6)
                ax.text(
                    right_edge_idx - 0.4, trigger_watch_price,
                    f"Key Trigger Level ({trig_s}) ",
                    fontsize=7.4, fontweight="bold", color="#4338ca", ha="right", va="bottom",
                    clip_on=True, zorder=7
                )
                right_tags.append((trigger_watch_price, f" {trig_s} ", "#6366f1"))

        y_pad = total_span * 0.14
        ax.set_ylim(y_min - y_pad, y_max + y_pad)
        ax.set_xlim(-0.8, right_edge_idx)
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", labelsize=8.8, colors="#434651")
        ax.tick_params(axis="x", labelsize=8.5, colors="#434651")

        for tag_y, tag_txt, tag_bg in right_tags:
            ax.text(
                right_edge_idx + 0.15, tag_y, tag_txt,
                fontsize=7.8, fontweight="bold", color="#ffffff", va="center", ha="left",
                bbox=dict(boxstyle="square,pad=0.18", facecolor=tag_bg, edgecolor="none"),
                clip_on=False, zorder=9
            )

        tick_indices = [4, int(n_bars * 0.35), int(n_bars * 0.68), n_bars - 2]
        ax.set_xticks(tick_indices)
        ax.set_xticklabels(
            [(to_utc_timestamp(sub.index[ti]) + timedelta(hours=5, minutes=30)).strftime("%d %b %H:%M") for ti in tick_indices],
            fontsize=8.5, color="#434651"
        )

        last_bar = sub.iloc[-1]
        o_s = format_price(float(last_bar["Open"]), dec)
        h_s = format_price(float(last_bar["High"]), dec)
        l_s = format_price(float(last_bar["Low"]), dec)
        c_s = format_price(float(last_bar["Close"]), dec)
        ema20_s = format_price(float(last_bar["EMA20"]), dec)
        ema50_s = format_price(float(last_bar["EMA50"]), dec)

        top_ax = fig.add_axes([0.015, 0.915, 0.96, 0.075], facecolor="#ffffff")
        top_ax.axis("off")
        top_ax.text(
            0.0, 0.68,
            f"{mt5_sym} · 5m · Live Spot   |   O {o_s}   H {h_s}   L {l_s}   C {c_s}",
            fontsize=10.0, fontweight="bold", color="#131722", va="center", ha="left"
        )
        top_ax.text(
            0.0, 0.20,
            f"EMA 20: {ema20_s} (Blue)   •   EMA 50: {ema50_s} (Orange)   •   Structure: {short_tag} ({human_score}/100)",
            fontsize=8.5, fontweight="bold", color="#475569", va="center", ha="left"
        )
        top_ax.text(
            0.99, 0.68,
            f"{get_ist_now().strftime('%d %b %Y • %I:%M:%S %p IST')}",
            fontsize=8.8, fontweight="bold", color="#64748b", va="center", ha="right"
        )

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=125, facecolor="#ffffff")
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
                files = {"photo": ("tv_pro_chart.png", photo_bytes, "image/png")}
                data  = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                res   = HTTP_SESSION.post(url, data=data, files=files, timeout=15)
                if res.status_code == 200 and res.json().get("ok"):
                    return
            files = {"photo": ("tv_pro_chart.png", photo_bytes, "image/png")}
            data  = {"chat_id": target_chat_id, "caption": "📊 <b>Live Price-Action Chart</b>", "parse_mode": "HTML"}
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

    info = FOREX_PAIRS.get(pair_key, FOREX_PAIRS["XAU/USD (GOLD)"])
    # Reuses closed historical bars for speed, but ALWAYS pulls a 100% fresh real-time tick right now!
    df_15m, df_5m, live_spot, src = get_clean_spot_data(pair_key, info, use_fast_cache=True)
    if df_5m is None or live_spot is None:
        send_telegram_message("⚠️ डेटा लोड हो रहा है, कृपया कुछ सेकंड बाद प्रयास करें।")
        return

    df_5m  = calculate_chart_indicators(df_5m)
    df_15m = calculate_chart_indicators(df_15m)
    atr_5m = float(df_5m.iloc[-2]["ATR"])
    dec    = info["decimals"]

    diag = diagnose_live_market_state(pair_key, info, df_15m, df_5m, live_spot)
    score          = diag["score"]
    direction      = diag["direction"]
    short_tag      = diag["short_tag"]
    is_live_sig    = diag["is_live_signal"]
    trig_watch_lvl = diag["trigger_line_price"]

    sl_level, sl_dist, lock_level, near_tp_lvl, target_level, tp_dist, actual_rr = calculate_smart_sl_and_easy_tp(
        df_5m, df_15m, info, direction, live_spot, diag["custom_sl"], atr_5m, setup_type=diag["setup_type"], allow_wall_block=False
    )
    lot_size = calculate_exact_mt5_lot(pair_key, info, sl_dist, BASE_RISK_PER_TRADE_USD)

    mkt_open_banner = ""
    if not info.get("is_crypto", False) and not is_forex_market_open():
        mkt_open_banner = "⏸️ <b>FOREX WEEKEND CLOSED (Showing Friday Close)</b>\n"

    chart_buf = generate_mt5_mobile_style_chart(
        df_5m, info, direction, live_spot, sl_level, target_level, near_tp_lvl,
        short_tag, lot_size, human_score=score, is_live_signal=is_live_sig, trigger_watch_price=trig_watch_lvl
    )

    if is_live_sig:
        caption = (
            f"{mkt_open_banner}"
            f"📊 <b>{pair_key} ({info['mt5_name']}) — LIVE M5 AUDIT</b>\n"
            f"{diag['state_headline']} (<b>Score: {score}/100</b>)\n\n"
            f"🔍 <b>Analysis:</b>\n{diag['detail_line']}\n\n"
            f"📍 <b>Live Spot:</b> <code>{format_price(live_spot, dec)}</code> | 💼 <b>Lot:</b> <code>{lot_size:.2f}</code>\n"
            f"🛑 <b>SL:</b> <code>{format_price(sl_level, dec)}</code> | 🎯 <b>TP1:</b> <code>{format_price(near_tp_lvl, dec)}</code> | <b>TP2:</b> <code>{format_price(target_level, dec)}</code>"
        )
    else:
        caption = (
            f"{mkt_open_banner}"
            f"📊 <b>{pair_key} ({info['mt5_name']}) — LIVE M5 AUDIT</b>\n"
            f"{diag['state_headline']}\n\n"
            f"🧠 <b>अभी ट्रेड क्यों नहीं है:</b>\n{diag['detail_line']}\n\n"
            f"📍 <b>Live Spot:</b> <code>{format_price(live_spot, dec)}</code>\n"
            f"🧱 <b>Resistance Wall:</b> <code>{format_price(diag['res_wall'], dec)}</code> | 🧲 <b>Support Floor:</b> <code>{format_price(diag['sup_wall'], dec)}</code>\n"
            f"🎯 <b>Next Trigger Level:</b> <code>{format_price(trig_watch_lvl, dec)}</code> (Need Score 78+ for Entry)"
        )
    send_telegram_signal_with_chart(caption, chart_buf)

def send_live_status_summary():
    bal, eq, day_pnl = get_live_account_metrics()
    fx_status = "🟢 OPEN" if is_forex_market_open() else "⏸️ WEEKEND CLOSED (BTC 24/7 Active)"
    report = (
        f"🧠 <b>HUMAN BRAIN V4 LIVE MARKET AUDIT</b> 🧠\n\n"
        f"🌍 <b>Forex Market:</b> {fx_status}\n"
        f"💰 <b>Tracked Equity:</b> <b>${eq:.2f}</b> | 📊 <b>Today's PnL:</b> <b>${day_pnl:+.2f}</b>\n"
        f"🔢 <b>Signals Today:</b> {trades_today}/{MAX_DAILY_TRADES_CAP} | 🎯 <b>To $5,400:</b> <b>${max(0.0, TARGET_EQUITY_USD - eq):.2f}</b>\n\n"
    )
    for name, info in FOREX_PAIRS.items():
        try:
            df_15m, df_5m, live_spot, _ = get_clean_spot_data(name, info, use_fast_cache=True)
            if df_5m is not None and df_15m is not None:
                df_5m  = calculate_chart_indicators(df_5m)
                df_15m = calculate_chart_indicators(df_15m)
                diag   = diagnose_live_market_state(name, info, df_15m, df_5m, live_spot)
                report += (
                    f"🔹 <b>{info['mt5_name']}:</b> <code>{format_price(live_spot, info['decimals'])}</code> "
                    f"| <b>{diag['score']}/100</b> ({diag['short_tag']})\n"
                )
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
# 7. MANUAL TRADER 4-STAGE STEP-BY-STEP TELEGRAM TRADE ASSISTANT
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

            entry_str  = format_price(trade["entry"], decimals)
            target_str = format_price(trade["target"], decimals)
            sl_str     = format_price(trade["sl"], decimals)
            price_str  = format_price(live_spot, decimals)

            # STAGE 1: 0.55R SAFE BREAKEVEN LOCK INSTRUCTION FOR MANUAL TRADER
            if not trade["trailed_to_cost"]:
                half_reached = (trade["direction"] == "BUY" and current_high >= trade["half_target"]) or \
                               (trade["direction"] == "SELL" and current_low <= trade["half_target"])
                if half_reached:
                    trade["trailed_to_cost"] = True
                    trade["sl"] = trade["entry"]
                    send_telegram_message(
                        f"🛡️ <b>STEP 1: MOVE SL TO ENTRY (BREAKEVEN) — {trade['mt5_name']}</b> 🛡️\n\n"
                        f"📍 <b>Live Price:</b> <code>{price_str}</code> (+0.55R Reached)\n"
                        f"👉 <b>अब MT5 में क्या करें:</b> अपने <b>{trade['mt5_name']}</b> ट्रेड का Stop-Loss (SL) बदलकर अपने <b>Entry Price (<code>{entry_str}</code>)</b> पर रख दें।\n"
                        f"✅ अब इस ट्रेड में आपका नुकसान का डर <b>0%</b> हो गया है!"
                    )

            # STAGE 2: TARGET 1 (78%) -> BOOK 50% PROFIT INSTRUCTION
            if not trade.get("near_tp_alerted", False):
                near_hit = (trade["direction"] == "BUY" and current_high >= trade["near_target"] and current_high < trade["target"]) or \
                           (trade["direction"] == "SELL" and current_low <= trade["near_target"] and current_low > trade["target"])
                if near_hit:
                    trade["near_tp_alerted"] = True
                    trade["trailed_to_cost"] = True
                    trade["sl"]              = trade["half_target"]
                    lock_s = format_price(trade["half_target"], decimals)
                    send_telegram_message(
                        f"💰 <b>STEP 2: TARGET 1 HIT — 50% प्रॉफिट बुक करें ({trade['mt5_name']})!</b> 💰\n\n"
                        f"📍 <b>Live Price:</b> <code>{price_str}</code>\n"
                        f"👉 <b>अब MT5 में क्या करें:</b> अपना <b>आधा लॉट (50%) क्लोज</b> करके मुनाफा जेब में डाल लें, और बचे हुए लॉट का SL आगे बढ़ाकर <code>{lock_s}</code> पर लॉक कर दें!"
                    )

            # STAGE 3: HUMAN EMERGENCY EXIT (OPPOSITE HEAVY ENGULFING OR 50-MIN TIME STOP)
            mins_open = (now_utc - trade["entry_candle_time"]).total_seconds() / 60.0
            c_open, c_close, c_disp = float(closed_5m["Open"]), float(closed_5m["Close"]), float(closed_5m["Disp_Power"])
            p_open, p_close = float(prev_5m["Open"]), float(prev_5m["Close"])

            opposite_danger = False
            if trade["direction"] == "BUY" and (c_close < c_open) and (c_disp >= 1.60) and (c_close < min(p_open, p_close)) and (c_close < float(closed_5m["EMA20"])):
                opposite_danger = True
            elif trade["direction"] == "SELL" and (c_close > c_open) and (c_disp >= 1.60) and (c_close > max(p_open, p_close)) and (c_close > float(closed_5m["EMA20"])):
                opposite_danger = True

            if opposite_danger and not trade["trailed_to_cost"]:
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=25)
                saved_loss = round(trade["max_risk"] * 0.45, 1)
                daily_realized_pnl -= saved_loss
                total_account_pnl  -= saved_loss
                send_telegram_message(
                    f"⚠️ <b>EMERGENCY MANUAL EXIT — {trade['mt5_name']} को अभी क्लोज करें!</b> ⚠️\n\n"
                    f"📍 <b>Live Price:</b> <code>{price_str}</code>\n"
                    f"👉 <b>क्यों:</b> मार्केट ने अचानक हमारे खिलाफ भारी उल्टी कैंडल (Opposite Engulfing) बना दी है। पूरे SL का इंतजार मत कीजिए, अभी MT5 खोलकर यह ट्रेड बंद कर दें ताकि पैसा बच जाए!"
                )
                continue

            if mins_open >= MAX_TRADE_HOLD_MINUTES and not trade["trailed_to_cost"]:
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=15)
                send_telegram_message(
                    f"⏳ <b>50-MIN TIME-STOP EXIT ({trade['mt5_name']})</b> ⏳\n"
                    f"50 मिनट बीत जाने के बाद भी मोमेंटम नहीं आया है। कृपया MT5 में इस ट्रेड को कॉस्ट के पास क्लोज करके बाहर निकल जाएं।"
                )
                continue

            # STAGE 4: FINAL TARGET 2 OR SL CHECK
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
                send_telegram_message(f"🎯 <b>FULL TARGET 2 HIT (+${net_win:.0f}) on {trade['mt5_name']}!</b> 🎯")
            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                consecutive_losses = 0
                consecutive_wins  += 1
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=15)
                locked_win = round(trade["expected_profit"] * 0.60, 1) if trade.get("near_tp_alerted") else 1.0
                daily_realized_pnl += locked_win
                total_account_pnl  += locked_win
                send_telegram_message(f"🛡️ <b>SAFE PROFIT LOCK EXIT (+${locked_win:.0f}) on {trade['mt5_name']}</b> 🛡️")
            elif hit_type == "SL":
                trade["is_open"] = False
                consecutive_wins    = 0
                consecutive_losses += 1
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=35)
                sl_direction_lock[pair_name][trade["direction"]] = now_utc + timedelta(minutes=60)
                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_account_pnl  -= loss_amt
                if consecutive_losses >= MAX_CONSECUTIVE_LOSSES_HALT:
                    global_pause_until = now_utc + timedelta(minutes=90)
                send_telegram_message(f"🛑 <b>STOP-LOSS HIT (-${loss_amt:.0f}) on {trade['mt5_name']}</b> ({sl_str})")

# =====================================================================
# 8. MASTER SCANNER LOOP (WITH NON-BLOCKING TELEGRAM COMMAND POLLING)
# =====================================================================
def scan_markets():
    global trades_today, current_day, last_candle_traded, last_structure_traded
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

    can_trade, dynamic_risk_usd, _ = human_pre_flight_safety_and_risk_calculator()
    if not can_trade:
        return

    forex_open  = is_forex_market_open()
    usdjpy_spot = fetch_tradingview_median_spot(["FX:USDJPY", "OANDA:USDJPY"], "forex") or 152.0
    usdcad_spot = fetch_tradingview_median_spot(["FX:USDCAD", "OANDA:USDCAD"], "forex") or 1.39

    for name, info in FOREX_PAIRS.items():
        # Poll Telegram commands before EVERY pair so /chart is answered within 0.5s!
        check_telegram_commands()

        if not forex_open and not info.get("is_crypto", False):
            continue
        try:
            df_15m, df_5m, live_spot, source_used = get_clean_spot_data(name, info, use_fast_cache=False)
            if df_5m is None or df_15m is None or live_spot is None:
                continue

            if (get_utc_now() - to_utc_timestamp(df_5m.index[-1])).total_seconds() > 1500:
                continue

            df_15m = calculate_chart_indicators(df_15m)
            df_5m  = calculate_chart_indicators(df_5m)

            closed_5m   = df_5m.iloc[-2]
            closed_time = to_utc_timestamp(df_5m.index[-2])
            atr_5m      = float(closed_5m["ATR"])
            dec         = info["decimals"]

            manage_open_trades(name, closed_time, live_spot, df_5m, dec)

            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            open_trades_count = sum(1 for t in daily_trades_log if t["is_open"])
            if open_trades_count >= MAX_CONCURRENT_TRADES:
                continue

            if (get_utc_now() - last_global_trade_time).total_seconds() < (MIN_GAP_BETWEEN_TRADES_MIN * 60):
                continue

            if last_candle_traded[name] == closed_time:
                continue

            cand = analyze_human_confluence_setups(name, info, df_15m, df_5m, live_spot, ignore_session_and_locks=False)

            if cand is not None:
                direction  = cand["direction"]
                signal     = cand["action"]
                short_tag  = cand["short_tag"]
                why_text   = cand["why_text"]
                custom_sl  = cand["sl_raw"]
                score      = cand["score"]
                s_type     = cand["setup_type"]

                if (direction == "BUY" and custom_sl >= live_spot) or (direction == "SELL" and custom_sl <= live_spot):
                    continue

                sl_level, sl_dist, half_target_level, near_tp_level, target_level, target_dist, actual_rr = calculate_smart_sl_and_easy_tp(
                    df_5m, df_15m, info, direction, live_spot, custom_sl, atr_5m, setup_type=s_type, allow_wall_block=True
                )

                if sl_level is None or sl_dist <= 0 or target_dist <= 0:
                    continue

                exact_lot = calculate_exact_mt5_lot(name, info, sl_dist, dynamic_risk_usd, usdjpy_spot, usdcad_spot)

                max_chase_buf = round(sl_dist * 0.22, dec)
                if direction == "BUY":
                    max_safe_entry = round(live_spot + max_chase_buf, dec)
                    entry_zone_str = f"<code>{format_price(live_spot, dec)}</code> से <code>{format_price(max_safe_entry, dec)}</code> तक"
                    no_chase_warn  = f"⚠️ अगर MT5 खोलते समय कीमत <code>{format_price(max_safe_entry, dec)}</code> से ऊपर निकल चुकी हो, तो ट्रेड मत लेना!"
                else:
                    max_safe_entry = round(live_spot - max_chase_buf, dec)
                    entry_zone_str = f"<code>{format_price(live_spot, dec)}</code> से <code>{format_price(max_safe_entry, dec)}</code> तक"
                    no_chase_warn  = f"⚠️ अगर MT5 खोलते समय कीमत <code>{format_price(max_safe_entry, dec)}</code> से नीचे निकल चुकी हो, तो ट्रेड मत लेना!"

                last_candle_traded[name]    = closed_time
                last_structure_traded[name] = cand["struct_id"]
                last_global_trade_time      = get_utc_now()
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
                    f"🧠 <b>A+ MANUAL SIGNAL #{trades_today}/{MAX_DAILY_TRADES_CAP} — {info['mt5_name']} ({score}/100)</b> 🧠\n\n"
                    f"🌍 <b>Pair:</b> <b>{name}</b> | 📢 <b>Action:</b> <b>{signal}</b>\n"
                    f"🏷️ <b>Setup:</b> <b>{short_tag}</b>\n"
                    f"💼 <b>MT5 Lot Size:</b> <b><code>{exact_lot:.2f} Lot</code></b>\n\n"
                    f"📍 <b>Safe Entry Zone:</b> {entry_zone_str}\n"
                    f"<i>({no_chase_warn})</i>\n\n"
                    f"🛑 <b>Stop-Loss (SL):</b> <code>{format_price(sl_level, dec)}</code>  <b>[{calculate_pips_or_points(name, sl_dist, dec)} दूर]</b>\n"
                    f"🛡️ <b>0.55R Safe Lock:</b> <code>{format_price(half_target_level, dec)}</code>\n"
                    f"🎯 <b>Target 1 (50% Book):</b> <code>{format_price(near_tp_level, dec)}</code>  <b>[{calculate_pips_or_points(name, tp1_dist, dec)} दूर]</b>\n"
                    f"🎯 <b>Target 2 (Full TP):</b> <code>{format_price(target_level, dec)}</code>  <b>[{calculate_pips_or_points(name, target_dist, dec)} दूर]</b>\n\n"
                    f"🔍 <b>Why This Trade:</b>\n{why_text}\n"
                    f"⛽ <b>Risk:</b> ${dynamic_risk_usd:.0f} ➡️ <b>Full Reward:</b> <b>+${expected_prof:.0f}</b>"
                )

                chart_buf = generate_mt5_mobile_style_chart(
                    df_5m, info, direction, live_spot, sl_level, target_level, near_tp_level,
                    short_tag, exact_lot, human_score=score, is_live_signal=True
                )
                send_telegram_signal_with_chart(msg, chart_buf)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    fetch_live_economic_calendar()
    send_telegram_message(
        "🧠 <b>Human Brain V4 (Instant Live-Tick + Pro TradingView Chart) Online</b>\n"
        "⚡ <b>Zero-Delay Live Price:</b> हर कमांड पर 0.1s में नया Live Price (65367 -> 65380) अपडेट होगा\n"
        "📊 <b>Experienced Trader Chart:</b> Supply/Demand Zones, HH/HL/LH/LL Swings & TradingView Position Tool"
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
            print("🛑 Bot stopped manually.")
            break
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(3)
