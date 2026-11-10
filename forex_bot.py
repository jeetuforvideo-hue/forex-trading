import os
import io
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches
    CHART_AVAILABLE = True
except ImportError:
    CHART_AVAILABLE = False

# =====================================================================
# 1. $100,000 ALL-ROUNDER PRICE-ACTION & FIBONACCI ENGINE
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "5610520408"

TOTAL_CAPITAL_USD          = 100000.0
MAX_DAILY_DRAWDOWN         = 5000.0
MAX_TOTAL_DRAWDOWN         = 10000.0

RISK_PER_TRADE_USD         = 400.0     # $400 Risk (0.40%) per trade
TARGET_RR_RATIO            = 1.8       # 1:1.8 Target (+$720 Profit per trade)
LOCK_PROFIT_R              = 0.8       # 0.8R Safe Breakeven Lock
DAILY_PROTECTION_CAP       = 1600.0
MAX_CONCURRENT_TRADES      = 2
MIN_GAP_BETWEEN_TRADES_MIN = 12

FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "mt5_name": "XAUUSD",
        "tv_ticker": "OANDA:XAUUSD",
        "tv_screener": "cfd",
        "kraken_pair": "PAXGUSD",
        "binance_symbol": "PAXGUSDT",
        "yf_symbol": "GC=F",
        "decimals": 2,
        "unit_name": "Points",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 5.00,
        "max_sl_dist": 9.50,
        "max_lot_cap": 1.25,
        "is_crypto": False
    },
    "EUR/USD": {
        "mt5_name": "EURUSD",
        "tv_ticker": "OANDA:EURUSD",
        "tv_screener": "forex",
        "kraken_pair": "EURUSD",
        "binance_symbol": "EURUSDT",
        "yf_symbol": "EURUSD=X",
        "decimals": 5,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0013,
        "max_sl_dist": 0.0025,
        "max_lot_cap": 3.00,
        "is_crypto": False
    },
    "GBP/USD": {
        "mt5_name": "GBPUSD",
        "tv_ticker": "OANDA:GBPUSD",
        "tv_screener": "forex",
        "kraken_pair": "GBPUSD",
        "binance_symbol": None,
        "yf_symbol": "GBPUSD=X",
        "decimals": 5,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0015,
        "max_sl_dist": 0.0028,
        "max_lot_cap": 3.00,
        "is_crypto": False
    },
    "USD/JPY": {
        "mt5_name": "USDJPY",
        "tv_ticker": "OANDA:USDJPY",
        "tv_screener": "forex",
        "kraken_pair": "USDJPY",
        "binance_symbol": None,
        "yf_symbol": "JPY=X",
        "decimals": 3,
        "unit_name": "Pips",
        "usd_exposure": "LONG_USD_ON_BUY",
        "min_sl_dist": 0.150,
        "max_sl_dist": 0.280,
        "max_lot_cap": 3.00,
        "is_crypto": False
    },
    "GBP/JPY": {
        "mt5_name": "GBPJPY",
        "tv_ticker": "OANDA:GBPJPY",
        "tv_screener": "forex",
        "kraken_pair": None,
        "binance_symbol": None,
        "yf_symbol": "GBPJPY=X",
        "decimals": 3,
        "unit_name": "Pips",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 0.200,
        "max_sl_dist": 0.360,
        "max_lot_cap": 2.50,
        "is_crypto": False
    },
    "AUD/USD": {
        "mt5_name": "AUDUSD",
        "tv_ticker": "OANDA:AUDUSD",
        "tv_screener": "forex",
        "kraken_pair": "AUDUSD",
        "binance_symbol": None,
        "yf_symbol": "AUDUSD=X",
        "decimals": 5,
        "unit_name": "Pips",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0012,
        "max_sl_dist": 0.0022,
        "max_lot_cap": 3.00,
        "is_crypto": False
    },
    "BTC/USD (BITCOIN)": {
        "mt5_name": "BTCUSD",
        "tv_ticker": "BITSTAMP:BTCUSD",
        "tv_screener": "crypto",
        "kraken_pair": "XBTUSD",
        "binance_symbol": "BTCUSDT",
        "yf_symbol": "BTC-USD",
        "decimals": 2,
        "unit_name": "Points",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 260.0,
        "max_sl_dist": 520.0,
        "max_lot_cap": 1.50,
        "is_crypto": True
    }
}

last_candle_traded     = {name: None for name in FOREX_PAIRS}
last_structure_traded  = {name: None for name in FOREX_PAIRS}
pair_cooldown_until    = {name: datetime.min.replace(tzinfo=timezone.utc) for name in FOREX_PAIRS}
sl_direction_lock      = {name: {"BUY": datetime.min.replace(tzinfo=timezone.utc), "SELL": datetime.min.replace(tzinfo=timezone.utc)} for name in FOREX_PAIRS}
last_global_trade_time = datetime.min.replace(tzinfo=timezone.utc)

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
        return 0.10

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
        return 0.10

    raw_lot = risk_usd / loss_per_1_lot
    max_cap = pair_info.get("max_lot_cap", 2.00)
    return max(0.05, min(round(raw_lot, 2), max_cap))

# =====================================================================
# 2. MULTI-SOURCE SPOT DATA ENGINE (OANDA + KRAKEN + BINANCE)
# =====================================================================
def fetch_tradingview_live_spot(tv_ticker, screener="forex"):
    url = f"https://scanner.tradingview.com/{screener}/scan"
    payload = {
        "symbols": {"tickers": [tv_ticker], "query": {"types": []}},
        "columns": ["close", "Recommend.Other", "RSI"]
    }
    headers = {"User-Agent": "Mozilla/5.0"}
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

def fetch_kraken_ohlc(kraken_pair, interval_min):
    if not kraken_pair:
        return None
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": kraken_pair, "interval": interval_min}
    try:
        r = requests.get(url, params=params, timeout=7).json()
        if not r.get("error"):
            res = r.get("result", {})
            keys = [k for k in res.keys() if k != "last"]
            if keys:
                rows = res[keys[0]]
                df = pd.DataFrame(rows, columns=["time", "Open", "High", "Low", "Close", "vwap", "Volume", "count"])
                df["Date"] = pd.to_datetime(df["time"], unit="s", utc=True)
                df.set_index("Date", inplace=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = df[col].astype(float)
                if len(df) >= 50:
                    return df[["Open", "High", "Low", "Close", "Volume"]]
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

def get_clean_spot_data(pair_name, info):
    df_5m  = None
    df_15m = None
    source_used = "Spot Feed"

    if info.get("kraken_pair"):
        df_5m  = fetch_kraken_ohlc(info["kraken_pair"], 5)
        df_15m = fetch_kraken_ohlc(info["kraken_pair"], 15)
        if df_5m is not None and df_15m is not None:
            source_used = "Kraken Spot + OANDA Sync"

    if (df_5m is None or df_15m is None) and info.get("binance_symbol"):
        df_5m  = fetch_binance_vision_ohlc(info["binance_symbol"], "5m", 120)
        df_15m = fetch_binance_vision_ohlc(info["binance_symbol"], "15m", 100)
        if df_5m is not None and df_15m is not None:
            source_used = "Binance Spot + OANDA Sync"

    if df_5m is None or df_15m is None:
        try:
            tkr = yf.Ticker(info["yf_symbol"])
            df_5m  = tkr.history(period="5d", interval="5m")
            df_15m = tkr.history(period="5d", interval="15m")
            source_used = "OANDA Spot Sync"
        except Exception:
            return None, None, None, ""

    if df_5m is None or df_5m.empty or len(df_5m) < 55 or df_15m is None or df_15m.empty or len(df_15m) < 40:
        return None, None, None, ""

    tv_spot = fetch_tradingview_live_spot(info["tv_ticker"], info["tv_screener"])
    raw_last_close = float(df_5m.iloc[-1]["Close"])

    if tv_spot is not None and tv_spot > 0:
        basis_diff = tv_spot - raw_last_close
        if abs(basis_diff) < (tv_spot * 0.03):
            for col in ["Open", "High", "Low", "Close"]:
                df_5m[col]  = df_5m[col] + basis_diff
                df_15m[col] = df_15m[col] + basis_diff
            live_spot_price = tv_spot
        else:
            live_spot_price = raw_last_close
    else:
        live_spot_price = raw_last_close

    return df_15m, df_5m, live_spot_price, source_used

# =====================================================================
# 3. TECHNICAL INDICATORS & 4 CORE PRICE-ACTION DETECTORS
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

def detect_fibonacci_swing_structure(df_5m, lookback=38):
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

def detect_reversal_neckline_breakout(df_5m):
    """
    Setup A: Inverse Head & Shoulders / W-Bottom / M-Top Neckline Breakout Detector
    (जैसे XAUUSD में 4108 का Head + 4111.50 का Right Shoulder बनने के बाद 4117 का Neckline टूटा)
    """
    if len(df_5m) < 40:
        return None

    c0 = df_5m.iloc[-2]  # अभी क्लोज हुई ब्रेकआउट कैंडल
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

    # पिछले 28 कैंडल्स में स्विंग स्ट्रक्चर देखें
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

    # 1. BULLISH REVERSAL (Inverse Head & Shoulders / Double Bottom + Neckline Breakout)
    # शर्त: Head Low कम से कम 3 से 22 कैंडल पहले बना हो, उसके बाद Higher Low (Right Shoulder) बना हो और अब Neckline टूटी हो
    if 4 <= (n_len - min_idx) <= 23:
        post_head_highs = highs[min_idx + 1:]
        post_head_lows  = lows[min_idx + 2:] if (min_idx + 2) < n_len else lows[min_idx + 1:]

        if len(post_head_highs) >= 3 and len(post_head_lows) >= 2:
            neckline_res       = float(np.max(post_head_highs[:-1])) if len(post_head_highs) > 1 else float(np.max(post_head_highs))
            right_shoulder_low = float(np.min(post_head_lows))

            # Higher Low (Right Shoulder >= Head Low) और Neckline उचित दूरी पर हो
            valid_structure = (right_shoulder_low > head_low) and ((neckline_res - head_low) >= atr_5m * 1.1) and ((neckline_res - head_low) <= atr_5m * 3.8)
            broke_neckline  = (price > neckline_res) and (float(df_5m.iloc[-3]["Close"]) <= neckline_res * 1.0003)
            strong_ignition = (price > open_0) and (price > ema9) and (price > ema20) and (body_ratio >= 0.48) and (close_pos >= 0.65) and (disp_power >= 0.95)

            if valid_structure and broke_neckline and strong_ignition and (42.0 <= rsi <= 72.0):
                sl_level = min(right_shoulder_low, low_0) - (atr_5m * 0.25)
                return {
                    "direction": "BUY",
                    "action": "BUY (LONG) 🟢",
                    "struct_id": f"IHNS_BUY_{times[min_idx]}",
                    "setup_text": f"Inverse H&S / Bottom Reversal Neckline Breakout ({neckline_res:.2f}) | RSI: {rsi:.0f}",
                    "sl_raw": sl_level,
                    "neckline": neckline_res,
                    "head_level": head_low
                }

    # 2. BEARISH REVERSAL (Head & Shoulders / Double Top + Neckline Breakdown)
    if 4 <= (n_len - max_idx) <= 23:
        post_head_lows  = lows[max_idx + 1:]
        post_head_highs = highs[max_idx + 2:] if (max_idx + 2) < n_len else highs[max_idx + 1:]

        if len(post_head_lows) >= 3 and len(post_head_highs) >= 2:
            neckline_sup        = float(np.min(post_head_lows[:-1])) if len(post_head_lows) > 1 else float(np.min(post_head_lows))
            right_shoulder_high = float(np.max(post_head_highs))

            valid_structure = (right_shoulder_high < head_high) and ((head_high - neckline_sup) >= atr_5m * 1.1) and ((head_high - neckline_sup) <= atr_5m * 3.8)
            broke_neckline  = (price < neckline_sup) and (float(df_5m.iloc[-3]["Close"]) >= neckline_sup * 0.9997)
            strong_collapse = (price < open_0) and (price < ema9) and (price < ema20) and (body_ratio >= 0.48) and (close_pos <= 0.35) and (disp_power >= 0.95)

            if valid_structure and broke_neckline and strong_collapse and (28.0 <= rsi <= 58.0):
                sl_level = max(right_shoulder_high, high_0) + (atr_5m * 0.25)
                return {
                    "direction": "SELL",
                    "action": "SELL (SHORT) 🔴",
                    "struct_id": f"HNS_SELL_{times[max_idx]}",
                    "setup_text": f"H&S / Top Reversal Neckline Breakdown ({neckline_sup:.2f}) | RSI: {rsi:.0f}",
                    "sl_raw": sl_level,
                    "neckline": neckline_sup,
                    "head_level": head_high
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
# 4. MASTER DECISION ENGINE (ALL 4 SETUPS ACTIVE)
# =====================================================================
def evaluate_all_market_setups(pair_name, pair_info, df_15m, df_5m):
    """
    चारों तरह के प्रोफेशनल सेटअप चेक करता है:
    1. Setup A: Inverse H&S / Double Bottom / Top Neckline Breakout (Reversal)
    2. Setup B: Fibonacci (0.382-0.618) + EMA 9/20 Trend Pullback
    3. Setup C: 10-Bar Liquidity Sweep / Stop-Hunt Reclaim
    4. Setup D: 6-Bar Consolidation Box Breakout Ignition
    """
    now_utc = get_utc_now()
    if now_utc < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
        return None, None, None, None, None

    session_ok, _ = get_trading_session(pair_info.get("is_crypto", False))
    if not session_ok:
        return None, None, None, None, None

    if not check_usd_exposure_filter(pair_info):
        return None, None, None, None, None

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
        return None, None, None, None, None

    fib = detect_fibonacci_swing_structure(df_5m, lookback=38)

    # -----------------------------------------------------------------
    # 1. SETUP A: REVERSAL NECKLINE BREAKOUT (Inverse H&S / W-Bottom / M-Top)
    # (15M के पुराने ट्रेंड का इंतज़ार किए बिना नीचे/ऊपर से पलटने वाला ब्रेकआउट पकड़ता है)
    # -----------------------------------------------------------------
    rev = detect_reversal_neckline_breakout(df_5m)
    if rev is not None:
        r_dir = rev["direction"]
        if now_utc >= sl_direction_lock[pair_name][r_dir] and last_structure_traded.get(pair_name) != rev["struct_id"]:
            return rev["action"], r_dir, rev["setup_text"], rev["sl_raw"], fib

    # 15M Trend Direction for Trend Setups
    m15_c     = df_15m.iloc[-2]
    m15_close = float(m15_c["Close"])
    m15_ema20 = float(m15_c["EMA20"])
    m15_ema50 = float(m15_c["EMA50"])

    htf_bullish = (m15_close >= m15_ema20) or (ema9 > ema20 > ema50)
    htf_bearish = (m15_close <= m15_ema20) or (ema9 < ema20 < ema50)

    # -----------------------------------------------------------------
    # 2. SETUP B: FIBONACCI (0.382-0.618) + EMA 9/20 TREND PULLBACK
    # -----------------------------------------------------------------
    if fib is not None and last_structure_traded.get(pair_name) != fib["swing_id"]:
        ema_separation = abs(ema9 - ema20)
        if ema_separation >= (atr_5m * 0.10):
            # Bullish Fib Pullback
            if htf_bullish and fib["type"] == "BULLISH_SWING" and now_utc >= sl_direction_lock[pair_name]["BUY"]:
                recent_3_low  = min(low_0, float(c1["Low"]), float(c2["Low"]))
                in_fib_zone   = (recent_3_low <= fib["fib_382"]) and (recent_3_low >= fib["fib_786"])
                ema_support   = (price > ema9) and (ema9 >= ema20 * 0.9995)
                green_trigger = (price > open_0) and (close_pos >= 0.58) and (body_ratio >= 0.42) and (price > float(c1["High"]) or price > float(c1["Close"]))
                if in_fib_zone and ema_support and green_trigger and (38.0 <= rsi <= 66.0):
                    sl_raw = min(recent_3_low, fib["fib_786"]) - (atr_5m * 0.25)
                    setup_text = f"Fibonacci Golden Zone (0.50-0.618) + EMA 9/20 Bounce | RSI: {rsi:.0f}"
                    return "BUY (LONG) 🟢", "BUY", setup_text, sl_raw, fib

            # Bearish Fib Pullback
            if htf_bearish and fib["type"] == "BEARISH_SWING" and now_utc >= sl_direction_lock[pair_name]["SELL"]:
                recent_3_high = max(high_0, float(c1["High"]), float(c2["High"]))
                in_fib_zone   = (recent_3_high >= fib["fib_382"]) and (recent_3_high <= fib["fib_786"])
                ema_resist    = (price < ema9) and (ema9 <= ema20 * 1.0005)
                red_trigger   = (price < open_0) and (close_pos <= 0.42) and (body_ratio >= 0.42) and (price < float(c1["Low"]) or price < float(c1["Close"]))
                if in_fib_zone and ema_resist and red_trigger and (34.0 <= rsi <= 62.0):
                    sl_raw = max(recent_3_high, fib["fib_786"]) + (atr_5m * 0.25)
                    setup_text = f"Fibonacci Golden Zone (0.50-0.618) + EMA 9/20 Reject | RSI: {rsi:.0f}"
                    return "SELL (SHORT) 🔴", "SELL", setup_text, sl_raw, fib

    # -----------------------------------------------------------------
    # 3. SETUP C: 10-BAR STOP-HUNT LIQUIDITY SWEEP & RECLAIM
    # -----------------------------------------------------------------
    pool_10   = df_5m.iloc[-13:-3]
    pool_high = float(pool_10["High"].max())
    pool_low  = float(pool_10["Low"].min())
    sweep_id  = f"SWEEP_{pool_low}_{pool_high}"

    if last_structure_traded.get(pair_name) != sweep_id:
        # Bullish Stop-Hunt Sweep
        swept_below = (low_0 < pool_low and price > pool_low) or (float(c1["Low"]) < pool_low and price > float(c1["High"]) and price > pool_low)
        bull_reclaim = (price > open_0) and (price > ema9) and (close_pos >= 0.62) and (l_wick >= float(c0["Range"]) * 0.28 or disp_power >= 0.95)
        if swept_below and bull_reclaim and (35.0 <= rsi <= 65.0) and now_utc >= sl_direction_lock[pair_name]["BUY"]:
            sl_raw = min(low_0, float(c1["Low"])) - (atr_5m * 0.25)
            setup_text = f"Liquidity Sweep Stop-Hunt & Bullish Reclaim ({pool_low:.2f}) | RSI: {rsi:.0f}"
            if fib is None:
                fib = {"swing_id": sweep_id, "fib_500": pool_low, "fib_618": pool_low, "fib_0": pool_high, "fib_382": pool_low, "fib_786": sl_raw, "fib_100": pool_low}
            else:
                fib["swing_id"] = sweep_id
            return "BUY (LONG) 🟢", "BUY", setup_text, sl_raw, fib

        # Bearish Stop-Hunt Sweep
        swept_above = (high_0 > pool_high and price < pool_high) or (float(c1["High"]) > pool_high and price < float(c1["Low"]) and price < pool_high)
        bear_reclaim = (price < open_0) and (price < ema9) and (close_pos <= 0.38) and (u_wick >= float(c0["Range"]) * 0.28 or disp_power >= 0.95)
        if swept_above and bear_reclaim and (35.0 <= rsi <= 65.0) and now_utc >= sl_direction_lock[pair_name]["SELL"]:
            sl_raw = max(high_0, float(c1["High"])) + (atr_5m * 0.25)
            setup_text = f"Liquidity Sweep Stop-Hunt & Bearish Breakdown ({pool_high:.2f}) | RSI: {rsi:.0f}"
            if fib is None:
                fib = {"swing_id": sweep_id, "fib_500": pool_high, "fib_618": pool_high, "fib_0": pool_low, "fib_382": pool_high, "fib_786": sl_raw, "fib_100": pool_high}
            else:
                fib["swing_id"] = sweep_id
            return "SELL (SHORT) 🔴", "SELL", setup_text, sl_raw, fib

    # -----------------------------------------------------------------
    # 4. SETUP D: 6-BAR CONSOLIDATION BOX BREAKOUT IGNITION
    # -----------------------------------------------------------------
    box_6    = df_5m.iloc[-8:-2]
    box_high = float(box_6["High"].max())
    box_low  = float(box_6["Low"].min())
    box_rng  = box_high - box_low
    box_id   = f"BOX_{box_low}_{box_high}"

    if box_rng <= (atr_5m * 2.2) and last_structure_traded.get(pair_name) != box_id:
        # Bullish Box Breakout
        if htf_bullish and price > box_high and price > open_0 and ema9 > ema20 and body_ratio >= 0.55 and close_pos >= 0.68 and disp_power >= 1.15 and (45.0 <= rsi <= 72.0):
            if now_utc >= sl_direction_lock[pair_name]["BUY"]:
                sl_raw = max(box_low, low_0 - (atr_5m * 0.25))
                setup_text = f"Consolidation Range Breakout Above {box_high:.2f} | RSI: {rsi:.0f}"
                if fib is None:
                    fib = {"swing_id": box_id, "fib_500": box_high, "fib_618": box_low, "fib_0": price, "fib_382": box_high, "fib_786": sl_raw, "fib_100": box_low}
                else:
                    fib["swing_id"] = box_id
                return "BUY (LONG) 🟢", "BUY", setup_text, sl_raw, fib

        # Bearish Box Breakdown
        if htf_bearish and price < box_low and price < open_0 and ema9 < ema20 and body_ratio >= 0.55 and close_pos <= 0.32 and disp_power >= 1.15 and (28.0 <= rsi <= 55.0):
            if now_utc >= sl_direction_lock[pair_name]["SELL"]:
                sl_raw = min(box_high, high_0 + (atr_5m * 0.25))
                setup_text = f"Consolidation Range Breakdown Below {box_low:.2f} | RSI: {rsi:.0f}"
                if fib is None:
                    fib = {"swing_id": box_id, "fib_500": box_low, "fib_618": box_high, "fib_0": price, "fib_382": box_low, "fib_786": sl_raw, "fib_100": box_high}
                else:
                    fib["swing_id"] = box_id
                return "SELL (SHORT) 🔴", "SELL", setup_text, sl_raw, fib

    return None, None, None, None, None

# =====================================================================
# 5. CHART GENERATOR WITH FIBONACCI LEVELS + EMA 9 & 20
# =====================================================================
def generate_fibonacci_ema_chart(df_5m, pair_name, direction, entry_p, sl_p, tp_p, half_p, fib, decimals, lot_size, risk_usd, reward_usd, rr_ratio):
    if not CHART_AVAILABLE:
        return None
    try:
        sub = df_5m.iloc[-45:-1].copy()
        if sub.empty or len(sub) < 20:
            return None

        fig, ax = plt.subplots(figsize=(11.5, 6.5), dpi=130)
        fig.patch.set_facecolor("#f3f4f6")
        ax.set_facecolor("#f3f4f6")

        n_bars = len(sub)
        x_vals = np.arange(n_bars)

        ax.plot(x_vals, sub["EMA9"].values, color="#16a34a", linewidth=1.5, label="EMA 9 (Green)", zorder=4)
        ax.plot(x_vals, sub["EMA20"].values, color="#dc2626", linewidth=1.5, label="EMA 20 (Red)", zorder=4)

        if fib is not None and "fib_500" in fib and "fib_618" in fib:
            gold_top = max(fib["fib_500"], fib["fib_618"])
            gold_bot = min(fib["fib_500"], fib["fib_618"])
            if abs(gold_top - gold_bot) > 0:
                ax.add_patch(mpatches.Rectangle(
                    (0, gold_bot), n_bars + 14, gold_top - gold_bot,
                    facecolor="#fbbf24", alpha=0.20, edgecolor="#d97706", linestyle="--", linewidth=1.0, zorder=1
                ))
            for f_lbl, f_val, f_col in [
                ("Key Level / 0.50", fib["fib_500"], "#d97706"),
                ("Golden / 0.618", fib["fib_618"], "#b45309"),
                ("Invalidation / SL Guard", fib["fib_786"], "#dc2626")
            ]:
                ax.axhline(f_val, color=f_col, linestyle=":", linewidth=0.9, alpha=0.75, zorder=2)
                ax.text(0.5, f_val, f" {f_lbl}: {format_price(f_val, decimals)} ", fontsize=7.2, color=f_col, fontweight="bold", va="bottom")

        for idx in range(n_bars):
            row = sub.iloc[idx]
            o, h, l, c = float(row["Open"]), float(row["High"]), float(row["Low"]), float(row["Close"])
            color = "#16a34a" if c >= o else "#dc2626"
            ax.plot([idx, idx], [l, h], color=color, linewidth=1.1, zorder=3)
            body_low = min(o, c)
            body_h = max(abs(c - o), (h - l) * 0.04, 1e-6)
            ax.add_patch(mpatches.Rectangle((idx - 0.32, body_low), 0.64, body_h, facecolor=color, edgecolor=color, linewidth=0.8, zorder=3))

        box_start_x = n_bars - 1
        box_width = 15
        box_center_x = box_start_x + (box_width / 2.0)

        ax.add_patch(mpatches.Rectangle((box_start_x, min(entry_p, tp_p)), box_width, abs(tp_p - entry_p), facecolor="#22c55e", alpha=0.28, edgecolor="#15803d", linewidth=1.2, zorder=4))
        ax.add_patch(mpatches.Rectangle((box_start_x, min(entry_p, sl_p)), box_width, abs(entry_p - sl_p), facecolor="#ef4444", alpha=0.28, edgecolor="#b91c1c", linewidth=1.2, zorder=4))

        ax.plot([box_start_x, box_start_x + box_width], [entry_p, entry_p], color="#1f2937", linewidth=1.6, zorder=5)
        ax.plot([box_start_x, box_start_x + box_width], [half_p, half_p], color="#0284c7", linestyle="--", linewidth=1.1, alpha=0.85, zorder=5)

        entry_s, sl_s, tp_s, half_s = format_price(entry_p, decimals), format_price(sl_p, decimals), format_price(tp_p, decimals), format_price(half_p, decimals)

        ax.text(box_center_x, tp_p, f" Target (1:{rr_ratio}): {tp_s} (+${reward_usd:.0f}) ", color="white", fontweight="bold", fontsize=8.5, ha="center", va="bottom" if direction == "BUY" else "top", bbox=dict(boxstyle="round,pad=0.35", facecolor="#16a34a", edgecolor="none"), zorder=7)
        ax.text(box_center_x, sl_p, f" Structure SL: {sl_s} (-${risk_usd:.0f}) ", color="white", fontweight="bold", fontsize=8.5, ha="center", va="top" if direction == "BUY" else "bottom", bbox=dict(boxstyle="round,pad=0.35", facecolor="#dc2626", edgecolor="none"), zorder=7)

        pos_title = "PRO LONG SETUP" if direction == "BUY" else "PRO SHORT SETUP"
        ax.text(box_center_x, entry_p, f" {pos_title} @ {entry_s} | Lot: {lot_size:.2f}\n 0.8R Safe Lock: {half_s} ", color="white", fontweight="bold", fontsize=8.2, ha="center", va="center", bbox=dict(boxstyle="round,pad=0.38", facecolor="#1f2937", edgecolor="white", alpha=0.92), zorder=8)

        ax.yaxis.tick_right()
        right_edge_x = box_start_x + box_width + 4.5
        ax.set_xlim(-1, right_edge_x)
        all_y = list(sub["High"].values) + list(sub["Low"].values) + [tp_p, sl_p, entry_p]
        y_min, y_max = min(all_y), max(all_y)
        y_pad = (y_max - y_min) * 0.14
        ax.set_ylim(y_min - y_pad, y_max + y_pad)

        ax.set_title(f"{pair_name} — Live M5 Price-Action & Fibonacci Setup ({lot_size:.2f} Lot)", loc="left", fontsize=11, fontweight="bold", color="#111827", pad=10)
        ax.legend(loc="upper left", fontsize=8)
        ax.grid(True, linestyle="--", alpha=0.30, color="#9ca3af")

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Chart Error: {e}")
        return None

def send_telegram_signal_with_chart(caption_html, chart_buf, target_chat_id=CHAT_ID):
    if chart_buf is not None:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        try:
            if len(caption_html) <= 1020:
                files = {"photo": ("price_action_setup.png", chart_buf, "image/png")}
                data = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                res = requests.post(url, data=data, files=files, timeout=15)
                if res.status_code == 200:
                    return
            else:
                files = {"photo": ("price_action_setup.png", chart_buf, "image/png")}
                data = {"chat_id": target_chat_id, "caption": "📊 <b>Live M5 Price-Action & Fibonacci Chart</b>", "parse_mode": "HTML"}
                requests.post(url, data=data, files=files, timeout=15)
                send_telegram_message(caption_html, target_chat_id)
                return
        except Exception as e:
            print(f"Photo Send Error: {e}")
    send_telegram_message(caption_html, target_chat_id)

# =====================================================================
# 6. ACTIVE TRADE MANAGEMENT & /STATUS COMMAND
# =====================================================================
def send_live_status_summary():
    forex_open = is_forex_market_open()
    market_state = "🟢 ALL 4 SETUPS ACTIVE" if forex_open else "🟡 WEEKEND: BTC/USD ACTIVE"
    _, session_name = get_trading_session(True)

    report = (
        f"📐 <b>ALL-ROUNDER PRICE-ACTION ENGINE STATUS</b> 📐\n\n"
        f"📢 <b>Market:</b> {market_state} ({session_name})\n"
        f"🧠 <b>Active Setups:</b> Inverse H&S Reversal + Fib 0.50-0.618 + Sweep + Box Breakout\n\n"
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
                    half_prof                = round(trade["max_risk"] * 0.5, 0)
                    trade["status"]          = "0.8R LOCKED"
                    send_telegram_message(
                        f"🛡️ <b>0.8R SAFE LOCK REACHED — MOVE SL TO ENTRY</b> 🛡️\n\n"
                        f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                        f"📍 <b>Entry:</b> {entry_str}  |  <b>Live Spot:</b> {price_str}\n"
                        f"📢 <b>Action:</b> 50% लॉट बुक करें (<b>+${half_prof:.0f}</b>) और बचे हुए लॉट का SL <b>Entry ({entry_str})</b> पर सेट करें।"
                    )
                    continue

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
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=40)
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
                half_prof = round(trade["max_risk"] * 0.5, 0)
                daily_realized_pnl += half_prof
                total_account_pnl += half_prof
                trade["status"] = "BREAKEVEN EXIT"
                send_telegram_message(
                    f"🛡️ <b>BREAKEVEN EXIT (+${half_prof:.0f})</b> 🛡️\n\n"
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

            signal, direction, setup_text, custom_sl, struct_info = evaluate_all_market_setups(
                name, info, df_15m, df_5m
            )

            if signal and custom_sl is not None:
                if direction == "BUY" and custom_sl >= live_spot:
                    continue
                if direction == "SELL" and custom_sl <= live_spot:
                    continue

                raw_sl_dist = abs(live_spot - custom_sl)
                min_sl      = max(atr_5m * 1.10, info.get("min_sl_dist", atr_5m * 1.10))
                max_sl      = max(atr_5m * 2.20, info.get("max_sl_dist", atr_5m * 2.20))
                sl_dist     = round(max(min_sl, min(raw_sl_dist, max_sl)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                if struct_info is not None and "swing_id" in struct_info:
                    last_structure_traded[name] = struct_info["swing_id"]
                last_global_trade_time = get_utc_now()
                trades_today += 1

                rr_ratio    = TARGET_RR_RATIO
                target_dist = round(sl_dist * rr_ratio, dec)
                lock_dist   = round(sl_dist * LOCK_PROFIT_R, dec)

                if direction == "BUY":
                    sl_level          = round(live_spot - sl_dist, dec)
                    half_target_level = round(live_spot + lock_dist, dec)
                    target_level      = round(live_spot + target_dist, dec)
                else:
                    sl_level          = round(live_spot + sl_dist, dec)
                    half_target_level = round(live_spot - lock_dist, dec)
                    target_level      = round(live_spot - target_dist, dec)

                max_risk_usd  = RISK_PER_TRADE_USD
                expected_prof = round(max_risk_usd * rr_ratio, 0)
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
                    "target": target_level,
                    "max_risk": max_risk_usd,
                    "expected_profit": expected_prof,
                    "entry_candle_time": closed_time,
                    "trailed_to_cost": False,
                    "is_open": True,
                    "status": "RUNNING"
                })

                entry_s  = format_price(live_spot, dec)
                sl_s     = format_price(sl_level, dec)
                half_s   = format_price(half_target_level, dec)
                target_s = format_price(target_level, dec)

                sl_pips_str     = calculate_pips_or_points(name, sl_dist, dec)
                target_pips_str = calculate_pips_or_points(name, target_dist, dec)

                msg = (
                    f"📐 <b>PRO PRICE-ACTION & FIBONACCI SIGNAL</b> 📐\n\n"
                    f"🌍 <b>Asset:</b> <b>{name}</b> (<code>{info['mt5_name']}</code>)\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n"
                    f"🛰️ <b>Spot Feed:</b> {source_used}\n"
                    f"🧠 <b>Pattern Detected:</b> {setup_text}\n\n"
                    f"📍 <b>MT5 Spot Entry:</b> <code>{entry_s}</code>\n"
                    f"🛑 <b>Structure SL:</b> <code>{sl_s}</code>  <i>({sl_pips_str})</i>\n"
                    f"🛡️ <b>0.8R Safe Lock:</b> <code>{half_s}</code>\n"
                    f"🎯 <b>Target (1:{rr_ratio}):</b> <code>{target_s}</code>  <i>({target_pips_str})</i>\n\n"
                    f"💼 <b>MT5 Lot Size:</b> <b><code>{exact_lot:.2f} Lot</code></b>\n"
                    f"💰 <b>Risk:</b> ${max_risk_usd:.0f}  ➡️  <b>Reward:</b> <b>+${expected_prof:.0f}</b>"
                )

                chart_buf = generate_fibonacci_ema_chart(
                    df_5m, name, direction, live_spot, sl_level, target_level,
                    half_target_level, struct_info, dec, exact_lot, max_risk_usd, expected_prof, rr_ratio
                )
                send_telegram_signal_with_chart(msg, chart_buf)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    send_telegram_message(
        "📐 <b>All-Rounder Price-Action & Fibonacci Engine Online</b> 📐\n\n"
        "• <b>Data Feeds:</b> TradingView OANDA Spot + Kraken + Binance Vision\n"
        "• <b>4 Active Setups:</b>\n"
        "  1. Inverse H&S / Double Bottom & Top Neckline Breakout\n"
        "  2. Fibonacci (0.382-0.618) + EMA 9/20 Trend Pullback\n"
        "  3. Liquidity Sweep Stop-Hunt & Reclaim\n"
        "  4. Consolidation Box Breakout Ignition"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 25:
            scan_markets()
            last_scan_time = time.time()
        time.sleep(3)
