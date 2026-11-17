import os
import io
import csv
import sys
import time
import subprocess
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

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
# 1. PDF ROADMAP CONFIGURATION ($5,000 TWO-STEP FUNDED ACCOUNT)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID   = os.getenv("CHAT_ID") or "5610520408"

STARTING_CAPITAL_USD         = 5000.0
HIGH_WATER_MARK_USD          = 5000.0

# Exact PDF Section 2 Risk Rules ($5,000 Account)
RISK_PER_TRADE_USD           = 12.50     # 0.25% of $5,000 (Max planned loss including spread/costs)
DAILY_STOP_LOSS_USD          = 25.00     # 0.50% of $5,000 (Stop after 2 losses OR -$25 total loss)
WEEKLY_STOP_LOSS_USD         = 75.00     # 1.50% of $5,000 (Weekly review/stop threshold)
MAX_DRAWDOWN_REVIEW_USD      = 100.00    # 2.00% of $5,000 from Starting Balance / High-Water Mark

MAX_DAILY_TRADES             = 2         # Maximum 2 high-conviction trades per day
MAX_DAILY_LOSS_COUNT         = 2         # Stop for the day after 2 losses
PRIMARY_RR_TARGET            = 2.00      # 1:2 Risk-to-Reward ($12.50 risk -> $25.00 target)
BREAKEVEN_LOCK_R             = 1.00      # Lock SL to Entry at 1:1 (+1R) to protect capital
PRE_ALERT_EXPIRY_MINUTES     = 25
MAX_TRADE_HOLD_MINUTES       = 90

STRICT_NY_SESSION_ONLY       = True
JOURNAL_CSV_PATH             = "trading_journal.csv"
BACKTEST_CSV_PATH            = "backtest_full_report.csv"

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# PDF Section 1: Primary Market = XAUUSD, Second Market = BTCUSD
FOREX_PAIRS = {
    "XAU/USD": {
        "mt5_name": "XAUUSD",
        "tv_header_name": "Gold Spot / U.S. Dollar",
        "cmd_keys": ["/xauusd", "xauusd", "/gold", "gold"],
        "tv_tickers": ["OANDA:XAUUSD", "FOREXCOM:XAUUSD", "FX_IDC:XAUUSD"],
        "tv_screener": "cfd",
        "binance_symbol": "PAXGUSDT",
        "yf_symbols": ["GC=F"],
        "currencies": ["USD"],
        "decimals": 2,
        "spread_and_cost": 0.35,         # Realistic spread + commission + slippage in price terms
        "unit_name": "Pts",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 2.80,
        "max_sl_dist": 12.15,            # Above $12.15 + $0.35 cost, 0.01 lot risks > $12.50 -> Skip!
        "min_lot": 0.01,
        "max_lot_cap": 0.05,
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
        "spread_and_cost": 28.00,        # Realistic BTCUSD spread + commission + slippage in price terms
        "unit_name": "Pts",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 160.0,
        "max_sl_dist": 1200.0,           # Above $1,200 + $28 cost, 0.01 lot risks > $12.50 -> Skip!
        "min_lot": 0.01,
        "max_lot_cap": 0.05,
        "is_crypto": True
    }
}

# Runtime State Tracking
last_candle_traded      = {name: None for name in FOREX_PAIRS}
last_pre_alert_candle   = {name: None for name in FOREX_PAIRS}
last_setup_traded       = {name: None for name in FOREX_PAIRS}
pending_pre_alerts      = {name: None for name in FOREX_PAIRS}
pair_cooldown_until     = {name: datetime.min.replace(tzinfo=timezone.utc) for name in FOREX_PAIRS}
pre_alert_cooldown      = {name: datetime.min.replace(tzinfo=timezone.utc) for name in FOREX_PAIRS}
live_bars_memory        = {name: {} for name in FOREX_PAIRS}
ohlc_history_cache      = {}

cached_news_events      = []
last_news_fetch_time    = 0
chart_rotation_index    = 0
last_update_id          = 0

trades_today            = 0
losses_today            = 0
daily_realized_pnl      = 0.0
weekly_realized_pnl     = 0.0
total_account_pnl       = 0.0

daily_trades_log        = []
journal_memory_log      = []
daily_halt_announced    = False
weekly_halt_announced   = False
dd_halt_announced       = False
pre_session_brief_sent  = None

def get_utc_now():
    return datetime.now(timezone.utc)

def get_ist_now():
    return get_utc_now() + timedelta(hours=5, minutes=30)

def get_ny_now():
    try:
        return datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return get_utc_now() - timedelta(hours=5)

def to_utc_timestamp(ts):
    if ts is None:
        return get_utc_now()
    if getattr(ts, "tzinfo", None) is not None:
        return ts.tz_convert(timezone.utc)
    return ts.replace(tzinfo=timezone.utc)

def get_ny_weekday_index(ts_utc):
    """Returns the weekday index (0=Monday ... 6=Sunday) in New York time so session days align accurately."""
    dt_utc = to_utc_timestamp(ts_utc)
    try:
        ny_dt = dt_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        ny_dt = dt_utc - timedelta(hours=5)
    return ny_dt.weekday()

def is_timestamp_in_ny_session(ts_utc):
    dt_utc = to_utc_timestamp(ts_utc)
    try:
        ny_dt = dt_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        ny_dt = dt_utc - timedelta(hours=5)
    ny_hr = ny_dt.hour + (ny_dt.minute / 60.0)
    return 9.25 <= ny_hr <= 12.25

current_day  = get_ist_now().date()
current_week = get_ist_now().isocalendar()[1]

# =====================================================================
# 2. NEW YORK SESSION & ECONOMIC CALENDAR ENGINE (PDF SECTION 3)
# =====================================================================
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

def get_ny_session_status():
    if not is_forex_market_open():
        return False, False, "Weekend Closed", ""

    ny_now = get_ny_now()
    ny_hr  = ny_now.hour + (ny_now.minute / 60.0)
    is_dst = bool(ny_now.dst())
    ist_window_str = "7:00 PM – 9:30 PM IST (EDT)" if is_dst else "8:00 PM – 10:30 PM IST (EST)"

    is_pre_session = (9.20 <= ny_hr < 9.50)
    is_ny_active   = (9.25 <= ny_hr <= 12.25)

    if is_ny_active:
        return True, is_pre_session, f"🟢 NY Session Active ({ist_window_str})", ist_window_str
    else:
        if STRICT_NY_SESSION_ONLY:
            return False, is_pre_session, f"⏸️ Waiting for NY Session ({ist_window_str})", ist_window_str
        return True, is_pre_session, f"🟢 24H Mode Active (NY Core: {ist_window_str})", ist_window_str

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
        if -15.0 <= diff_mins <= 15.0:
            return False, ev["title"]
    return True, "Clear"

# =====================================================================
# 3. $5,000 TWO-STEP RISK MANAGEMENT & JOURNAL ENGINE (PDF SEC 2 & 5)
# =====================================================================
def get_live_account_metrics(live_prices_map=None):
    global HIGH_WATER_MARK_USD
    closed_balance = STARTING_CAPITAL_USD + total_account_pnl
    if closed_balance > HIGH_WATER_MARK_USD:
        HIGH_WATER_MARK_USD = closed_balance

    floating_pnl = 0.0
    if live_prices_map:
        for t in daily_trades_log:
            if t["is_open"] and t["pair"] in live_prices_map:
                spot = live_prices_map[t["pair"]]
                if spot is not None and t["sl_dist"] > 0:
                    r_move = (spot - t["entry"]) / t["sl_dist"] if t["direction"] == "BUY" else (t["entry"] - spot) / t["sl_dist"]
                    floating_pnl += (r_move * t["max_risk"])

    live_equity = closed_balance + floating_pnl
    if live_equity > HIGH_WATER_MARK_USD:
        HIGH_WATER_MARK_USD = live_equity

    return closed_balance, live_equity, daily_realized_pnl + floating_pnl, weekly_realized_pnl + floating_pnl

def check_funded_account_risk_rules(live_prices_map=None):
    global daily_halt_announced, weekly_halt_announced, dd_halt_announced
    bal, eq, day_net_pnl, week_net_pnl = get_live_account_metrics(live_prices_map)

    reference_peak = max(STARTING_CAPITAL_USD, HIGH_WATER_MARK_USD)
    current_dd     = reference_peak - min(bal, eq)
    if current_dd >= MAX_DRAWDOWN_REVIEW_USD:
        if not dd_halt_announced:
            dd_halt_announced = True
            send_telegram_message(
                f"🛑 <b>DRAWDOWN REVIEW THRESHOLD REACHED (-${current_dd:.2f} / 2.0%)</b>\n"
                f"Peak/Starting Balance: <code>${reference_peak:.2f}</code> | Current Equity: <code>${eq:.2f}</code>\n"
                f"Per Roadmap Section 2: Trading is paused to review the journal and rule compliance."
            )
        return False, 0.0, "Max Drawdown Review Threshold (-$100)"

    if week_net_pnl <= -WEEKLY_STOP_LOSS_USD:
        if not weekly_halt_announced:
            weekly_halt_announced = True
            send_telegram_message(
                f"🛑 <b>WEEKLY STOP REACHED (-${abs(week_net_pnl):.2f} / 1.5%)</b>\n"
                f"Per Roadmap Section 2: Weekly loss reached -$75. Live/Evaluation trading stopped for the week."
            )
        return False, 0.0, "Weekly Stop Reached (-$75)"

    if day_net_pnl <= -DAILY_STOP_LOSS_USD or losses_today >= MAX_DAILY_LOSS_COUNT:
        if not daily_halt_announced:
            daily_halt_announced = True
            send_telegram_message(
                f"🛡️ <b>DAILY STOP TRIGGERED (Today: ${day_net_pnl:+.2f} | Losses: {losses_today}/2)</b>\n"
                f"Per Roadmap Section 2: Maximum daily stop (-$25 or 2 losses) reached. Done for today."
            )
        return False, 0.0, "Daily Stop Reached (-$25 / 2 Losses)"

    if trades_today >= MAX_DAILY_TRADES:
        return False, 0.0, "Max 2 Daily Trades Completed"

    remaining_daily_room = DAILY_STOP_LOSS_USD - abs(min(0.0, day_net_pnl))
    if remaining_daily_room < 8.0:
        return False, 0.0, "Insufficient Daily Risk Room"

    allowed_risk = min(RISK_PER_TRADE_USD, remaining_daily_room)
    return True, round(allowed_risk, 2), "Clear"

def calculate_roadmap_position_size(pair_name, pair_info, sl_dist, max_risk_usd=RISK_PER_TRADE_USD):
    if sl_dist <= 0:
        return None, 0.0, 0.0

    spread_cost_dist  = pair_info.get("spread_and_cost", 0.0)
    effective_sl_dist = sl_dist + spread_cost_dist

    if "XAU" in pair_name:
        loss_per_1_lot = effective_sl_dist * 100.0
        cost_per_1_lot = spread_cost_dist * 100.0
    elif "BTC" in pair_name:
        loss_per_1_lot = effective_sl_dist * 1.0
        cost_per_1_lot = spread_cost_dist * 1.0
    else:
        loss_per_1_lot = effective_sl_dist * 100000.0
        cost_per_1_lot = spread_cost_dist * 100000.0

    min_lot = pair_info.get("min_lot", 0.01)
    min_lot_risk_usd = loss_per_1_lot * min_lot

    if min_lot_risk_usd > (max_risk_usd + 0.25):
        return None, round(min_lot_risk_usd, 2), round(cost_per_1_lot * min_lot, 2)

    raw_lot  = max_risk_usd / loss_per_1_lot
    safe_lot = max(min_lot, min(round(raw_lot, 2), pair_info.get("max_lot_cap", 0.05)))
    actual_planned_risk = round(safe_lot * loss_per_1_lot, 2)

    if actual_planned_risk > (max_risk_usd + 0.25):
        safe_lot = round(safe_lot - 0.01, 2)
        if safe_lot < min_lot:
            return None, actual_planned_risk, round(cost_per_1_lot * min_lot, 2)
        actual_planned_risk = round(safe_lot * loss_per_1_lot, 2)

    cost_usd = round(safe_lot * cost_per_1_lot, 2)
    return safe_lot, actual_planned_risk, cost_usd

def append_to_trading_journal(trade_record, result_r_val, net_pnl_usd, result_label):
    wd_idx = get_ny_weekday_index(trade_record.get("entry_candle_time", get_utc_now()))
    wd_nam = WEEKDAY_NAMES[wd_idx]
    row = {
        "Date / time": get_ist_now().strftime("%Y-%m-%d %I:%M %p IST"),
        "Day": wd_nam,
        "Market": trade_record["mt5_name"],
        "1H bias": trade_record.get("h1_bias", "N/A"),
        "Level / sweep": trade_record.get("swept_level_name", "Key Level Sweep"),
        "5M confirmation": trade_record.get("mss_confirmation", "5M MSS + Close"),
        "Entry / SL / TP": f"E:{trade_record['entry']} | SL:{trade_record['sl']} | TP:{trade_record['target']}",
        "Risk $": f"${trade_record['max_risk']:.2f}",
        "Result (R)": f"{result_r_val:+.2f}R (${net_pnl_usd:+.2f}) [{result_label}]",
        "Rule followed?": "YES",
        "weekday_idx": wd_idx,
        "numeric_r": float(result_r_val),
        "numeric_pnl": float(net_pnl_usd),
        "cost_usd": float(trade_record.get("cost_usd", 0.35))
    }
    journal_memory_log.append(row)
    file_exists = os.path.isfile(JOURNAL_CSV_PATH)
    csv_cols = ["Date / time", "Day", "Market", "1H bias", "Level / sweep", "5M confirmation", "Entry / SL / TP", "Risk $", "Result (R)", "Rule followed?"]
    try:
        with open(JOURNAL_CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=csv_cols)
            if not file_exists:
                writer.writeheader()
            writer.writerow({k: row[k] for k in csv_cols})
    except Exception as e:
        print(f"Journal Write Warning: {e}")

# =====================================================================
# 4. ZERO-DELAY 1H + 15M + 5M DATA ENGINE (LIVE + DEEP BACKTEST FETCH)
# =====================================================================
def send_telegram_message(message, target_chat_id=CHAT_ID):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print(f"\n[TELEGRAM]:\n{message}\n")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": message, "parse_mode": "HTML"}
    for attempt in range(2):
        try:
            r = HTTP_SESSION.post(url, json=payload, timeout=8) if attempt == 0 else requests.post(url, json=payload, timeout=10)
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"Telegram Send Error ({attempt}): {e}")

def send_telegram_document(file_bytes, filename, caption_html="", target_chat_id=CHAT_ID):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument"
    for attempt in range(2):
        try:
            files = {"document": (filename, file_bytes, "text/csv")}
            data  = {"chat_id": target_chat_id, "caption": caption_html[:950], "parse_mode": "HTML"}
            r = HTTP_SESSION.post(url, data=data, files=files, timeout=15) if attempt == 0 else requests.post(url, data=data, files=files, timeout=18)
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"Document Send Warning ({attempt}): {e}")

def format_price(price, decimals):
    return f"{price:.{decimals}f}"

def format_tv_comma_price(price, decimals):
    return f"{price:,.{decimals}f}"

def fetch_tradingview_live_candle_and_spot(tv_tickers, screener="cfd"):
    if isinstance(tv_tickers, str):
        tv_tickers = [tv_tickers]
    url = f"https://scanner.tradingview.com/{screener}/scan?cb={time.time_ns()}"
    cols = ["close", "open|5", "high|5", "low|5", "close|5"]
    payload = {"symbols": {"tickers": tv_tickers, "query": {"types": []}}, "columns": cols}
    try:
        res = HTTP_SESSION.post(url, json=payload, timeout=3.5)
        if res.status_code == 200:
            data = res.json().get("data", [])
            for item in data:
                d = item.get("d", [])
                if d and len(d) >= 5 and d[0] is not None:
                    spot = float(d[0])
                    o5   = float(d[1]) if d[1] is not None else spot
                    h5   = float(d[2]) if d[2] is not None else max(spot, o5)
                    l5   = float(d[3]) if d[3] is not None else min(spot, o5)
                    c5   = float(d[4]) if d[4] is not None else spot
                    return {
                        "spot": spot,
                        "o5": o5, "h5": max(h5, o5, c5), "l5": min(l5, o5, c5), "c5": c5
                    }
    except Exception:
        pass
    return None

def fetch_binance_vision_ohlc(binance_symbol, interval_str="5m", limit=240):
    if not binance_symbol:
        return None
    for base_url in ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]:
        params = {"symbol": binance_symbol, "interval": interval_str, "limit": limit}
        try:
            r = HTTP_SESSION.get(base_url, params=params, timeout=5.5).json()
            if isinstance(r, list) and len(r) >= 50:
                df = pd.DataFrame(r, columns=["open_time", "Open", "High", "Low", "Close", "Volume", "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"])
                df["Date"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
                df.set_index("Date", inplace=True)
                for col in ["Open", "High", "Low", "Close", "Volume"]:
                    df[col] = df[col].astype(float)
                return df[["Open", "High", "Low", "Close", "Volume"]]
        except Exception:
            pass
    return None

def fetch_binance_paginated_5m_history(binance_symbol, total_bars=3000):
    if not binance_symbol:
        return None
    chunks = []
    end_time_ms = None
    bars_needed = total_bars

    for _ in range(max(1, (total_bars + 999) // 1000)):
        batch_limit = min(1000, bars_needed)
        fetched = None
        for base_url in ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]:
            params = {"symbol": binance_symbol, "interval": "5m", "limit": batch_limit}
            if end_time_ms is not None:
                params["endTime"] = end_time_ms
            try:
                r = HTTP_SESSION.get(base_url, params=params, timeout=6.0).json()
                if isinstance(r, list) and len(r) > 0:
                    fetched = r
                    break
            except Exception:
                pass
        if not fetched:
            break
        df_chunk = pd.DataFrame(fetched, columns=["open_time", "Open", "High", "Low", "Close", "Volume", "close_time", "q_vol", "trades", "tb_base", "tb_quote", "ignore"])
        df_chunk["Date"] = pd.to_datetime(df_chunk["open_time"], unit="ms", utc=True)
        df_chunk.set_index("Date", inplace=True)
        for col in ["Open", "High", "Low", "Close", "Volume"]:
            df_chunk[col] = df_chunk[col].astype(float)
        chunks.append(df_chunk[["Open", "High", "Low", "Close", "Volume"]])
        earliest_open_ms = int(fetched[0][0])
        end_time_ms = earliest_open_ms - 1
        bars_needed -= len(fetched)
        if len(fetched) < batch_limit or bars_needed <= 0:
            break

    if not chunks:
        return None
    full_df = pd.concat(chunks).sort_index()
    full_df = full_df[~full_df.index.duplicated(keep="last")]
    return sanitize_ohlc_dataframe(full_df, min_bars=100)

def sanitize_ohlc_dataframe(df, min_bars=40):
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

def get_roadmap_3tf_spot_data(pair_name, info, use_fast_cache=False):
    now_ts     = time.time()
    now_utc    = get_utc_now()
    bucket_min = (now_utc.minute // 5) * 5
    bucket_ts  = now_utc.replace(minute=bucket_min, second=0, microsecond=0)
    hour_ts    = now_utc.replace(minute=0, second=0, microsecond=0)

    raw_df_1h, raw_df_15m, raw_df_5m = None, None, None

    if use_fast_cache and pair_name in ohlc_history_cache:
        cached = ohlc_history_cache[pair_name]
        if (now_ts - cached["ts"]) <= 60.0 and cached.get("bucket_ts") == bucket_ts:
            raw_df_1h  = cached["raw_df_1h"].copy()
            raw_df_15m = cached["raw_df_15m"].copy()
            raw_df_5m  = cached["raw_df_5m"].copy()

    if raw_df_1h is None or raw_df_15m is None or raw_df_5m is None:
        if info.get("binance_symbol"):
            raw_df_1h  = fetch_binance_vision_ohlc(info["binance_symbol"], "1h", 160)
            raw_df_15m = fetch_binance_vision_ohlc(info["binance_symbol"], "15m", 220)
            raw_df_5m  = fetch_binance_vision_ohlc(info["binance_symbol"], "5m", 180)

        if raw_df_1h is None or raw_df_15m is None or raw_df_5m is None:
            for yf_sym in info.get("yf_symbols", []):
                try:
                    tkr   = yf.Ticker(yf_sym)
                    r_1h  = tkr.history(period="15d", interval="1h", auto_adjust=False)
                    r_15m = tkr.history(period="10d", interval="15m", auto_adjust=False)
                    r_5m  = tkr.history(period="5d", interval="5m", auto_adjust=False)
                    c_1h  = sanitize_ohlc_dataframe(r_1h, min_bars=40)
                    c_15m = sanitize_ohlc_dataframe(r_15m, min_bars=50)
                    c_5m  = sanitize_ohlc_dataframe(r_5m, min_bars=50)
                    if c_1h is not None and c_15m is not None and c_5m is not None:
                        raw_df_1h, raw_df_15m, raw_df_5m = c_1h, c_15m, c_5m
                        break
                except Exception:
                    continue

        raw_df_1h  = sanitize_ohlc_dataframe(raw_df_1h, min_bars=40)
        raw_df_15m = sanitize_ohlc_dataframe(raw_df_15m, min_bars=50)
        raw_df_5m  = sanitize_ohlc_dataframe(raw_df_5m, min_bars=50)
        if raw_df_1h is None or raw_df_15m is None or raw_df_5m is None:
            return None, None, None, None

        ohlc_history_cache[pair_name] = {
            "ts": now_ts,
            "bucket_ts": bucket_ts,
            "raw_df_1h": raw_df_1h.copy(),
            "raw_df_15m": raw_df_15m.copy(),
            "raw_df_5m": raw_df_5m.copy()
        }

    df_1h  = raw_df_1h.copy()
    df_15m = raw_df_15m.copy()
    df_5m  = raw_df_5m.copy()

    tv_data = fetch_tradingview_live_candle_and_spot(info.get("tv_tickers", []), info.get("tv_screener", "cfd"))

    if tv_data is not None and tv_data["spot"] > 0:
        tv_spot = tv_data["spot"]
        mem     = live_bars_memory[pair_name]

        raw_open_5m = float(df_5m.iloc[-1]["Open"])
        if mem.get("hour_ts") != hour_ts:
            fixed_basis = tv_data["o5"] - raw_open_5m
            if abs(fixed_basis) > (tv_spot * 0.05):
                fixed_basis = 0.0
            mem["hour_ts"]     = hour_ts
            mem["fixed_basis"] = fixed_basis
        else:
            fixed_basis = mem.get("fixed_basis", 0.0)

        if abs(fixed_basis) > 0:
            for col in ["Open", "High", "Low", "Close"]:
                df_5m[col]  = df_5m[col] + fixed_basis
                df_15m[col] = df_15m[col] + fixed_basis
                df_1h[col]  = df_1h[col] + fixed_basis

        if info.get("is_crypto", False) or is_forex_market_open():
            last_idx_5m = df_5m.index[-1]
            df_5m.loc[last_idx_5m, "Open"]  = tv_data["o5"]
            df_5m.loc[last_idx_5m, "High"]  = max(tv_data["h5"], tv_spot)
            df_5m.loc[last_idx_5m, "Low"]   = min(tv_data["l5"], tv_spot)
            df_5m.loc[last_idx_5m, "Close"] = tv_spot

            last_idx_15m = df_15m.index[-1]
            df_15m.loc[last_idx_15m, "Close"] = tv_spot
            df_15m.loc[last_idx_15m, "High"]  = max(float(df_15m.loc[last_idx_15m, "High"]), tv_spot)
            df_15m.loc[last_idx_15m, "Low"]   = min(float(df_15m.loc[last_idx_15m, "Low"]), tv_spot)

            last_idx_1h = df_1h.index[-1]
            df_1h.loc[last_idx_1h, "Close"] = tv_spot
            df_1h.loc[last_idx_1h, "High"]  = max(float(df_1h.loc[last_idx_1h, "High"]), tv_spot)
            df_1h.loc[last_idx_1h, "Low"]   = min(float(df_1h.loc[last_idx_1h, "Low"]), tv_spot)

        live_spot_price = tv_spot
    else:
        live_spot_price = float(df_5m.iloc[-1]["Close"])

    return df_1h, df_15m, df_5m, live_spot_price

# =====================================================================
# 5. PRE-SESSION KEY LEVELS & 1H CONTEXT ENGINE (PDF SECTION 1)
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
    return df

def extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, decimals):
    closed_1h = df_1h.iloc[:-1].copy() if len(df_1h) > 25 else df_1h.copy()
    atr_1h    = float(closed_1h.iloc[-1]["ATR"])
    if np.isnan(atr_1h) or atr_1h <= 0:
        atr_1h = float((closed_1h["High"] - closed_1h["Low"]).mean())

    ref_ts    = to_utc_timestamp(df_15m.index[-1])
    today_utc = ref_ts.date()

    df_15m_utc = df_15m.iloc[-140:].copy()
    df_15m_utc["utc_date"] = [to_utc_timestamp(ts).date() for ts in df_15m_utc.index]
    df_15m_utc["utc_hour"] = [to_utc_timestamp(ts).hour + to_utc_timestamp(ts).minute / 60.0 for ts in df_15m_utc.index]

    unique_dates = sorted(df_15m_utc["utc_date"].unique())
    prev_dates   = [d for d in unique_dates if d < today_utc]
    prev_day     = prev_dates[-1] if prev_dates else unique_dates[0]

    prev_day_bars = df_15m_utc[df_15m_utc["utc_date"] == prev_day]
    pdh_price = round(float(prev_day_bars["High"].max()), decimals) if not prev_day_bars.empty else round(float(closed_1h.iloc[-24:]["High"].max()), decimals)
    pdl_price = round(float(prev_day_bars["Low"].min()), decimals)  if not prev_day_bars.empty else round(float(closed_1h.iloc[-24:]["Low"].min()), decimals)

    today_bars  = df_15m_utc[df_15m_utc["utc_date"] == today_utc]
    asian_bars  = today_bars[(today_bars["utc_hour"] >= 0.0) & (today_bars["utc_hour"] < 7.0)]
    london_bars = today_bars[(today_bars["utc_hour"] >= 7.0) & (today_bars["utc_hour"] < 13.25)]

    if asian_bars.empty:
        asian_bars = prev_day_bars[(prev_day_bars["utc_hour"] >= 0.0) & (prev_day_bars["utc_hour"] < 7.0)]
    if london_bars.empty:
        london_bars = prev_day_bars[(prev_day_bars["utc_hour"] >= 7.0) & (prev_day_bars["utc_hour"] < 13.25)]

    asian_high  = round(float(asian_bars["High"].max()), decimals)  if not asian_bars.empty else pdh_price
    asian_low   = round(float(asian_bars["Low"].min()), decimals)   if not asian_bars.empty else pdl_price
    london_high = round(float(london_bars["High"].max()), decimals) if not london_bars.empty else pdh_price
    london_low  = round(float(london_bars["Low"].min()), decimals)  if not london_bars.empty else pdl_price

    highs_1h = closed_1h["High"].values.astype(float)
    lows_1h  = closed_1h["Low"].values.astype(float)
    n_1h     = len(closed_1h)

    swing_highs_1h, swing_lows_1h = [], []
    for i in range(2, n_1h - 1):
        if highs_1h[i] >= max(highs_1h[i - 2:i]) and highs_1h[i] >= highs_1h[i + 1]:
            swing_highs_1h.append(round(float(highs_1h[i]), decimals))
        if lows_1h[i] <= min(lows_1h[i - 2:i]) and lows_1h[i] <= lows_1h[i + 1]:
            swing_lows_1h.append(round(float(lows_1h[i]), decimals))

    c_last   = closed_1h.iloc[-1]
    h1_close = float(c_last["Close"])
    h1_ema20 = float(c_last["EMA20"])
    h1_ema50 = float(c_last["EMA50"])

    recent_8_1h = closed_1h.iloc[-8:]
    span_8_1h   = float(recent_8_1h["High"].max() - recent_8_1h["Low"].min())
    ema_gap     = abs(h1_ema20 - h1_ema50)

    if span_8_1h < (atr_1h * 1.40) and ema_gap < (atr_1h * 0.12):
        h1_bias = "RANGING"
    else:
        hh_hl = (len(swing_highs_1h) >= 2 and len(swing_lows_1h) >= 2 and swing_highs_1h[-1] > swing_highs_1h[-2] - atr_1h * 0.15 and swing_lows_1h[-1] >= swing_lows_1h[-2] - atr_1h * 0.10)
        lh_ll = (len(swing_highs_1h) >= 2 and len(swing_lows_1h) >= 2 and swing_highs_1h[-1] <= swing_highs_1h[-2] + atr_1h * 0.10 and swing_lows_1h[-1] < swing_lows_1h[-2] + atr_1h * 0.15)

        if (h1_close > h1_ema20 and h1_ema20 >= h1_ema50) or (hh_hl and h1_close >= h1_ema50):
            h1_bias = "BULLISH"
        elif (h1_close < h1_ema20 and h1_ema20 <= h1_ema50) or (lh_ll and h1_close <= h1_ema50):
            h1_bias = "BEARISH"
        elif abs(h1_close - h1_ema20) <= (atr_1h * 0.16) and ema_gap <= (atr_1h * 0.16):
            h1_bias = "RANGING"
        else:
            h1_bias = "BULLISH" if h1_close >= h1_ema20 else "BEARISH"

    raw_levels = [
        {"name": "PDH (Prev Day High)", "short": "PDH", "price": pdh_price, "type": "HIGH_POOL", "color": "#b91c1c"},
        {"name": "PDL (Prev Day Low)",  "short": "PDL", "price": pdl_price, "type": "LOW_POOL",  "color": "#047857"},
        {"name": "London High",         "short": "LON H", "price": london_high, "type": "HIGH_POOL", "color": "#dc2626"},
        {"name": "London Low",          "short": "LON L", "price": london_low,  "type": "LOW_POOL",  "color": "#059669"},
        {"name": "Asian High",          "short": "ASIA H", "price": asian_high, "type": "HIGH_POOL", "color": "#7c3aed"},
        {"name": "Asian Low",           "short": "ASIA L", "price": asian_low,  "type": "LOW_POOL",  "color": "#2563eb"}
    ]

    if swing_highs_1h:
        sh_last = swing_highs_1h[-1]
        is_eqh  = any(abs(sh_last - prev_sh) <= atr_1h * 0.12 for prev_sh in swing_highs_1h[:-1])
        lbl_h   = "1H EQH (Equal Highs)" if is_eqh else "1H Swing High"
        raw_levels.append({"name": lbl_h, "short": "1H EQH" if is_eqh else "1H SH", "price": sh_last, "type": "HIGH_POOL", "color": "#1e293b"})

    if swing_lows_1h:
        sl_last = swing_lows_1h[-1]
        is_eql  = any(abs(sl_last - prev_sl) <= atr_1h * 0.12 for prev_sl in swing_lows_1h[:-1])
        lbl_l   = "1H EQL (Equal Lows)" if is_eql else "1H Swing Low"
        raw_levels.append({"name": lbl_l, "short": "1H EQL" if is_eql else "1H SL", "price": sl_last, "type": "LOW_POOL", "color": "#1e293b"})

    merged_levels = []
    for lv in raw_levels:
        merged = False
        for m in merged_levels:
            if abs(lv["price"] - m["price"]) <= (atr_1h * 0.08) and lv["type"] == m["type"]:
                if lv["short"] not in m["short"]:
                    m["short"] = f"{m['short']}/{lv['short']}"
                    m["name"]  = f"{m['name']} + {lv['name']}"
                merged = True
                break
        if not merged:
            merged_levels.append(lv.copy())

    zone_half_thickness = round(atr_1h * 0.08, decimals)
    for m in merged_levels:
        m["top"] = round(m["price"] + zone_half_thickness, decimals)
        m["bot"] = round(m["price"] - zone_half_thickness, decimals)

    return {
        "h1_bias": h1_bias,
        "atr_1h": atr_1h,
        "pdh": pdh_price,
        "pdl": pdl_price,
        "london_high": london_high,
        "london_low": london_low,
        "asian_high": asian_high,
        "asian_low": asian_low,
        "levels": merged_levels
    }

# =====================================================================
# 6. ONE TESTED SETUP ENGINE: 15M SWEEP + 5M MSS + FVG/PULLBACK (PDF SEC 1)
# =====================================================================
def detect_5m_fvg_zone(df_5m, direction, lookback=6):
    n = len(df_5m)
    if n < lookback + 3:
        return None
    for idx in range(n - 2, n - 2 - lookback, -1):
        c_curr  = df_5m.iloc[idx]
        c_prev2 = df_5m.iloc[idx - 2]
        if direction == "BUY":
            gap_bot = float(c_prev2["High"])
            gap_top = float(c_curr["Low"])
            if gap_top > gap_bot:
                return {"top": gap_top, "bot": gap_bot, "mid": (gap_top + gap_bot) / 2.0}
        else:
            gap_top = float(c_prev2["Low"])
            gap_bot = float(c_curr["High"])
            if gap_top > gap_bot:
                return {"top": gap_top, "bot": gap_bot, "mid": (gap_top + gap_bot) / 2.0}
    return None

def evaluate_roadmap_sweep_mss_setup(pair_name, info, df_1h, df_15m, df_5m, live_spot, ignore_locks=False, return_skip_reason=False):
    if not ignore_locks:
        ny_ok, _, _, _ = get_ny_session_status()
        if not ny_ok:
            return (None, "OUTSIDE_NY_SESSION") if return_skip_reason else None
        news_ok, _ = check_news_window_for_pair(info)
        if not news_ok:
            return (None, "HIGH_IMPACT_NEWS") if return_skip_reason else None
        if get_utc_now() < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
            return (None, "COOLDOWN") if return_skip_reason else None

    dec     = info["decimals"]
    roadmap = extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, dec)
    h1_bias = roadmap["h1_bias"]
    levels  = roadmap["levels"]

    if h1_bias == "RANGING" and not ignore_locks:
        return (None, "1H_RANGING_CHOPPY") if return_skip_reason else None

    m15_recent = df_15m.iloc[-6:-1] if len(df_15m) >= 7 else df_15m.iloc[-5:]
    m15_c0     = df_15m.iloc[-2] if len(df_15m) >= 2 else df_15m.iloc[-1]

    m5_c0 = df_5m.iloc[-2]
    m5_c1 = df_5m.iloc[-3]
    m5_c2 = df_5m.iloc[-4]
    m5_c3 = df_5m.iloc[-5]

    p_close = float(m5_c0["Close"])
    p_open  = float(m5_c0["Open"])
    p_high  = float(m5_c0["High"])
    p_low   = float(m5_c0["Low"])
    atr_5m  = float(m5_c0["ATR"])
    body_r  = float(m5_c0["Body_Ratio"])
    close_p = float(m5_c0["Close_Pos"])
    rng_5   = float(m5_c0["Range"])

    if np.isnan(atr_5m) or atr_5m <= 0:
        return (None, "INVALID_ATR") if return_skip_reason else None

    if rng_5 > (atr_5m * 1.85):
        return (None, "EXTENDED_CLIMAX_CANDLE") if return_skip_reason else None

    candidates = []
    skipped_for_risk = False
    buf_5m = round(max(atr_5m * 0.12, info["spread_and_cost"] * 0.5), dec)

    for lv in levels:
        lvl_price = lv["price"]
        lvl_name  = lv["name"]
        lvl_short = lv["short"]
        lvl_type  = lv["type"]

        # BULLISH SETUP (1H BULLISH + LOW_POOL SWEEP + 5M MSS UP)
        if (h1_bias == "BULLISH" or ignore_locks) and lvl_type == "LOW_POOL":
            m15_min_low    = float(m15_recent["Low"].min())
            m15_last_close = float(m15_c0["Close"])
            swept_on_15m   = (m15_min_low <= lvl_price + atr_5m * 0.25) and (m15_min_low >= lvl_price - atr_5m * 2.20) and (m15_last_close >= lvl_price - atr_5m * 0.10)

            if swept_on_15m:
                mss_neck_high = max(float(m5_c1["High"]), float(m5_c2["High"]), float(m5_c3["High"]))
                sweep_low_5m  = min(float(df_5m.iloc[-8:-1]["Low"].min()), m15_min_low)

                mss_body_confirmed = (
                    (p_close > mss_neck_high) and
                    (p_close > p_open) and
                    (body_r >= 0.45) and
                    (close_p >= 0.62) and
                    (live_spot >= lvl_price)
                )
                mss_pre_forming = (
                    (not mss_body_confirmed) and
                    (p_close > lvl_price) and
                    (p_close > p_open) and
                    (body_r >= 0.40) and
                    ((mss_neck_high + buf_5m - p_close) <= atr_5m * 0.85) and
                    (live_spot < mss_neck_high + buf_5m)
                )

                if mss_body_confirmed or mss_pre_forming:
                    fvg_zone = detect_5m_fvg_zone(df_5m, "BUY", lookback=5)
                    if mss_body_confirmed:
                        entry_spot = round(p_close, dec)
                        mss_desc   = "5M Bullish MSS Body Close + FVG" if fvg_zone else "5M Bullish MSS Body Close"
                        stage_type = "CONFIRMED_BREAKOUT"
                    else:
                        entry_spot = round(mss_neck_high + buf_5m, dec)
                        mss_desc   = "5M Bullish MSS Trigger Forming"
                        stage_type = "PRE_ALERT"

                    invalidation_sl = round(min(sweep_low_5m, lvl_price) - (atr_5m * 0.22), dec)
                    sl_dist         = round(abs(entry_spot - invalidation_sl), dec)

                    if sl_dist < info["min_sl_dist"]:
                        sl_dist = info["min_sl_dist"]
                        invalidation_sl = round(entry_spot - sl_dist, dec)

                    lot_size, planned_risk_usd, cost_usd = calculate_roadmap_position_size(pair_name, info, sl_dist, RISK_PER_TRADE_USD)
                    if lot_size is None or sl_dist > info["max_sl_dist"]:
                        skipped_for_risk = True
                    else:
                        tp1_price = round(entry_spot + (sl_dist * BREAKEVEN_LOCK_R), dec)
                        tp2_price = round(entry_spot + (sl_dist * PRIMARY_RR_TARGET), dec)
                        tp2_dist  = round(sl_dist * PRIMARY_RR_TARGET, dec)

                        candidates.append({
                            "stage": stage_type,
                            "direction": "BUY",
                            "action": "BUY (LONG) 🟢",
                            "h1_bias": h1_bias,
                            "swept_level_name": f"{lvl_short} ({format_price(lvl_price, dec)})",
                            "swept_level_short": lvl_short,
                            "swept_level_price": lvl_price,
                            "mss_confirmation": mss_desc,
                            "short_tag": f"{lvl_short} Sweep + {mss_desc}",
                            "setup_id": f"BUY_{lvl_short}_{lvl_price}",
                            "entry_spot": entry_spot,
                            "sl_price": invalidation_sl,
                            "sl_dist": sl_dist,
                            "lock_level": tp1_price,
                            "tp1_price": tp1_price,
                            "tp2_price": tp2_price,
                            "tp2_dist": tp2_dist,
                            "lot_size": lot_size,
                            "planned_risk_usd": planned_risk_usd,
                            "cost_usd": cost_usd,
                            "fvg_zone": fvg_zone,
                            "rr": PRIMARY_RR_TARGET,
                            "score": 90 if mss_body_confirmed else 84,
                            "why_text": (
                                f"1️⃣ <b>1H Context:</b> <b>{h1_bias}</b> structure aligned.\n"
                                f"2️⃣ <b>15M Level Sweep:</b> Swept <b>{lvl_name} (<code>{format_price(lvl_price, dec)}</code>)</b> and closed back above.\n"
                                f"3️⃣ <b>5M Confirmation:</b> <b>{mss_desc}</b> with invalidation SL below sweep low (<code>{format_price(invalidation_sl, dec)}</code>)."
                            )
                        })

        # BEARISH SETUP (1H BEARISH + HIGH_POOL SWEEP + 5M MSS DOWN)
        if (h1_bias == "BEARISH" or ignore_locks) and lvl_type == "HIGH_POOL":
            m15_max_high   = float(m15_recent["High"].max())
            m15_last_close = float(m15_c0["Close"])
            swept_on_15m   = (m15_max_high >= lvl_price - atr_5m * 0.25) and (m15_max_high <= lvl_price + atr_5m * 2.20) and (m15_last_close <= lvl_price + atr_5m * 0.10)

            if swept_on_15m:
                mss_neck_low  = min(float(m5_c1["Low"]), float(m5_c2["Low"]), float(m5_c3["Low"]))
                sweep_high_5m = max(float(df_5m.iloc[-8:-1]["High"].max()), m15_max_high)

                mss_body_confirmed = (
                    (p_close < mss_neck_low) and
                    (p_close < p_open) and
                    (body_r >= 0.45) and
                    (close_p <= 0.38) and
                    (live_spot <= lvl_price)
                )
                mss_pre_forming = (
                    (not mss_body_confirmed) and
                    (p_close < lvl_price) and
                    (p_close < p_open) and
                    (body_r >= 0.40) and
                    ((p_close - (mss_neck_low - buf_5m)) <= atr_5m * 0.85) and
                    (live_spot > mss_neck_low - buf_5m)
                )

                if mss_body_confirmed or mss_pre_forming:
                    fvg_zone = detect_5m_fvg_zone(df_5m, "SELL", lookback=5)
                    if mss_body_confirmed:
                        entry_spot = round(p_close, dec)
                        mss_desc   = "5M Bearish MSS Body Close + FVG" if fvg_zone else "5M Bearish MSS Body Close"
                        stage_type = "CONFIRMED_BREAKOUT"
                    else:
                        entry_spot = round(mss_neck_low - buf_5m, dec)
                        mss_desc   = "5M Bearish MSS Trigger Forming"
                        stage_type = "PRE_ALERT"

                    invalidation_sl = round(max(sweep_high_5m, lvl_price) + (atr_5m * 0.22), dec)
                    sl_dist         = round(abs(invalidation_sl - entry_spot), dec)

                    if sl_dist < info["min_sl_dist"]:
                        sl_dist = info["min_sl_dist"]
                        invalidation_sl = round(entry_spot + sl_dist, dec)

                    lot_size, planned_risk_usd, cost_usd = calculate_roadmap_position_size(pair_name, info, sl_dist, RISK_PER_TRADE_USD)
                    if lot_size is None or sl_dist > info["max_sl_dist"]:
                        skipped_for_risk = True
                    else:
                        tp1_price = round(entry_spot - (sl_dist * BREAKEVEN_LOCK_R), dec)
                        tp2_price = round(entry_spot - (sl_dist * PRIMARY_RR_TARGET), dec)
                        tp2_dist  = round(sl_dist * PRIMARY_RR_TARGET, dec)

                        candidates.append({
                            "stage": stage_type,
                            "direction": "SELL",
                            "action": "SELL (SHORT) 🔴",
                            "h1_bias": h1_bias,
                            "swept_level_name": f"{lvl_short} ({format_price(lvl_price, dec)})",
                            "swept_level_short": lvl_short,
                            "swept_level_price": lvl_price,
                            "mss_confirmation": mss_desc,
                            "short_tag": f"{lvl_short} Sweep + {mss_desc}",
                            "setup_id": f"SELL_{lvl_short}_{lvl_price}",
                            "entry_spot": entry_spot,
                            "sl_price": invalidation_sl,
                            "sl_dist": sl_dist,
                            "lock_level": tp1_price,
                            "tp1_price": tp1_price,
                            "tp2_price": tp2_price,
                            "tp2_dist": tp2_dist,
                            "lot_size": lot_size,
                            "planned_risk_usd": planned_risk_usd,
                            "cost_usd": cost_usd,
                            "fvg_zone": fvg_zone,
                            "rr": PRIMARY_RR_TARGET,
                            "score": 90 if mss_body_confirmed else 84,
                            "why_text": (
                                f"1️⃣ <b>1H Context:</b> <b>{h1_bias}</b> structure aligned.\n"
                                f"2️⃣ <b>15M Level Sweep:</b> Swept <b>{lvl_name} (<code>{format_price(lvl_price, dec)}</code>)</b> and closed back below.\n"
                                f"3️⃣ <b>5M Confirmation:</b> <b>{mss_desc}</b> with invalidation SL above sweep high (<code>{format_price(invalidation_sl, dec)}</code>)."
                            )
                        })

    if not candidates:
        reason = "SKIPPED_SL_EXCEEDS_12_50_RISK" if skipped_for_risk else "NO_SETUP"
        return (None, reason) if return_skip_reason else None

    candidates.sort(key=lambda x: (1 if x["stage"] == "CONFIRMED_BREAKOUT" else 0, x["score"]), reverse=True)

    for cand in candidates:
        if not ignore_locks:
            if last_setup_traded.get(pair_name) == cand["setup_id"] and cand["stage"] == "PRE_ALERT":
                continue
        return (cand, "OK") if return_skip_reason else cand

    return (None, "LOCKED") if return_skip_reason else None

# =====================================================================
# 7. ALL-IN-ONE BACKTEST + DAY-WISE (MON–FRI) ANALYTICS ENGINE
# =====================================================================
def simulate_single_pair_backtest(pair_name, info, ny_session_only=False):
    df_5m = fetch_binance_paginated_5m_history(info.get("binance_symbol"), total_bars=2600)
    if df_5m is None or len(df_5m) < 250:
        for yf_sym in info.get("yf_symbols", []):
            try:
                tkr  = yf.Ticker(yf_sym)
                r_5m = tkr.history(period="30d", interval="5m", auto_adjust=False)
                c_5m = sanitize_ohlc_dataframe(r_5m, min_bars=250)
                if c_5m is not None:
                    df_5m = c_5m
                    break
            except Exception:
                continue

    if df_5m is None or len(df_5m) < 250:
        return None

    tv_data = fetch_tradingview_live_candle_and_spot(info.get("tv_tickers", []), info.get("tv_screener", "cfd"))
    if tv_data is not None and tv_data["spot"] > 0:
        shift = tv_data["spot"] - float(df_5m.iloc[-1]["Close"])
        if abs(shift) <= (tv_data["spot"] * 0.05):
            for col in ["Open", "High", "Low", "Close"]:
                df_5m[col] = df_5m[col] + shift

    df_15m = df_5m.resample("15min").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()
    df_1h  = df_5m.resample("1h").agg({"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}).dropna()

    df_5m  = enrich_price_action_metrics(df_5m)
    df_15m = enrich_price_action_metrics(df_15m)
    df_1h  = enrich_price_action_metrics(df_1h)

    dec          = info["decimals"]
    trades_list  = []
    skipped_risk = 0
    skipped_chop = 0

    idx_5m  = 180
    n_5m    = len(df_5m)
    day_trades_map = {}
    day_losses_map = {}

    while idx_5m < (n_5m - 20):
        curr_ts  = df_5m.index[idx_5m]
        curr_day = to_utc_timestamp(curr_ts).date()

        if ny_session_only and not is_timestamp_in_ny_session(curr_ts):
            idx_5m += 1
            continue

        if day_trades_map.get(curr_day, 0) >= MAX_DAILY_TRADES or day_losses_map.get(curr_day, 0) >= MAX_DAILY_LOSS_COUNT:
            idx_5m += 1
            continue

        sub_5m  = df_5m.iloc[max(0, idx_5m - 90) : idx_5m + 1]
        sub_15m = df_15m[df_15m.index <= curr_ts].iloc[-90:]
        sub_1h  = df_1h[df_1h.index <= curr_ts].iloc[-60:]

        if len(sub_15m) < 25 or len(sub_1h) < 20:
            idx_5m += 1
            continue

        spot_now = float(sub_5m.iloc[-1]["Close"])
        cand, reason = evaluate_roadmap_sweep_mss_setup(
            pair_name, info, sub_1h, sub_15m, sub_5m, spot_now,
            ignore_locks=True, return_skip_reason=True
        )

        if reason == "SKIPPED_SL_EXCEEDS_12_50_RISK":
            skipped_risk += 1
            idx_5m += 3
            continue

        if cand is None or cand["stage"] != "CONFIRMED_BREAKOUT":
            idx_5m += 1
            continue

        if cand["h1_bias"] == "RANGING":
            skipped_chop += 1
            idx_5m += 3
            continue

        direction  = cand["direction"]
        entry_p    = cand["entry_spot"]
        sl_p       = cand["sl_price"]
        tp1_p      = cand["tp1_price"]
        tp2_p      = cand["tp2_price"]
        risk_usd   = cand["planned_risk_usd"]
        cost_usd   = cand["cost_usd"]
        reward_usd = round((risk_usd * PRIMARY_RR_TARGET) - cost_usd, 2)

        trailed_be  = False
        outcome_lbl = "TIME_EXIT"
        net_r       = 0.0
        net_pnl     = -cost_usd
        exit_ts     = df_5m.index[min(idx_5m + 18, n_5m - 1)]

        for f_idx in range(idx_5m + 1, min(idx_5m + 19, n_5m)):
            f_bar   = df_5m.iloc[f_idx]
            f_hi    = float(f_bar["High"])
            f_lo    = float(f_bar["Low"])
            exit_ts = df_5m.index[f_idx]

            if direction == "BUY":
                if f_lo <= sl_p:
                    if trailed_be:
                        outcome_lbl = "BREAKEVEN (0R)"
                        net_r       = 0.0
                        net_pnl     = -cost_usd
                    else:
                        outcome_lbl = "LOSS (-1R)"
                        net_r       = -1.0
                        net_pnl     = -risk_usd
                    idx_5m = f_idx + 3
                    break
                if f_hi >= tp2_p:
                    outcome_lbl = "WIN (+2R)"
                    net_r       = +2.0
                    net_pnl     = reward_usd
                    idx_5m = f_idx + 3
                    break
                if not trailed_be and f_hi >= tp1_p:
                    trailed_be = True
                    sl_p       = entry_p
            else:
                if f_hi >= sl_p:
                    if trailed_be:
                        outcome_lbl = "BREAKEVEN (0R)"
                        net_r       = 0.0
                        net_pnl     = -cost_usd
                    else:
                        outcome_lbl = "LOSS (-1R)"
                        net_r       = -1.0
                        net_pnl     = -risk_usd
                    idx_5m = f_idx + 3
                    break
                if f_lo <= tp2_p:
                    outcome_lbl = "WIN (+2R)"
                    net_r       = +2.0
                    net_pnl     = reward_usd
                    idx_5m = f_idx + 3
                    break
                if not trailed_be and f_lo <= tp1_p:
                    trailed_be = True
                    sl_p       = entry_p
        else:
            last_c      = float(df_5m.iloc[min(idx_5m + 18, n_5m - 1)]["Close"])
            raw_r       = (last_c - entry_p) / cand["sl_dist"] if direction == "BUY" else (entry_p - last_c) / cand["sl_dist"]
            net_r       = round(max(-1.0, min(2.0, raw_r)), 2)
            net_pnl     = round((net_r * (risk_usd - cost_usd)) - cost_usd, 2)
            outcome_lbl = f"TIME_EXIT ({net_r:+.1f}R)"
            idx_5m += 18

        day_trades_map[curr_day] = day_trades_map.get(curr_day, 0) + 1
        if net_r < -0.5:
            day_losses_map[curr_day] = day_losses_map.get(curr_day, 0) + 1

        wd_idx = get_ny_weekday_index(curr_ts)

        trades_list.append({
            "timestamp": to_utc_timestamp(curr_ts) + timedelta(hours=5, minutes=30),
            "exit_timestamp": to_utc_timestamp(exit_ts) + timedelta(hours=5, minutes=30),
            "weekday_idx": wd_idx,
            "day_name": WEEKDAY_NAMES[wd_idx],
            "day_short": WEEKDAY_SHORT[wd_idx],
            "market": info["mt5_name"],
            "direction": direction,
            "h1_bias": cand["h1_bias"],
            "level_swept": cand["swept_level_short"],
            "level_full": cand["swept_level_name"],
            "mss_confirm": cand["mss_confirmation"],
            "entry": format_price(entry_p, dec),
            "sl": format_price(cand["sl_price"], dec),
            "tp": format_price(tp2_p, dec),
            "lot": cand["lot_size"],
            "risk_usd": risk_usd,
            "cost_usd": cost_usd,
            "result_r": net_r,
            "net_pnl": round(net_pnl, 2),
            "outcome": outcome_lbl,
            "in_ny_window": is_timestamp_in_ny_session(curr_ts)
        })

    return {
        "market": info["mt5_name"],
        "bars_tested": n_5m,
        "days_tested": round(n_5m / 288.0, 1),
        "trades": trades_list,
        "skipped_risk": skipped_risk,
        "skipped_chop": skipped_chop
    }

def compute_stage6_metrics(trades_list):
    total = len(trades_list)
    if total == 0:
        return {
            "total": 0, "wins": 0, "losses": 0, "breakevens": 0,
            "win_rate": 0.0, "decisive_wr": 0.0, "non_loss_rate": 0.0,
            "avg_win_usd": 0.0, "avg_loss_usd": 0.0,
            "avg_r": 0.0, "expectancy_usd": 0.0, "profit_factor": 0.0,
            "net_pnl_usd": 0.0, "total_costs_usd": 0.0, "max_dd_usd": 0.0, "max_dd_pct": 0.0,
            "final_equity": STARTING_CAPITAL_USD, "equity_curve": [STARTING_CAPITAL_USD], "dd_curve": [0.0]
        }

    wins       = [t for t in trades_list if t["net_pnl"] > 1.0]
    losses     = [t for t in trades_list if t["net_pnl"] < -2.0]
    breakevens = [t for t in trades_list if -2.0 <= t["net_pnl"] <= 1.0]

    win_cnt  = len(wins)
    loss_cnt = len(losses)
    be_cnt   = len(breakevens)

    decisive_cnt  = win_cnt + loss_cnt
    win_rate      = (win_cnt / total) * 100.0
    decisive_wr   = (win_cnt / decisive_cnt) * 100.0 if decisive_cnt > 0 else 0.0
    non_loss_rate = ((win_cnt + be_cnt) / total) * 100.0

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss   = abs(sum(t["net_pnl"] for t in losses)) + abs(sum(t["net_pnl"] for t in breakevens if t["net_pnl"] < 0))
    net_pnl_usd  = sum(t["net_pnl"] for t in trades_list)
    total_costs  = sum(t["cost_usd"] for t in trades_list)

    avg_win_usd    = (gross_profit / win_cnt) if win_cnt > 0 else 0.0
    avg_loss_usd   = (abs(sum(t["net_pnl"] for t in losses)) / loss_cnt) if loss_cnt > 0 else 0.0
    avg_r          = sum(t["result_r"] for t in trades_list) / total
    expectancy_usd = net_pnl_usd / total
    profit_factor  = (gross_profit / gross_loss) if gross_loss > 0 else (99.9 if gross_profit > 0 else 0.0)

    eq_curve = [STARTING_CAPITAL_USD]
    dd_curve = [0.0]
    peak_eq  = STARTING_CAPITAL_USD
    max_dd   = 0.0

    curr_eq = STARTING_CAPITAL_USD
    for t in trades_list:
        curr_eq += t["net_pnl"]
        eq_curve.append(round(curr_eq, 2))
        if curr_eq > peak_eq:
            peak_eq = curr_eq
        dd_now = peak_eq - curr_eq
        dd_curve.append(round(dd_now, 2))
        if dd_now > max_dd:
            max_dd = dd_now

    max_dd_pct = (max_dd / STARTING_CAPITAL_USD) * 100.0

    return {
        "total": total,
        "wins": win_cnt,
        "losses": loss_cnt,
        "breakevens": be_cnt,
        "win_rate": round(win_rate, 1),
        "decisive_wr": round(decisive_wr, 1),
        "non_loss_rate": round(non_loss_rate, 1),
        "avg_win_usd": round(avg_win_usd, 2),
        "avg_loss_usd": round(avg_loss_usd, 2),
        "avg_r": round(avg_r, 2),
        "expectancy_usd": round(expectancy_usd, 2),
        "profit_factor": round(profit_factor, 2),
        "net_pnl_usd": round(net_pnl_usd, 2),
        "total_costs_usd": round(total_costs, 2),
        "max_dd_usd": round(max_dd, 2),
        "max_dd_pct": round(max_dd_pct, 2),
        "final_equity": round(curr_eq, 2),
        "equity_curve": eq_curve,
        "dd_curve": dd_curve
    }

def compute_day_wise_breakdown(trades_list):
    """
    Computes exact Day-of-Week performance (Monday to Friday + Weekend):
    Tracks Trades, Wins, Breakevens, Losses, Win Rate (%), Avg R, and Net PnL ($) for each day.
    """
    day_stats = {}
    for d_idx in range(7):
        d_trades = [t for t in trades_list if t.get("weekday_idx") == d_idx]
        wins     = sum(1 for t in d_trades if t["net_pnl"] > 1.0)
        losses   = sum(1 for t in d_trades if t["net_pnl"] < -2.0)
        bes      = sum(1 for t in d_trades if -2.0 <= t["net_pnl"] <= 1.0)
        dec_cnt  = wins + losses
        wr_pct   = (wins / dec_cnt * 100.0) if dec_cnt > 0 else 0.0
        net_pnl  = round(sum(t["net_pnl"] for t in d_trades), 2)
        avg_r    = round(sum(t["result_r"] for t in d_trades) / len(d_trades), 2) if d_trades else 0.0

        day_stats[d_idx] = {
            "day_name": WEEKDAY_NAMES[d_idx],
            "day_short": WEEKDAY_SHORT[d_idx],
            "total": len(d_trades),
            "wins": wins,
            "breakevens": bes,
            "losses": losses,
            "wr": round(wr_pct, 0),
            "avg_r": avg_r,
            "net_pnl": net_pnl
        }

    active_weekdays = [day_stats[i] for i in range(5) if day_stats[i]["total"] > 0]
    best_day  = max(active_weekdays, key=lambda x: x["net_pnl"]) if active_weekdays else None
    worst_day = min(active_weekdays, key=lambda x: x["net_pnl"]) if active_weekdays else None

    return {
        "days": day_stats,
        "best_day": best_day,
        "worst_day": worst_day
    }

def generate_visual_backtest_report_chart(combined_stats, xau_stats, btc_stats, day_breakdown):
    """
    Generates a 4-Panel All-in-One Visual Backtest & Day-Wise Analytics Dashboard:
    - Panel 1 (Top Full-Width): $5,000 Equity Curve + Drawdown Limit
    - Panel 2 (Bottom-Left): Day-Wise Net PnL & Win-Rate (Mon, Tue, Wed, Thu, Fri)
    - Panel 3 (Bottom-Center): XAUUSD vs BTCUSD Net PnL Comparison
    - Panel 4 (Bottom-Right): Trade Outcome Distribution (+2R Wins, 0R BE, -1R Losses)
    """
    try:
        fig = plt.figure(figsize=(12.2, 7.0), dpi=125, facecolor="#ffffff")

        # Panel 1 (Top): Equity Curve
        ax1 = fig.add_axes([0.06, 0.50, 0.90, 0.41], facecolor="#ffffff")
        eq_curve = combined_stats["equity_curve"]
        x_idx    = np.arange(len(eq_curve))

        ax1.grid(True, linestyle="-", linewidth=0.5, color="#f0f3fa")
        for sp in ["top", "right"]:
            ax1.spines[sp].set_visible(False)

        ax1.axhline(STARTING_CAPITAL_USD, color="#64748b", linestyle="--", linewidth=1.0, label="Start Balance ($5,000)")
        ax1.axhline(STARTING_CAPITAL_USD - MAX_DRAWDOWN_REVIEW_USD, color="#f23645", linestyle=":", linewidth=1.1, label="2% Review Stop ($4,900)")

        line_col = "#089981" if combined_stats["net_pnl_usd"] >= 0 else "#f23645"
        ax1.plot(x_idx, eq_curve, color=line_col, linewidth=2.2, zorder=4)
        ax1.fill_between(x_idx, STARTING_CAPITAL_USD, eq_curve, where=(np.array(eq_curve) >= STARTING_CAPITAL_USD), facecolor="#089981", alpha=0.12)
        ax1.fill_between(x_idx, STARTING_CAPITAL_USD, eq_curve, where=(np.array(eq_curve) < STARTING_CAPITAL_USD), facecolor="#f23645", alpha=0.12)

        ax1.set_title(
            f"$5,000 Two-Step Funded Account — Equity Curve (Final: ${combined_stats['final_equity']:,.2f} | Net: ${combined_stats['net_pnl_usd']:+.2f} | PF: {combined_stats['profit_factor']:.2f})",
            fontsize=10.5, fontweight="bold", color="#131722", pad=8
        )
        ax1.set_ylabel("Equity ($ USD)", fontsize=8.8, fontweight="bold", color="#131722")
        ax1.legend(loc="upper left", fontsize=8.0, frameon=True)

        # Panel 2 (Bottom-Left): Day-Wise Net PnL (Mon - Fri)
        ax_day = fig.add_axes([0.06, 0.08, 0.34, 0.32], facecolor="#ffffff")
        for sp in ["top", "right"]:
            ax_day.spines[sp].set_visible(False)
        ax_day.grid(True, axis="y", linestyle="-", linewidth=0.45, color="#f0f3fa")

        d_labels = [day_breakdown["days"][i]["day_short"] for i in range(5)]
        d_pnls   = [day_breakdown["days"][i]["net_pnl"] for i in range(5)]
        d_wrs    = [day_breakdown["days"][i]["wr"] for i in range(5)]
        d_cols   = ["#089981" if p >= 0 else "#f23645" for p in d_pnls]

        d_bars = ax_day.bar(d_labels, d_pnls, width=0.55, color=d_cols, alpha=0.88)
        ax_day.axhline(0, color="#475569", linewidth=0.8)
        ax_day.set_title("Day-Wise Net Profit ($ Mon – Fri)", fontsize=9.0, fontweight="bold", color="#131722")
        ax_day.set_ylabel("Net Profit ($)", fontsize=8.2, color="#131722")

        for b, wr_val in zip(d_bars, d_wrs):
            yv = b.get_height()
            ax_day.text(
                b.get_x() + b.get_width() / 2.0,
                yv + (1.2 if yv >= 0 else -4.5),
                f"${yv:+.0f}\n({wr_val:.0f}%)",
                ha="center", va="bottom" if yv >= 0 else "top", fontsize=7.1, fontweight="bold", color="#131722"
            )

        # Panel 3 (Bottom-Center): XAUUSD vs BTCUSD Comparison
        ax2 = fig.add_axes([0.46, 0.08, 0.23, 0.32], facecolor="#ffffff")
        for sp in ["top", "right"]:
            ax2.spines[sp].set_visible(False)
        ax2.grid(True, axis="y", linestyle="-", linewidth=0.45, color="#f0f3fa")

        markets = [f"XAUUSD\n({xau_stats['total']})", f"BTCUSD\n({btc_stats['total']})"]
        pnls    = [xau_stats["net_pnl_usd"], btc_stats["net_pnl_usd"]]
        colors  = ["#089981" if p >= 0 else "#f23645" for p in pnls]

        bars = ax2.bar(markets, pnls, width=0.48, color=colors, alpha=0.88)
        ax2.axhline(0, color="#475569", linewidth=0.8)
        ax2.set_title("XAUUSD vs BTCUSD", fontsize=9.0, fontweight="bold", color="#131722")

        for b, st in zip(bars, [xau_stats, btc_stats]):
            y_v = b.get_height()
            ax2.text(
                b.get_x() + b.get_width() / 2.0,
                y_v + (1.2 if y_v >= 0 else -4.5),
                f"${y_v:+.0f}\n({st['decisive_wr']:.0f}%)",
                ha="center", va="bottom" if y_v >= 0 else "top", fontsize=7.4, fontweight="bold", color="#131722"
            )

        # Panel 4 (Bottom-Right): Outcome Distribution (+2R Wins, 0R Breakevens, -1R Losses)
        ax3 = fig.add_axes([0.74, 0.08, 0.22, 0.32], facecolor="#ffffff")
        for sp in ["top", "right"]:
            ax3.spines[sp].set_visible(False)
        ax3.grid(True, axis="y", linestyle="-", linewidth=0.45, color="#f0f3fa")

        outcomes = ["+2R Win", "0R BE", "-1R Loss"]
        counts   = [combined_stats["wins"], combined_stats["breakevens"], combined_stats["losses"]]
        o_cols   = ["#089981", "#3b82f6", "#f23645"]

        b3 = ax3.bar(outcomes, counts, width=0.50, color=o_cols, alpha=0.88)
        ax3.set_title(
            f"Outcomes (Max DD: ${combined_stats['max_dd_usd']:.0f})",
            fontsize=9.0, fontweight="bold", color="#131722"
        )
        for b in b3:
            h_v = int(b.get_height())
            ax3.text(b.get_x() + b.get_width() / 2.0, h_v + 0.15, str(h_v), ha="center", va="bottom", fontsize=8.2, fontweight="bold", color="#131722")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=125, facecolor="#ffffff")
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Backtest Chart Error: {e}")
        return None

def run_and_send_master_backtest_report(target_pair=None):
    """
    Executes the complete PDF Stage 3, 4 & 6 Backtest + Day-Wise (Mon–Fri) Report and sends:
    1. 4-Panel Visual Dashboard Chart (Equity Curve + Day-Wise Bar Chart + XAU vs BTC + Outcomes)
    2. Complete All-in-One Text Report (including Day-by-Day Mon–Fri Win Rate & Net Profit)
    3. Downloadable CSV file of every single backtested trade (with Day of Week column)
    """
    send_telegram_message("⏳ <b>Running Historical Backtest + Day-Wise (Mon–Fri) Profitability Analysis...</b>")

    xau_res = simulate_single_pair_backtest("XAU/USD", FOREX_PAIRS["XAU/USD"], ny_session_only=False) if target_pair in [None, "XAU/USD"] else {"trades": [], "skipped_risk": 0, "skipped_chop": 0, "days_tested": 0}
    btc_res = simulate_single_pair_backtest("BTC/USD", FOREX_PAIRS["BTC/USD"], ny_session_only=False) if target_pair in [None, "BTC/USD"] else {"trades": [], "skipped_risk": 0, "skipped_chop": 0, "days_tested": 0}

    xau_trades = xau_res["trades"] if xau_res else []
    btc_trades = btc_res["trades"] if btc_res else []

    all_trades = sorted(xau_trades + btc_trades, key=lambda x: x["timestamp"])
    ny_trades  = [t for t in all_trades if t["in_ny_window"]]

    comb_stats    = compute_stage6_metrics(all_trades)
    ny_stats      = compute_stage6_metrics(ny_trades)
    xau_stats     = compute_stage6_metrics(xau_trades)
    btc_stats     = compute_stage6_metrics(btc_trades)
    day_breakdown = compute_day_wise_breakdown(all_trades)

    # Format Day-Wise (Monday to Friday + Weekend) Breakdown Text
    day_lines = ""
    for d_idx in range(5):
        ds = day_breakdown["days"][d_idx]
        icon = "🟢" if ds["net_pnl"] > 0 else ("🔴" if ds["net_pnl"] < 0 else "⚪")
        day_lines += (
            f"{icon} <b>{ds['day_name']}:</b> {ds['total']} trades "
            f"(W:{ds['wins']} | BE:{ds['breakevens']} | L:{ds['losses']}) | "
            f"WR: <b>{ds['wr']:.0f}%</b> | Net: <b><code>${ds['net_pnl']:+.2f}</code></b>\n"
        )

    # Check if any weekend BTC trades exist
    wknd_trades = day_breakdown["days"][5]["total"] + day_breakdown["days"][6]["total"]
    if wknd_trades > 0:
        wknd_pnl = day_breakdown["days"][5]["net_pnl"] + day_breakdown["days"][6]["net_pnl"]
        day_lines += f"⚪ <b>Weekend (BTC):</b> {wknd_trades} trades | Net: <b><code>${wknd_pnl:+.2f}</code></b>\n"

    best_d  = day_breakdown["best_day"]
    worst_d = day_breakdown["worst_day"]
    best_day_str  = f"🔥 <b>Most Profitable Day:</b> <b>{best_d['day_name']} (${best_d['net_pnl']:+.2f} | {best_d['wr']:.0f}% WR)</b>" if best_d else ""
    worst_day_str = f"⚠️ <b>Weakest Day:</b> <b>{worst_d['day_name']} (${worst_d['net_pnl']:+.2f} | {worst_d['wr']:.0f}% WR)</b>" if worst_d else ""

    # Level Sweep Breakdown (PDH, PDL, LON H/L, ASIA H/L, 1H Swing)
    level_summary = {}
    for t in all_trades:
        lvl_k = t["level_swept"]
        if lvl_k not in level_summary:
            level_summary[lvl_k] = {"count": 0, "wins": 0, "pnl": 0.0}
        level_summary[lvl_k]["count"] += 1
        if t["net_pnl"] > 1.0:
            level_summary[lvl_k]["wins"] += 1
        level_summary[lvl_k]["pnl"] += t["net_pnl"]

    lvl_lines = ""
    for lk, lv_d in sorted(level_summary.items(), key=lambda item: item[1]["pnl"], reverse=True)[:5]:
        lvl_lines += f"• <b>{lk}:</b> {lv_d['count']} trades | Wins: {lv_d['wins']} | Net: <b>${lv_d['pnl']:+.2f}</b>\n"
    if not lvl_lines:
        lvl_lines = "• No completed level sweeps in sample.\n"

    better_market = "XAUUSD (Gold)" if xau_stats["net_pnl_usd"] >= btc_stats["net_pnl_usd"] else "BTCUSD (Bitcoin)"
    total_skipped_risk = (xau_res["skipped_risk"] if xau_res else 0) + (btc_res["skipped_risk"] if btc_res else 0)
    total_skipped_chop = (xau_res["skipped_chop"] if xau_res else 0) + (btc_res["skipped_chop"] if btc_res else 0)
    days_cov = max(xau_res["days_tested"] if xau_res else 0, btc_res["days_tested"] if btc_res else 0)

    report_html = (
        f"📊 <b>ALL-IN-ONE BACKTEST & DAY-WISE REPORT</b> 📊\n"
        f"<i>(Sample: Last ~{days_cov:.0f} Days M5/M15/H1 | $5,000 Account | $12.50 Risk/Trade)</i>\n\n"
        f"🏆 <b>1. OVERALL PERFORMANCE (AFTER SPREAD & COSTS):</b>\n"
        f"• <b>Balance:</b> <code>$5,000.00</code> ➔ <b>Final Equity:</b> <b><code>${comb_stats['final_equity']:,.2f}</code></b>\n"
        f"• <b>Net Profit / Loss:</b> <b><code>${comb_stats['net_pnl_usd']:+.2f}</code></b> (Costs Deducted: <code>${comb_stats['total_costs_usd']:.2f}</code>)\n"
        f"• <b>Total Setups:</b> <b>{comb_stats['total']}</b> (Wins: <b>{comb_stats['wins']}</b> | BE 0R: <b>{comb_stats['breakevens']}</b> | Losses: <b>{comb_stats['losses']}</b>)\n"
        f"• <b>Win Rate (Excl. BE):</b> <b>{comb_stats['decisive_wr']:.1f}%</b> | <b>Protected (Win+BE):</b> <b>{comb_stats['non_loss_rate']:.1f}%</b>\n"
        f"• <b>Profit Factor:</b> <b>{comb_stats['profit_factor']:.2f}</b> | <b>Expectancy:</b> <b>${comb_stats['expectancy_usd']:+.2f}/trade ({comb_stats['avg_r']:+.2f}R)</b>\n"
        f"• <b>Max Drawdown:</b> <b><code>${comb_stats['max_dd_usd']:.2f} ({comb_stats['max_dd_pct']:.2f}%)</code></b> <i>(Limit: $100 / 2.0%)</i>\n\n"
        f"📅 <b>2. DAY-WISE PROFITABILITY BREAKDOWN (MON – FRI):</b>\n"
        f"{day_lines}"
        f"{best_day_str}\n"
        f"{worst_day_str}\n\n"
        f"⚖️ <b>3. MARKET COMPARISON (XAUUSD vs BTCUSD):</b>\n"
        f"• 🥇 <b>XAUUSD:</b> {xau_stats['total']} trades | WR: <b>{xau_stats['decisive_wr']:.0f}%</b> | PF: <b>{xau_stats['profit_factor']:.2f}</b> | Net: <b>${xau_stats['net_pnl_usd']:+.2f}</b>\n"
        f"• ₿ <b>BTCUSD:</b> {btc_stats['total']} trades | WR: <b>{btc_stats['decisive_wr']:.0f}%</b> | PF: <b>{btc_stats['profit_factor']:.2f}</b> | Net: <b>${btc_stats['net_pnl_usd']:+.2f}</b>\n"
        f"• 🗽 <b>NY Window Only:</b> {ny_stats['total']} trades | WR: <b>{ny_stats['decisive_wr']:.0f}%</b> | Net: <b>${ny_stats['net_pnl_usd']:+.2f}</b>\n"
        f"• 💡 <b>Best Market After Costs:</b> <b>{better_market}</b>\n\n"
        f"🧲 <b>4. SWEPT LEVEL PERFORMANCE & SAFEGUARDS:</b>\n"
        f"{lvl_lines}"
        f"• 🛡️ <b>Skipped (> $12.50 Risk on 0.01 Lot):</b> {total_skipped_risk} | <b>Skipped (1H Choppy):</b> {total_skipped_chop}"
    )

    chart_buf = generate_visual_backtest_report_chart(comb_stats, xau_stats, btc_stats, day_breakdown)
    send_telegram_signal_with_chart("📈 <b>4-Panel Visual Backtest & Day-Wise (Mon–Fri) Profitability Dashboard</b>", chart_buf)
    send_telegram_message(report_html)

    if all_trades:
        csv_buf = io.StringIO()
        field_names = ["Date / Time (IST)", "Day of Week", "Market", "Direction", "1H Bias", "Level Swept", "5M Confirmation", "Entry", "SL", "1:2 TP", "Lot Size", "Planned Risk $", "Spread/Cost $", "Result (R)", "Net PnL $", "Outcome", "NY Session?"]
        writer = csv.DictWriter(csv_buf, fieldnames=field_names)
        writer.writeheader()
        for t in all_trades:
            writer.writerow({
                "Date / Time (IST)": t["timestamp"].strftime("%Y-%m-%d %I:%M %p"),
                "Day of Week": t["day_name"],
                "Market": t["market"],
                "Direction": t["direction"],
                "1H Bias": t["h1_bias"],
                "Level Swept": t["level_full"],
                "5M Confirmation": t["mss_confirm"],
                "Entry": t["entry"],
                "SL": t["sl"],
                "1:2 TP": t["tp"],
                "Lot Size": t["lot"],
                "Planned Risk $": f"${t['risk_usd']:.2f}",
                "Spread/Cost $": f"${t['cost_usd']:.2f}",
                "Result (R)": f"{t['result_r']:+.2f}R",
                "Net PnL $": f"${t['net_pnl']:+.2f}",
                "Outcome": t["outcome"],
                "NY Session?": "YES" if t["in_ny_window"] else "NO"
            })
        csv_bytes = csv_buf.getvalue().encode("utf-8")
        send_telegram_document(
            csv_bytes,
            "Backtest_All_Trades_With_Days.csv",
            caption_html=f"📂 <b>Complete Trade-by-Trade Backtest Sheet ({len(all_trades)} Setups with Day-of-Week)</b>"
        )

# =====================================================================
# 8. TRADINGVIEW LIVE CHART ENGINE (PDH/PDL, ASIAN/LONDON H/L, ENTRY SPOT)
# =====================================================================
def draw_tradingview_arrow(ax, x_pos, y_pos, y_span, is_up=True):
    h = y_span * 0.038
    w = 1.05
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

def generate_roadmap_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=None):
    try:
        sub = df_5m.iloc[-56:].copy()
        if sub.empty or len(sub) < 20:
            return None

        dec     = info["decimals"]
        mt5_sym = info["mt5_name"]
        hdr_nam = info.get("tv_header_name", mt5_sym)

        roadmap = extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, dec)
        h1_bias = roadmap["h1_bias"]
        levels  = roadmap["levels"]

        fig = plt.figure(figsize=(11.0, 6.2), dpi=120, facecolor="#ffffff")
        ax  = fig.add_axes([0.015, 0.08, 0.880, 0.83], facecolor="#ffffff")

        n_bars = len(sub)
        x_vals = np.arange(n_bars)
        right_edge_idx = n_bars + 21

        ax.grid(True, which="major", linestyle="-", linewidth=0.45, color="#f0f3fa", zorder=1)
        for spine in ["top", "left"]:
            ax.spines[spine].set_visible(False)
        ax.spines["right"].set_color("#e0e3eb")
        ax.spines["bottom"].set_color("#e0e3eb")

        ax.text(
            0.45, 0.50, f"{mt5_sym} · 1H ➔ 15M ➔ 5M", transform=ax.transAxes,
            fontsize=32, fontweight="bold", color="#131722", alpha=0.07,
            ha="center", va="center", zorder=1
        )

        opens  = sub["Open"].values.astype(float)
        highs  = sub["High"].values.astype(float)
        lows   = sub["Low"].values.astype(float)
        closes = sub["Close"].values.astype(float)

        c_min, c_max = float(np.min(lows)), float(np.max(highs))
        c_span = max(c_max - c_min, float(sub.iloc[-2]["ATR"]) * 4.0)

        visible_levels = [
            lv for lv in levels
            if (c_min - c_span * 0.45) <= lv["price"] <= (c_max + c_span * 0.45)
        ]
        if not visible_levels:
            nearest_lv = min(levels, key=lambda l: abs(l["price"] - live_spot))
            visible_levels = [nearest_lv]

        all_y = list(highs) + list(lows) + [live_spot]
        for lv in visible_levels:
            all_y.extend([lv["top"], lv["bot"]])
        if active_trade is not None:
            all_y.extend([active_trade["entry_spot"], active_trade["sl_price"], active_trade["tp2_price"]])

        y_min, y_max = float(np.nanmin(all_y)), float(np.nanmax(all_y))
        total_span   = max(y_max - y_min, 1e-6)
        min_body_h   = total_span * 0.0040

        bull_mask    = closes >= opens
        bear_mask    = ~bull_mask
        bot_bodies   = np.minimum(opens, closes)
        body_heights = np.maximum(np.abs(closes - opens), min_body_h)

        ax.vlines(x_vals[bull_mask], lows[bull_mask], highs[bull_mask], color="#089981", linewidth=1.15, zorder=4)
        ax.vlines(x_vals[bear_mask], lows[bear_mask], highs[bear_mask], color="#f23645", linewidth=1.15, zorder=4)
        ax.bar(x_vals[bull_mask], body_heights[bull_mask], bottom=bot_bodies[bull_mask], width=0.68, color="#089981", edgecolor="#089981", linewidth=0.40, zorder=4)
        ax.bar(x_vals[bear_mask], body_heights[bear_mask], bottom=bot_bodies[bear_mask], width=0.68, color="#f23645", edgecolor="#f23645", linewidth=0.40, zorder=4)

        for idx, lv in enumerate(visible_levels):
            z_top = lv["top"]
            z_mid = lv["price"]
            z_bot = lv["bot"]
            s_idx = 2
            lbl_c = lv["color"]

            ax.add_patch(mpatches.Rectangle(
                (s_idx, z_bot),
                right_edge_idx - s_idx,
                z_top - z_bot,
                facecolor="#f23645" if lv["type"] == "HIGH_POOL" else "#089981",
                alpha=0.08, edgecolor="none", zorder=2
            ))
            ax.plot([s_idx, right_edge_idx], [z_top, z_top], color="#1e222d", linewidth=0.85, zorder=3)
            ax.plot([s_idx, right_edge_idx], [z_bot, z_bot], color="#1e222d", linewidth=0.85, zorder=3)
            ax.plot([s_idx, right_edge_idx], [z_mid, z_mid], color=lbl_c, linewidth=0.80, linestyle="--", alpha=0.80, zorder=3)

            ax.text(
                s_idx + 1, z_top + total_span * 0.006,
                f"{lv['short']} ({format_price(z_mid, dec)})",
                fontsize=7.3, fontweight="bold", color=lbl_c, ha="left", va="bottom",
                bbox=dict(boxstyle="round,pad=0.14", facecolor="#ffffff", edgecolor=lbl_c, alpha=0.88, linewidth=0.6),
                clip_on=True, zorder=6
            )

        last_bull = closes[-1] >= opens[-1]
        live_col  = "#089981" if last_bull else "#f23645"
        ax.axhline(live_spot, color=live_col, linewidth=0.85, linestyle=":", alpha=0.85, zorder=5)

        if active_trade is not None:
            ent_p = active_trade["entry_spot"]
            sl_p  = active_trade["sl_price"]
            tp1_p = active_trade["tp1_price"]
            tp2_p = active_trade["tp2_price"]
            p_x0  = n_bars - 1
            p_w   = 18

            fvg = active_trade.get("fvg_zone")
            if fvg is not None:
                ax.add_patch(mpatches.Rectangle(
                    (n_bars - 6, fvg["bot"]), 8, fvg["top"] - fvg["bot"],
                    facecolor="#fbbf24", alpha=0.25, edgecolor="#d97706", linestyle=":", linewidth=0.8, zorder=3
                ))

            ax.add_patch(mpatches.Rectangle(
                (p_x0, min(ent_p, sl_p)), p_w, abs(ent_p - sl_p),
                facecolor="#f23645", alpha=0.16, edgecolor="#f23645", linewidth=0.9, zorder=5
            ))
            ax.add_patch(mpatches.Rectangle(
                (p_x0, min(ent_p, tp2_p)), p_w, abs(tp2_p - ent_p),
                facecolor="#089981", alpha=0.16, edgecolor="#089981", linewidth=0.9, zorder=5
            ))

            ax.plot([p_x0 - 2, right_edge_idx], [ent_p, ent_p], color="#2563eb", linestyle="--", linewidth=1.45, zorder=7)
            ax.plot([p_x0, p_x0 + p_w], [tp1_p, tp1_p], color="#089981", linestyle="--", linewidth=1.0, zorder=6)
            ax.plot([p_x0, p_x0 + p_w], [tp2_p, tp2_p], color="#089981", linewidth=1.4, zorder=6)
            ax.plot([p_x0, p_x0 + p_w], [sl_p, sl_p], color="#f23645", linewidth=1.4, zorder=6)

            is_buy_dir = active_trade["direction"] == "BUY"
            draw_tradingview_arrow(
                ax, p_x0 + 2.0,
                ent_p + (total_span * 0.005 if is_buy_dir else -total_span * 0.005),
                total_span, is_up=is_buy_dir
            )

            stage_prefix = "ENTER NOW @" if active_trade.get("stage") == "CONFIRMED_BREAKOUT" else "ENTRY SPOT @"
            ax.text(
                p_x0 + (p_w * 0.52), ent_p,
                f" 🎯 {stage_prefix} {format_price(ent_p, dec)} (1:2 RR) ",
                fontsize=7.5, fontweight="bold", color="#ffffff", ha="center", va="center",
                bbox=dict(boxstyle="round,pad=0.22", facecolor="#1d4ed8", edgecolor="#1e3a8a", linewidth=0.8),
                clip_on=True, zorder=9
            )

        y_pad = total_span * 0.10
        ax.set_ylim(y_min - y_pad, y_max + y_pad)
        ax.set_xlim(-0.8, right_edge_idx)
        ax.yaxis.tick_right()
        ax.tick_params(axis="y", labelsize=8.6, colors="#131722")
        ax.tick_params(axis="x", labelsize=8.3, colors="#131722")

        y_ticks = ax.get_yticks()
        ax.set_yticks(y_ticks)
        ax.set_yticklabels([format_tv_comma_price(yt, dec) for yt in y_ticks], fontsize=8.6, color="#131722")

        if active_trade is not None:
            for t_y, t_lbl, t_bg in [
                (active_trade["tp2_price"], f" 1:2 TP {format_tv_comma_price(active_trade['tp2_price'], dec)} ", "#089981"),
                (active_trade["entry_spot"], f" ENTRY {format_tv_comma_price(active_trade['entry_spot'], dec)} ", "#2563eb"),
                (active_trade["sl_price"], f" SL {format_tv_comma_price(active_trade['sl_price'], dec)} ", "#f23645")
            ]:
                ax.text(
                    right_edge_idx + 0.25, t_y, t_lbl,
                    fontsize=7.6, fontweight="bold", color="#ffffff", va="center", ha="left",
                    bbox=dict(boxstyle="square,pad=0.18", facecolor=t_bg, edgecolor="none"),
                    clip_on=False, zorder=9
                )

        now_utc = get_utc_now()
        rem_sec = max(0, (5 - (now_utc.minute % 5)) * 60 - now_utc.second)
        mm, ss  = divmod(rem_sec, 60)
        live_tag_txt = f" {format_tv_comma_price(live_spot, dec)} \n {mm:02d}:{ss:02d} "
        ax.text(
            right_edge_idx + 0.25, live_spot, live_tag_txt,
            fontsize=7.8, fontweight="bold", color="#ffffff", va="center", ha="left",
            bbox=dict(boxstyle="square,pad=0.19", facecolor=live_col, edgecolor="none"),
            clip_on=False, zorder=10
        )

        tick_indices = [4, int(n_bars * 0.34), int(n_bars * 0.64), n_bars - 1]
        x_labels = []
        for ti in tick_indices:
            dt_ist = to_utc_timestamp(sub.index[ti]) + timedelta(hours=5, minutes=30)
            x_labels.append(dt_ist.strftime("%d %b %I:%M %p"))
        ax.set_xticks(tick_indices)
        ax.set_xticklabels(x_labels, fontsize=8.2, color="#131722")

        last_bar = sub.iloc[-1]
        o_val    = float(last_bar["Open"])
        h_val    = float(last_bar["High"])
        l_val    = float(last_bar["Low"])
        c_val    = float(last_bar["Close"])
        chg_val  = c_val - o_val
        chg_pct  = (chg_val / max(o_val, 1e-9)) * 100.0
        ohlc_col = "#089981" if c_val >= o_val else "#f23645"

        top_ax = fig.add_axes([0.015, 0.915, 0.96, 0.075], facecolor="#ffffff")
        top_ax.axis("off")
        top_ax.text(
            0.0, 0.72,
            f"{hdr_nam} ({mt5_sym}) · PDH/PDL + Session Liquidity Map (5m)",
            fontsize=9.5, fontweight="bold", color="#131722", va="center", ha="left"
        )
        top_ax.text(
            0.0, 0.22,
            f"O {format_tv_comma_price(o_val, dec)}   H {format_tv_comma_price(h_val, dec)}   L {format_tv_comma_price(l_val, dec)}   C {format_tv_comma_price(c_val, dec)}   {chg_val:+.{dec}f} ({chg_pct:+.2f}%)",
            fontsize=8.6, fontweight="bold", color=ohlc_col, va="center", ha="left"
        )
        bias_bg = "#ecfdf5" if h1_bias == "BULLISH" else ("#fef2f2" if h1_bias == "BEARISH" else "#f8fafc")
        top_ax.text(
            0.985, 0.50, f"1H: {h1_bias}",
            fontsize=8.4, fontweight="bold", color="#131722", va="center", ha="right",
            bbox=dict(boxstyle="round,pad=0.24", facecolor=bias_bg, edgecolor="#cbd5e1", linewidth=0.7)
        )

        fig.text(0.015, 0.018, "TradingView · $5,000 Two-Step Roadmap", fontsize=9.0, fontweight="bold", color="#131722")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=120, facecolor="#ffffff")
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

        if len(caption_html) <= 1000:
            for attempt in range(2):
                try:
                    files = {"photo": ("chart.png", photo_bytes, "image/png")}
                    data  = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                    res   = HTTP_SESSION.post(url, data=data, files=files, timeout=15) if attempt == 0 else requests.post(url, data=data, files=files, timeout=18)
                    if res.status_code == 200 and res.json().get("ok"):
                        return
                except Exception as e:
                    print(f"Photo Attempt {attempt + 1} Warning: {e}")
                    time.sleep(0.4)

        for attempt in range(2):
            try:
                files = {"photo": ("chart.png", photo_bytes, "image/png")}
                data  = {"chat_id": target_chat_id}
                res   = requests.post(url, data=data, files=files, timeout=18)
                if res.status_code == 200 and res.json().get("ok"):
                    send_telegram_message(caption_html, target_chat_id)
                    return
            except Exception as e:
                print(f"Fallback Photo Attempt {attempt + 1} Warning: {e}")
                time.sleep(0.4)

    send_telegram_message(caption_html, target_chat_id)

# =====================================================================
# 9. TELEGRAM COMMANDS (/backtest, /report, /days, /journal, /gold, /btc)
# =====================================================================
def send_instant_preview_chart(pair_key=None):
    global chart_rotation_index
    pair_names_list = list(FOREX_PAIRS.keys())
    if pair_key is None:
        pair_key = pair_names_list[chart_rotation_index % len(pair_names_list)]
        chart_rotation_index += 1

    info = FOREX_PAIRS.get(pair_key, FOREX_PAIRS["XAU/USD"])
    df_1h, df_15m, df_5m, live_spot = get_roadmap_3tf_spot_data(pair_key, info, use_fast_cache=True)
    if df_1h is None or df_15m is None or df_5m is None or live_spot is None:
        send_telegram_message("⚠️ Data loading, please try again in a few seconds.")
        return

    df_1h  = enrich_price_action_metrics(df_1h)
    df_15m = enrich_price_action_metrics(df_15m)
    df_5m  = enrich_price_action_metrics(df_5m)
    dec    = info["decimals"]

    roadmap = extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, dec)
    cand    = pending_pre_alerts.get(pair_key) or evaluate_roadmap_sweep_mss_setup(pair_key, info, df_1h, df_15m, df_5m, live_spot, ignore_locks=True)

    chart_buf = generate_roadmap_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=cand)
    _, _, ny_status_txt, _ = get_ny_session_status()

    if cand is not None:
        stage_lbl = "🚨 ENTER NOW (5M MSS CONFIRMED)" if cand.get("stage") == "CONFIRMED_BREAKOUT" else "⏳ PRE-ALERT LOCKED (WAITING FOR 5M MSS CLOSE)"
        caption = (
            f"📊 <b>{info['mt5_name']} • ROADMAP 1H ➔ 15M ➔ 5M SETUP</b>\n"
            f"🔔 <b>Status:</b> <b>{stage_lbl}</b>\n\n"
            f"🧭 <b>1H Context:</b> <b>{roadmap['h1_bias']}</b> | 📢 <b>Action:</b> <b>{cand['action']}</b>\n"
            f"🧲 <b>15M Level Swept:</b> {cand['swept_level_name']}\n"
            f"📍 <b>Live Price:</b> <code>{format_price(live_spot, dec)}</code>\n"
            f"🎯 <b>ENTRY SPOT:</b> <b><code>{format_price(cand['entry_spot'], dec)}</code></b>\n"
            f"💼 <b>Lot Size ($12.50 Risk):</b> <code>{cand['lot_size']:.2f} Lot</code> (Planned Risk: ${cand['planned_risk_usd']:.2f})\n"
            f"🛑 <b>Invalidation SL:</b> <code>{format_price(cand['sl_price'], dec)}</code>\n"
            f"🛡️ <b>1:1 Lock (TP1):</b> <code>{format_price(cand['tp1_price'], dec)}</code> | 🎯 <b>1:2 Target (TP2):</b> <code>{format_price(cand['tp2_price'], dec)}</code>"
        )
    else:
        no_trade_note = "⚠️ <b>1H is RANGING/CHOPPY — Stand Aside!</b>\n" if roadmap["h1_bias"] == "RANGING" else ""
        caption = (
            f"📊 <b>{info['mt5_name']} • PRE-SESSION LIQUIDITY MAP</b>\n"
            f"🕒 <b>Session:</b> {ny_status_txt}\n"
            f"🧭 <b>1H Context:</b> <b>{roadmap['h1_bias']}</b> | 📍 <b>Live:</b> <code>{format_price(live_spot, dec)}</code>\n"
            f"{no_trade_note}\n"
            f"📌 <b>Pre-Marked Roadmap Levels:</b>\n"
            f"• <b>PDH / PDL:</b> <code>{format_price(roadmap['pdh'], dec)}</code> / <code>{format_price(roadmap['pdl'], dec)}</code>\n"
            f"• <b>London H / L:</b> <code>{format_price(roadmap['london_high'], dec)}</code> / <code>{format_price(roadmap['london_low'], dec)}</code>\n"
            f"• <b>Asian H / L:</b> <code>{format_price(roadmap['asian_high'], dec)}</code> / <code>{format_price(roadmap['asian_low'], dec)}</code>\n\n"
            f"⏳ <b>Rule:</b> Waiting for 15M sweep of a key level + 5M MSS body close."
        )

    send_telegram_signal_with_chart(caption, chart_buf)

def send_live_status_summary():
    bal, eq, day_pnl, week_pnl = get_live_account_metrics()
    _, _, ny_status_txt, _     = get_ny_session_status()
    dd_from_peak               = max(STARTING_CAPITAL_USD, HIGH_WATER_MARK_USD) - min(bal, eq)
    today_name                 = WEEKDAY_NAMES[get_ny_weekday_index(get_utc_now())]

    report = (
        f"📊 <b>$5,000 TWO-STEP FUNDED ROADMAP STATUS</b>\n\n"
        f"📅 <b>Day:</b> <b>{today_name}</b> | 🕒 <b>Session:</b> {ny_status_txt}\n"
        f"💰 <b>Balance / Equity:</b> <b>${bal:.2f} / ${eq:.2f}</b> (Peak: ${HIGH_WATER_MARK_USD:.2f})\n"
        f"📈 <b>Today's PnL:</b> <b>${day_pnl:+.2f}</b> (Limit: -${DAILY_STOP_LOSS_USD:.2f})\n"
        f"📅 <b>Weekly PnL:</b> <b>${week_pnl:+.2f}</b> (Limit: -${WEEKLY_STOP_LOSS_USD:.2f})\n"
        f"🛡️ <b>Drawdown from Peak:</b> <b>${dd_from_peak:.2f}</b> (Review Stop: ${MAX_DRAWDOWN_REVIEW_USD:.2f})\n"
        f"🔢 <b>Trades Today:</b> {trades_today}/{MAX_DAILY_TRADES} | <b>Losses Today:</b> {losses_today}/{MAX_DAILY_LOSS_COUNT}\n\n"
    )
    for name, info in FOREX_PAIRS.items():
        try:
            df_1h, df_15m, df_5m, live_spot = get_roadmap_3tf_spot_data(name, info, use_fast_cache=True)
            if df_1h is not None and df_15m is not None and live_spot is not None:
                df_1h  = enrich_price_action_metrics(df_1h)
                df_15m = enrich_price_action_metrics(df_15m)
                rm     = extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, info["decimals"])
                pend   = pending_pre_alerts.get(name)
                st_str = f"⏳ Pre-Alert @ {format_price(pend['entry_spot'], info['decimals'])}" if pend else f"PDH:{format_price(rm['pdh'], info['decimals'])} | PDL:{format_price(rm['pdl'], info['decimals'])}"
                report += f"🔹 <b>{info['mt5_name']}:</b> <code>{format_price(live_spot, info['decimals'])}</code> | 1H: <b>{rm['h1_bias']}</b> ({st_str})\n"
        except Exception:
            pass
    report += "\n💡 Send <code>/backtest</code>, <code>/report</code>, or <code>/days</code> for full Day-Wise (Mon–Fri) & Stage 6 analytics."
    send_telegram_message(report)

def send_trading_journal_summary():
    if not journal_memory_log:
        send_telegram_message(
            "📒 <b>LIVE TRADING JOURNAL (PDF SECTION 5)</b>\n\n"
            "No live trades completed in this session yet.\n"
            "👉 To see the complete historical backtest + Day-Wise (Mon–Fri) profitability report right now, send: <code>/backtest</code> or <code>/days</code>"
        )
        return

    live_trades = [
        {"net_pnl": r["numeric_pnl"], "result_r": r["numeric_r"], "cost_usd": r["cost_usd"], "weekday_idx": r.get("weekday_idx", 0)}
        for r in journal_memory_log
    ]
    st  = compute_stage6_metrics(live_trades)
    dbd = compute_day_wise_breakdown(live_trades)

    d_txt = ""
    for i in range(5):
        ds = dbd["days"][i]
        if ds["total"] > 0:
            d_txt += f"• <b>{ds['day_name']}:</b> {ds['total']} trades | WR: {ds['wr']:.0f}% | Net: <b>${ds['net_pnl']:+.2f}</b>\n"

    msg = (
        f"📒 <b>LIVE TRADING JOURNAL & STAGE 6 METRICS</b>\n\n"
        f"• <b>Total Trades:</b> {st['total']} (Wins: {st['wins']} | BE: {st['breakevens']} | Losses: {st['losses']})\n"
        f"• <b>Win Rate (Excl. BE):</b> <b>{st['decisive_wr']:.1f}%</b> | <b>Profit Factor:</b> <b>{st['profit_factor']:.2f}</b>\n"
        f"• <b>Expectancy After Costs:</b> <b>${st['expectancy_usd']:+.2f}/trade ({st['avg_r']:+.2f}R)</b>\n"
        f"• <b>Net Realized PnL:</b> <b>${st['net_pnl_usd']:+.2f}</b> | <b>Max Drawdown:</b> <b>${st['max_dd_usd']:.2f}</b>\n\n"
        f"📅 <b>Live Day-Wise Summary:</b>\n{d_txt}\n"
        f"<b>Recent Journal Entries:</b>\n"
    )
    for idx, row in enumerate(journal_memory_log[-5:], 1):
        msg += (
            f"<b>#{idx}. {row['Market']} ({row['Day']} · {row['Date / time']})</b>\n"
            f"• 1H: {row['1H bias']} | Sweep: {row['Level / sweep']} | {row['5M confirmation']}\n"
            f"• <code>{row['Entry / SL / TP']}</code> | <b>{row['Result (R)']}</b>\n\n"
        )
    send_telegram_message(msg)

    if os.path.isfile(JOURNAL_CSV_PATH):
        try:
            with open(JOURNAL_CSV_PATH, "rb") as f:
                send_telegram_document(f.read(), "Live_Trading_Journal.csv", caption_html="📒 <b>Full Live Trading Journal CSV</b>")
        except Exception:
            pass

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
                elif msg_text in ["/journal", "journal"]:
                    send_trading_journal_summary()
                elif msg_text in ["/backtest", "backtest", "/report", "report", "/days", "days"]:
                    run_and_send_master_backtest_report(None)
                elif msg_text in ["/backtest gold", "/backtest xauusd", "/report gold"]:
                    run_and_send_master_backtest_report("XAU/USD")
                elif msg_text in ["/backtest btc", "/backtest btcusd", "/report btc"]:
                    run_and_send_master_backtest_report("BTC/USD")
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
# 10. TRADE EXECUTION, LOCKED PRE-ALERTS & 1:2 MANAGEMENT
# =====================================================================
def dispatch_confirmed_trade_alert(name, info, df_1h, df_15m, df_5m, live_spot, cand, closed_time):
    global trades_today, last_candle_traded, last_setup_traded

    dec        = info["decimals"]
    direction  = cand["direction"]
    signal     = cand["action"]
    entry_spot = cand["entry_spot"]
    sl_level   = cand["sl_price"]
    sl_dist    = cand["sl_dist"]
    tp1_level  = cand["tp1_price"]
    tp2_level  = cand["tp2_price"]
    lot_size   = cand["lot_size"]
    risk_usd   = cand["planned_risk_usd"]
    cost_usd   = cand.get("cost_usd", 0.35)
    reward_usd = round((risk_usd * PRIMARY_RR_TARGET) - cost_usd, 2)
    day_name   = WEEKDAY_NAMES[get_ny_weekday_index(closed_time)]

    cand["stage"]            = "CONFIRMED_BREAKOUT"
    last_candle_traded[name] = closed_time
    last_setup_traded[name]  = cand["setup_id"]
    pending_pre_alerts[name] = None
    trades_today += 1

    daily_trades_log.append({
        "id": trades_today, "pair": name, "mt5_name": info["mt5_name"],
        "action": signal, "direction": direction, "strategy": cand["short_tag"],
        "h1_bias": cand["h1_bias"], "swept_level_name": cand["swept_level_name"],
        "mss_confirmation": cand["mss_confirmation"], "lot_size": lot_size,
        "entry": entry_spot, "sl": sl_level, "sl_dist": sl_dist,
        "half_target": tp1_level, "target": tp2_level,
        "max_risk": risk_usd, "cost_usd": cost_usd, "expected_profit": reward_usd,
        "entry_candle_time": closed_time, "trailed_to_cost": False, "is_open": True
    })

    msg = (
        f"🚨 <b>ENTER NOW — {info['mt5_name']} (TRADE #{trades_today}/{MAX_DAILY_TRADES} · {day_name})</b> 🚨\n\n"
        f"📢 <b>Action:</b> <b>{signal}</b>\n"
        f"🎯 <b>ENTRY SPOT:</b> <b><code>{format_price(entry_spot, dec)}</code></b> (Live: <code>{format_price(live_spot, dec)}</code>)\n"
        f"💼 <b>MT5 Lot Size (0.25% Risk):</b> <b><code>{lot_size:.2f} Lot</code></b>\n\n"
        f"🛑 <b>Invalidation Stop-Loss:</b> <code>{format_price(sl_level, dec)}</code>\n"
        f"🛡️ <b>1:1 Breakeven Lock (TP1):</b> <code>{format_price(tp1_level, dec)}</code>\n"
        f"🎯 <b>1:2 Full Target (TP2):</b> <code>{format_price(tp2_level, dec)}</code>\n\n"
        f"🔍 <b>Roadmap Checklist:</b>\n{cand['why_text']}\n\n"
        f"⚖️ <b>Risk (incl. cost):</b> ${risk_usd:.2f}  |  <b>Net 1:2 Reward:</b> +${reward_usd:.2f}"
    )

    chart_buf = generate_roadmap_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=cand)
    send_telegram_signal_with_chart(msg, chart_buf)

def dispatch_pre_alert_setup(name, info, df_1h, df_15m, df_5m, live_spot, cand, closed_time):
    dec        = info["decimals"]
    entry_spot = cand["entry_spot"]
    sl_level   = cand["sl_price"]
    tp1_level  = cand["tp1_price"]
    tp2_level  = cand["tp2_price"]
    lot_size   = cand["lot_size"]
    risk_usd   = cand["planned_risk_usd"]
    reward_usd = round((risk_usd * PRIMARY_RR_TARGET) - cand.get("cost_usd", 0.35), 2)

    cand["created_utc"]         = get_utc_now()
    pending_pre_alerts[name]    = cand
    last_pre_alert_candle[name] = closed_time
    last_setup_traded[name]     = cand["setup_id"]

    msg = (
        f"⏳ <b>PRE-ALERT LOCKED — {info['mt5_name']} (15M SWEEP DONE)</b> ⏳\n\n"
        f"📢 <b>Upcoming Action:</b> <b>{cand['action']}</b>\n"
        f"🧲 <b>15M Swept Level:</b> <b>{cand['swept_level_name']}</b>\n"
        f"📍 <b>Current Live Price:</b> <code>{format_price(live_spot, dec)}</code>\n"
        f"🎯 <b>LOCKED 5M MSS ENTRY SPOT:</b> <b><code>{format_price(entry_spot, dec)}</code></b>\n\n"
        f"💼 <b>Lot Size ($12.50 Max Risk):</b> <b><code>{lot_size:.2f} Lot</code></b>\n"
        f"🛑 <b>Invalidation SL:</b> <code>{format_price(sl_level, dec)}</code>\n"
        f"🛡️ <b>1:1 Lock (TP1):</b> <code>{format_price(tp1_level, dec)}</code>\n"
        f"🎯 <b>1:2 Target (TP2):</b> <code>{format_price(tp2_level, dec)}</code> (+${reward_usd:.2f})"
    )

    chart_buf = generate_roadmap_chart(df_1h, df_15m, df_5m, info, live_spot, active_trade=cand)
    send_telegram_signal_with_chart(msg, chart_buf)

def check_pending_pre_alerts_realtime():
    can_trade, _, _ = check_funded_account_risk_rules()
    if not can_trade:
        return

    now_utc = get_utc_now()
    for name, cand in list(pending_pre_alerts.items()):
        if cand is None:
            continue
        info = FOREX_PAIRS[name]
        dec  = info["decimals"]

        mins_waiting = (now_utc - cand.get("created_utc", now_utc)).total_seconds() / 60.0
        if mins_waiting > PRE_ALERT_EXPIRY_MINUTES:
            pending_pre_alerts[name] = None
            pre_alert_cooldown[name] = now_utc + timedelta(minutes=15)
            continue

        df_1h, df_15m, df_5m, live_spot = get_roadmap_3tf_spot_data(name, info, use_fast_cache=True)
        if df_1h is None or df_15m is None or df_5m is None or live_spot is None:
            continue

        df_1h  = enrich_price_action_metrics(df_1h)
        df_15m = enrich_price_action_metrics(df_15m)
        df_5m  = enrich_price_action_metrics(df_5m)
        closed_time = to_utc_timestamp(df_5m.index[-2])
        live_open   = float(df_5m.iloc[-1]["Open"])

        if cand["direction"] == "BUY" and live_spot <= cand["sl_price"]:
            pending_pre_alerts[name] = None
            pre_alert_cooldown[name] = now_utc + timedelta(minutes=20)
            send_telegram_message(
                f"❌ <b>PRE-ALERT INVALIDATED — {info['mt5_name']}</b>\n"
                f"Price broke below invalidation SL (<code>{format_price(cand['sl_price'], dec)}</code>) before confirming 5M MSS. No trade taken."
            )
            continue
        elif cand["direction"] == "SELL" and live_spot >= cand["sl_price"]:
            pending_pre_alerts[name] = None
            pre_alert_cooldown[name] = now_utc + timedelta(minutes=20)
            send_telegram_message(
                f"❌ <b>PRE-ALERT INVALIDATED — {info['mt5_name']}</b>\n"
                f"Price broke above invalidation SL (<code>{format_price(cand['sl_price'], dec)}</code>) before confirming 5M MSS. No trade taken."
            )
            continue

        triggered_now = (
            (cand["direction"] == "BUY" and live_spot >= cand["entry_spot"] and live_spot > live_open) or
            (cand["direction"] == "SELL" and live_spot <= cand["entry_spot"] and live_spot < live_open)
        )
        if triggered_now:
            dispatch_confirmed_trade_alert(name, info, df_1h, df_15m, df_5m, live_spot, cand, closed_time)

def manage_open_trades(pair_name, candle_time, live_spot, df_5m, decimals):
    global daily_realized_pnl, weekly_realized_pnl, total_account_pnl, losses_today, pair_cooldown_until

    now_utc   = get_utc_now()
    latest_5m = df_5m.iloc[-1]

    current_high = max(live_spot, float(latest_5m["High"]))
    current_low  = min(live_spot, float(latest_5m["Low"]))

    for trade in daily_trades_log:
        if trade["pair"] == pair_name and trade["is_open"]:
            if to_utc_timestamp(candle_time) <= trade["entry_candle_time"]:
                continue

            entry_str = format_price(trade["entry"], decimals)
            sl_str    = format_price(trade["sl"], decimals)
            price_str = format_price(live_spot, decimals)

            if not trade["trailed_to_cost"]:
                one_r_reached = (trade["direction"] == "BUY" and current_high >= trade["half_target"]) or \
                                (trade["direction"] == "SELL" and current_low <= trade["half_target"])
                if one_r_reached:
                    trade["trailed_to_cost"] = True
                    trade["sl"] = trade["entry"]
                    send_telegram_message(
                        f"🛡️ <b>1:1 (+1R) REACHED — {trade['mt5_name']}</b>\n"
                        f"📍 <b>Live Price:</b> <code>{price_str}</code>\n"
                        f"👉 Move Stop-Loss to Entry (<code>{entry_str}</code>) to make this trade 100% risk-free."
                    )

            hit_type = None
            if trade["direction"] == "BUY":
                if current_high >= trade["target"]:
                    hit_type = "TARGET"
                elif current_low <= trade["sl"]:
                    hit_type = "BREAKEVEN_SL" if trade["trailed_to_cost"] else "SL"
            else:
                if current_low <= trade["target"]:
                    hit_type = "TARGET"
                elif current_high >= trade["sl"]:
                    hit_type = "BREAKEVEN_SL" if trade["trailed_to_cost"] else "SL"

            if hit_type == "TARGET":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=30)
                net_win = trade["expected_profit"]
                daily_realized_pnl  += net_win
                weekly_realized_pnl += net_win
                total_account_pnl   += net_win
                append_to_trading_journal(trade, +2.0, net_win, "WIN")
                send_telegram_message(
                    f"🎯 <b>1:2 FULL TARGET HIT (+${net_win:.2f} / +2R) — {trade['mt5_name']}</b>\n"
                    f"📒 Logged in Journal. Send <code>/journal</code> or <code>/report</code> to view full metrics."
                )
            elif hit_type == "BREAKEVEN_SL":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=20)
                be_cost = -trade.get("cost_usd", 0.35)
                daily_realized_pnl  += be_cost
                weekly_realized_pnl += be_cost
                total_account_pnl   += be_cost
                append_to_trading_journal(trade, 0.0, be_cost, "BREAKEVEN")
                send_telegram_message(
                    f"🛡️ <b>BREAKEVEN EXIT (0R) — {trade['mt5_name']}</b>\n"
                    f"Capital preserved and logged in <code>/journal</code>."
                )
            elif hit_type == "SL":
                trade["is_open"] = False
                losses_today += 1
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=45)
                loss_amt = trade["max_risk"]
                daily_realized_pnl  -= loss_amt
                weekly_realized_pnl -= loss_amt
                total_account_pnl   -= loss_amt
                append_to_trading_journal(trade, -1.0, -loss_amt, "LOSS")
                send_telegram_message(
                    f"🛑 <b>INVALIDATION STOP-LOSS HIT (-${loss_amt:.2f} / -1R) — {trade['mt5_name']}</b> (<code>{sl_str}</code>)\n"
                    f"Losses Today: {losses_today}/{MAX_DAILY_LOSS_COUNT}. Do NOT increase risk on the next trade."
                )

# =====================================================================
# 11. MASTER SCANNER LOOP
# =====================================================================
def scan_markets():
    global trades_today, losses_today, current_day, current_week
    global daily_trades_log, daily_realized_pnl, weekly_realized_pnl
    global daily_halt_announced, weekly_halt_announced, pre_session_brief_sent

    now_ist  = get_ist_now()
    today    = now_ist.date()
    iso_week = now_ist.isocalendar()[1]

    if today != current_day:
        current_day = today
        trades_today = 0
        losses_today = 0
        daily_trades_log = []
        daily_realized_pnl = 0.0
        daily_halt_announced = False
        for k in live_bars_memory:
            live_bars_memory[k] = {}

    if iso_week != current_week:
        current_week = iso_week
        weekly_realized_pnl = 0.0
        weekly_halt_announced = False

    ny_ok, is_pre_session, ny_status_txt, ist_window_str = get_ny_session_status()
    if is_pre_session and pre_session_brief_sent != today:
        pre_session_brief_sent = today
        today_name = WEEKDAY_NAMES[get_ny_weekday_index(get_utc_now())]
        send_telegram_message(
            f"🔔 <b>NEW YORK PRE-SESSION ROUTINE ({today_name})</b> 🔔\n\n"
            f"🕒 <b>NY Session Window:</b> {ist_window_str}\n"
            f"📋 <b>Pre-Session Checklist (PDF Section 3):</b>\n"
            f"• Risk per trade: <b>$12.50 (0.25%)</b> | Max <b>2 trades</b> today\n"
            f"• Sending pre-marked PDH/PDL & Asian/London High-Low charts for <b>XAUUSD</b> and <b>BTCUSD</b>..."
        )
        send_instant_preview_chart("XAU/USD")
        send_instant_preview_chart("BTC/USD")

    check_pending_pre_alerts_realtime()

    now_utc = get_utc_now()
    live_prices_map = {}

    for name, info in FOREX_PAIRS.items():
        check_telegram_commands()

        if not is_forex_market_open() and not info.get("is_crypto", False):
            continue
        try:
            df_1h, df_15m, df_5m, live_spot = get_roadmap_3tf_spot_data(name, info, use_fast_cache=False)
            if df_1h is None or df_15m is None or df_5m is None or live_spot is None:
                continue

            live_prices_map[name] = live_spot

            df_1h  = enrich_price_action_metrics(df_1h)
            df_15m = enrich_price_action_metrics(df_15m)
            df_5m  = enrich_price_action_metrics(df_5m)

            closed_time = to_utc_timestamp(df_5m.index[-2])
            dec         = info["decimals"]

            manage_open_trades(name, closed_time, live_spot, df_5m, dec)

            can_trade, _, _ = check_funded_account_risk_rules(live_prices_map)
            if not can_trade:
                continue

            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue
            if pending_pre_alerts.get(name) is not None:
                continue
            if last_candle_traded[name] == closed_time:
                continue

            cand = evaluate_roadmap_sweep_mss_setup(name, info, df_1h, df_15m, df_5m, live_spot, ignore_locks=False)

            if cand is not None:
                if cand["stage"] == "CONFIRMED_BREAKOUT":
                    dispatch_confirmed_trade_alert(name, info, df_1h, df_15m, df_5m, live_spot, cand, closed_time)
                elif cand["stage"] == "PRE_ALERT":
                    if now_utc >= pre_alert_cooldown.get(name, datetime.min.replace(tzinfo=timezone.utc)) and last_pre_alert_candle.get(name) != closed_time:
                        dispatch_pre_alert_setup(name, info, df_1h, df_15m, df_5m, live_spot, cand, closed_time)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    fetch_live_economic_calendar()
    _, _, ny_stat, ist_win = get_ny_session_status()
    send_telegram_message(
        f"📊 <b>$5,000 Two-Step Funded Roadmap Bot + Day-Wise Backtest Active</b>\n"
        f"• <b>Markets:</b> XAUUSD (Primary) + BTCUSD (Secondary)\n"
        f"• <b>Setup:</b> 1H Bias ➔ 15M Session/PDH-PDL Sweep ➔ 5M MSS Body Close (1:2 RR)\n"
        f"• <b>Risk Rules:</b> $12.50/trade (0.25%) | Max 2 trades/day | -$25 Daily / -$75 Weekly Stop\n"
        f"• <b>Reports:</b> Send <code>/backtest</code>, <code>/report</code>, or <code>/days</code> for full Day-Wise (Mon–Fri) & Stage 6 analytics!"
    )

    start_run      = time.time()
    last_scan_time = 0
    is_github_ci   = os.getenv("GITHUB_ACTIONS", "").lower() == "true"
    max_run_secs   = 21000 if is_github_ci else 999999999

    while time.time() - start_run < max_run_secs:
        try:
            check_telegram_commands()
            if time.time() - last_scan_time >= 10:
                scan_markets()
                last_scan_time = time.time()
            time.sleep(0.5)
        except KeyboardInterrupt:
            print("Stopped manually.")
            break
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(3)
