import os
import io
import csv
import sys
import time
import threading
import subprocess
from concurrent.futures import ThreadPoolExecutor
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

TELEGRAM_SESSION = requests.Session()
TELEGRAM_SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Connection": "keep-alive"
})

DATA_EXECUTOR = ThreadPoolExecutor(max_workers=6)

# =====================================================================
# 1. PDF ROADMAP CONFIGURATION ($5,000 TWO-STEP FUNDED ACCOUNT)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN") or "YOUR_TELEGRAM_BOT_TOKEN"
CHAT_ID   = os.getenv("CHAT_ID") or "5610520408"

STARTING_CAPITAL_USD         = 5000.0
HIGH_WATER_MARK_USD          = 5000.0

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

STRICT_SESSION_ONLY          = True      # London + New York active windows
LOW_SPREAD_FILTER_ENABLED    = True      # Trade only during low-spread liquidity hours
JOURNAL_CSV_PATH             = "trading_journal.csv"

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKDAY_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

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
        "spread_and_cost": 0.35,
        "low_spread_utc_windows": [(7.0, 11.0), (13.0, 17.0)],
        "unit_name": "Pts",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 2.80,
        "max_sl_dist": 12.15,
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
        "spread_and_cost": 28.00,
        "low_spread_utc_windows": [(7.0, 21.0)],
        "unit_name": "Pts",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 160.0,
        "max_sl_dist": 1200.0,
        "min_lot": 0.01,
        "max_lot_cap": 0.05,
        "is_crypto": True
    }
}

# Runtime State Tracking (100% Zero-Start)
last_candle_traded      = {name: None for name in FOREX_PAIRS}
last_pre_alert_candle   = {name: None for name in FOREX_PAIRS}
last_setup_traded       = {name: None for name in FOREX_PAIRS}
last_radar_key          = {name: None for name in FOREX_PAIRS}
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
    dt_utc = to_utc_timestamp(ts_utc)
    try:
        ny_dt = dt_utc.astimezone(ZoneInfo("America/New_York"))
    except Exception:
        ny_dt = dt_utc - timedelta(hours=5)
    return ny_dt.weekday()

current_day  = get_ist_now().date()
current_week = get_ist_now().isocalendar()[1]

# =====================================================================
# 2. TELEGRAM-ONLY RESET & MEMORY ENGINE (0-START)
# =====================================================================
def reset_live_account_to_zero():
    """Resets live account and /backtest data cleanly back to $5,000.00 and 0 trades."""
    global HIGH_WATER_MARK_USD, total_account_pnl, daily_realized_pnl, weekly_realized_pnl
    global trades_today, losses_today, daily_trades_log, journal_memory_log
    global daily_halt_announced, weekly_halt_announced, dd_halt_announced, last_radar_key
    HIGH_WATER_MARK_USD   = STARTING_CAPITAL_USD
    total_account_pnl     = 0.0
    daily_realized_pnl    = 0.0
    weekly_realized_pnl   = 0.0
    trades_today          = 0
    losses_today          = 0
    daily_trades_log      = []
    journal_memory_log    = []
    last_radar_key        = {name: None for name in FOREX_PAIRS}
    daily_halt_announced  = False
    weekly_halt_announced = False
    dd_halt_announced     = False
    if os.path.isfile(JOURNAL_CSV_PATH):
        try:
            os.remove(JOURNAL_CSV_PATH)
        except Exception:
            pass

# =====================================================================
# 3. LONDON + NEW YORK SESSION, LOW-SPREAD & NEWS ENGINE
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

def get_active_sessions_info():
    """Returns (is_trading_allowed, is_pre_session, status_label, sessions_summary)."""
    sessions_str = "London (12:30–4:30 PM IST) + New York (6:30–10:30 PM IST)"
    if not is_forex_market_open():
        return False, False, "Weekend Closed", sessions_str

    utc_now = get_utc_now()
    utc_hr  = utc_now.hour + (utc_now.minute / 60.0)

    in_london = (7.0 <= utc_hr <= 11.0)
    in_ny     = (13.0 <= utc_hr <= 17.0)
    is_pre_london = (6.75 <= utc_hr < 7.0)
    is_pre_ny     = (12.75 <= utc_hr < 13.0)

    if in_london and in_ny:
        return True, False, "🟢 London + NY Overlap Active", sessions_str
    if in_london:
        return True, False, "🟢 London Session Active", sessions_str
    if in_ny:
        return True, False, "🟢 NY Session Active", sessions_str

    if not STRICT_SESSION_ONLY:
        return True, (is_pre_london or is_pre_ny), f"🟢 24H Mode ({sessions_str})", sessions_str

    return False, (is_pre_london or is_pre_ny), f"⏸️ Waiting ({sessions_str})", sessions_str

def check_low_spread_window(pair_info):
    """
    Checks whether current UTC time is inside low-spread liquidity hours for the pair.
    Returns (is_low_spread_ok, label_text).
    """
    if not LOW_SPREAD_FILTER_ENABLED:
        return True, "🟢 Spread Filter Off"

    if pair_info.get("is_crypto", False):
        if not is_forex_market_open():
            pass
    else:
        if not is_forex_market_open():
            return False, "🔴 Weekend Wide Spread"

    utc_now = get_utc_now()
    utc_hr  = utc_now.hour + (utc_now.minute / 60.0)

    # Daily rollover / thin liquidity spike window (21:45 - 00:30 UTC)
    if (not pair_info.get("is_crypto", False)) and (utc_hr >= 21.75 or utc_hr < 0.50):
        return False, "🔴 Rollover Wide Spread"

    windows = pair_info.get("low_spread_utc_windows", [(7.0, 11.0), (13.0, 17.0)])
    for start_hr, end_hr in windows:
        if start_hr <= utc_hr <= end_hr:
            return True, "🟢 Low Spread Active"

    return False, "🟡 Elevated Spread Window"

def get_dynamic_spread_cost(pair_info):
    base_cost = float(pair_info.get("spread_and_cost", 0.0))
    spread_ok, _ = check_low_spread_window(pair_info)
    return base_cost if spread_ok else round(base_cost * 1.5, 4)

def fetch_live_economic_calendar():
    global cached_news_events, last_news_fetch_time
    now_ts = time.time()
    if (now_ts - last_news_fetch_time) < 1800 and cached_news_events:
        return cached_news_events
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    try:
        res = HTTP_SESSION.get(url, timeout=3.5)
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
# 4. $5,000 TWO-STEP RISK MANAGEMENT & JOURNAL ENGINE
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
                    t["max_r_seen"] = round(max(float(t.get("max_r_seen", 0.0)), r_move), 2)
                    t["min_r_seen"] = round(min(float(t.get("min_r_seen", 0.0)), r_move), 2)

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
                f"Trading paused to review rule compliance."
            )
        return False, 0.0, "Max Drawdown Review Threshold (-$100)"

    if week_net_pnl <= -WEEKLY_STOP_LOSS_USD:
        if not weekly_halt_announced:
            weekly_halt_announced = True
            send_telegram_message(
                f"🛑 <b>WEEKLY STOP REACHED (-${abs(week_net_pnl):.2f} / 1.5%)</b>\n"
                f"Weekly loss reached -$75. Trading stopped for the week."
            )
        return False, 0.0, "Weekly Stop Reached (-$75)"

    if day_net_pnl <= -DAILY_STOP_LOSS_USD or losses_today >= MAX_DAILY_LOSS_COUNT:
        if not daily_halt_announced:
            daily_halt_announced = True
            send_telegram_message(
                f"🛡️ <b>DAILY STOP TRIGGERED (Today: ${day_net_pnl:+.2f} | Losses: {losses_today}/2)</b>\n"
                f"Maximum daily stop (-$25 or 2 losses) reached. Done for today."
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

    spread_cost_dist  = get_dynamic_spread_cost(pair_info)
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
    mfe_r  = float(trade_record.get("max_r_seen", max(0.0, result_r_val)))
    mae_r  = float(trade_record.get("min_r_seen", min(0.0, result_r_val)))
    row = {
        "Date / time": get_ist_now().strftime("%Y-%m-%d %I:%M %p IST"),
        "Day": wd_nam,
        "Market": trade_record["mt5_name"],
        "Direction": trade_record.get("direction", "BUY"),
        "1H bias": trade_record.get("h1_bias", "N/A"),
        "Level / sweep": trade_record.get("swept_level_name", "Key Level Sweep"),
        "5M confirmation": trade_record.get("mss_confirmation", "5M MSS + Close"),
        "Entry / SL / TP": f"E:{trade_record['entry']} | SL:{trade_record['sl']} | TP:{trade_record['target']}",
        "Lot": trade_record.get("lot_size", 0.01),
        "Risk $": f"${trade_record['max_risk']:.2f}",
        "Result (R)": f"{result_r_val:+.2f}R (${net_pnl_usd:+.2f}) [{result_label} · Peak:{mfe_r:+.1f}R/Dip:{mae_r:+.1f}R]",
        "Rule followed?": "YES",
        "weekday_idx": wd_idx,
        "numeric_r": float(result_r_val),
        "numeric_pnl": float(net_pnl_usd),
        "cost_usd": float(trade_record.get("cost_usd", 0.35))
    }
    journal_memory_log.append(row)
    csv_cols = ["Date / time", "Day", "Market", "1H bias", "Level / sweep", "5M confirmation", "Entry / SL / TP", "Risk $", "Result (R)", "Rule followed?"]

    def _write_csv():
        file_exists = os.path.isfile(JOURNAL_CSV_PATH)
        try:
            with open(JOURNAL_CSV_PATH, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=csv_cols)
                if not file_exists:
                    writer.writeheader()
                writer.writerow({k: row[k] for k in csv_cols})
        except Exception as e:
            print(f"Journal Write Warning: {e}")

    threading.Thread(target=_write_csv, daemon=True).start()

# =====================================================================
# 5. HIGH-SPEED PARALLEL DATA & TELEGRAM PHOTO ENGINE
# =====================================================================
def send_telegram_chat_action(action="upload_photo", target_chat_id=CHAT_ID):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return
    try:
        TELEGRAM_SESSION.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendChatAction",
            json={"chat_id": target_chat_id, "action": action},
            timeout=1.5
        )
    except Exception:
        pass

def send_telegram_message(message, target_chat_id=CHAT_ID):
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print(f"\n[TELEGRAM]:\n{message}\n")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": message, "parse_mode": "HTML"}
    for attempt in range(2):
        try:
            r = TELEGRAM_SESSION.post(url, json=payload, timeout=5) if attempt == 0 else requests.post(url, json=payload, timeout=7)
            if r.status_code == 200:
                return
        except Exception as e:
            print(f"Telegram Send Error ({attempt}): {e}")

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
        res = HTTP_SESSION.post(url, json=payload, timeout=2.0)
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

def fetch_binance_vision_ohlc(binance_symbol, interval_str="5m", limit=220):
    if not binance_symbol:
        return None
    for base_url in ["https://data-api.binance.vision/api/v3/klines", "https://api.binance.com/api/v3/klines"]:
        params = {"symbol": binance_symbol, "interval": interval_str, "limit": limit}
        try:
            r = HTTP_SESSION.get(base_url, params=params, timeout=3.0).json()
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

def get_roadmap_3tf_spot_data(pair_name, info, use_fast_cache=True):
    now_ts     = time.time()
    now_utc    = get_utc_now()
    bucket_min = (now_utc.minute // 5) * 5
    bucket_ts  = now_utc.replace(minute=bucket_min, second=0, microsecond=0)
    hour_ts    = now_utc.replace(minute=0, second=0, microsecond=0)

    raw_df_1h, raw_df_15m, raw_df_5m = None, None, None
    tv_future = DATA_EXECUTOR.submit(
        fetch_tradingview_live_candle_and_spot,
        info.get("tv_tickers", []),
        info.get("tv_screener", "cfd")
    )

    if use_fast_cache and pair_name in ohlc_history_cache:
        cached = ohlc_history_cache[pair_name]
        if (now_ts - cached["ts"]) <= 45.0 and cached.get("bucket_ts") == bucket_ts:
            raw_df_1h  = cached["raw_df_1h"].copy()
            raw_df_15m = cached["raw_df_15m"].copy()
            raw_df_5m  = cached["raw_df_5m"].copy()

    if raw_df_1h is None or raw_df_15m is None or raw_df_5m is None:
        if info.get("binance_symbol"):
            sym = info["binance_symbol"]
            f_1h  = DATA_EXECUTOR.submit(fetch_binance_vision_ohlc, sym, "1h", 140)
            f_15m = DATA_EXECUTOR.submit(fetch_binance_vision_ohlc, sym, "15m", 180)
            f_5m  = DATA_EXECUTOR.submit(fetch_binance_vision_ohlc, sym, "5m", 160)
            raw_df_1h  = f_1h.result()
            raw_df_15m = f_15m.result()
            raw_df_5m  = f_5m.result()

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

    df_1h   = raw_df_1h.copy()
    df_15m  = raw_df_15m.copy()
    df_5m   = raw_df_5m.copy()
    tv_data = tv_future.result()

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
# 6. PRE-SESSION KEY LEVELS & 1H CONTEXT ENGINE
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
    if isinstance(df.index, pd.DatetimeIndex):
        utc_idx = df.index.tz_convert("UTC") if df.index.tz is not None else df.index.tz_localize("UTC")
        df["utc_date"] = utc_idx.date
        df["utc_hour"] = utc_idx.hour + (utc_idx.minute / 60.0)
    return df

def extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, decimals):
    closed_1h = df_1h.iloc[:-1] if len(df_1h) > 25 else df_1h
    atr_1h    = float(closed_1h.iloc[-1]["ATR"])
    if np.isnan(atr_1h) or atr_1h <= 0:
        atr_1h = float((closed_1h["High"] - closed_1h["Low"]).mean())

    df_15m_utc = df_15m.iloc[-140:]
    if "utc_date" not in df_15m_utc.columns or "utc_hour" not in df_15m_utc.columns:
        df_15m_utc = df_15m_utc.copy()
        df_15m_utc["utc_date"] = [to_utc_timestamp(ts).date() for ts in df_15m_utc.index]
        df_15m_utc["utc_hour"] = [to_utc_timestamp(ts).hour + to_utc_timestamp(ts).minute / 60.0 for ts in df_15m_utc.index]

    today_utc    = df_15m_utc["utc_date"].iloc[-1]
    unique_dates = sorted(pd.unique(df_15m_utc["utc_date"]))
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
# 7. SETUP ENGINES: (A) 15M SWEEP + 5M MSS  |  (B) HTF PULLBACK TO OB/FVG AFTER BOS
# =====================================================================
def detect_5m_fvg_zone(df_5m, direction, lookback=8):
    n = len(df_5m)
    if n < lookback + 4:
        return None
    for idx in range(n - 2, max(2, n - 2 - lookback), -1):
        c_curr  = df_5m.iloc[idx]
        c_prev2 = df_5m.iloc[idx - 2]
        if direction == "BUY":
            gap_bot = float(c_prev2["High"])
            gap_top = float(c_curr["Low"])
            if gap_top > gap_bot:
                return {"top": gap_top, "bot": gap_bot, "mid": (gap_top + gap_bot) / 2.0, "x_idx": idx - 2}
        else:
            gap_top = float(c_prev2["Low"])
            gap_bot = float(c_curr["High"])
            if gap_top > gap_bot:
                return {"top": gap_top, "bot": gap_bot, "mid": (gap_top + gap_bot) / 2.0, "x_idx": idx - 2}
    return None

def detect_5m_bos_and_order_block(df_5m, direction, decimals):
    n = len(df_5m)
    if n < 14:
        return None

    highs = df_5m["High"].values.astype(float)
    lows  = df_5m["Low"].values.astype(float)
    opens = df_5m["Open"].values.astype(float)
    close_last = float(df_5m.iloc[-2]["Close"])

    if direction == "BUY":
        best_bos = None
        for b_idx in range(n - 2, max(5, n - 14), -1):
            prior_high = float(np.max(highs[max(0, b_idx - 7):b_idx - 1]))
            if float(highs[b_idx]) > prior_high and float(df_5m.iloc[b_idx]["Close"]) > prior_high:
                ob_idx = None
                for k in range(b_idx - 1, max(0, b_idx - 7), -1):
                    if float(df_5m.iloc[k]["Close"]) < opens[k]:
                        ob_idx = k
                        break
                if ob_idx is not None:
                    ob_top = round(float(max(opens[ob_idx], float(df_5m.iloc[ob_idx]["Close"]), highs[ob_idx])), decimals)
                    ob_bot = round(float(lows[ob_idx]), decimals)
                    best_bos = {
                        "bos_price": round(prior_high, decimals),
                        "bos_x": b_idx,
                        "ob": {"top": ob_top, "bot": ob_bot, "mid": round((ob_top + ob_bot) / 2.0, decimals), "x_idx": ob_idx}
                    }
                    break
        if not best_bos:
            return None
        fvg = detect_5m_fvg_zone(df_5m, "BUY", lookback=8)
        zone = best_bos["ob"]
        in_ob  = (close_last <= zone["top"] * 1.0005) and (close_last >= zone["bot"] * 0.9985)
        in_fvg = (fvg is not None) and (close_last <= fvg["top"]) and (close_last >= fvg["bot"])
        if in_ob or in_fvg:
            return {
                "bos_price": best_bos["bos_price"],
                "bos_x": best_bos["bos_x"],
                "ob_zone": best_bos["ob"],
                "fvg_zone": fvg,
                "active_zone": fvg if (in_fvg and not in_ob) else best_bos["ob"],
                "zone_name": "5M FVG" if (in_fvg and not in_ob) else "5M Bullish OB"
            }
        return None

    else:
        best_bos = None
        for b_idx in range(n - 2, max(5, n - 14), -1):
            prior_low = float(np.min(lows[max(0, b_idx - 7):b_idx - 1]))
            if float(lows[b_idx]) < prior_low and float(df_5m.iloc[b_idx]["Close"]) < prior_low:
                ob_idx = None
                for k in range(b_idx - 1, max(0, b_idx - 7), -1):
                    if float(df_5m.iloc[k]["Close"]) > opens[k]:
                        ob_idx = k
                        break
                if ob_idx is not None:
                    ob_bot = round(float(min(opens[ob_idx], float(df_5m.iloc[ob_idx]["Close"]), lows[ob_idx])), decimals)
                    ob_top = round(float(highs[ob_idx]), decimals)
                    best_bos = {
                        "bos_price": round(prior_low, decimals),
                        "bos_x": b_idx,
                        "ob": {"top": ob_top, "bot": ob_bot, "mid": round((ob_top + ob_bot) / 2.0, decimals), "x_idx": ob_idx}
                    }
                    break
        if not best_bos:
            return None
        fvg = detect_5m_fvg_zone(df_5m, "SELL", lookback=8)
        zone = best_bos["ob"]
        in_ob  = (close_last >= zone["bot"] * 0.9995) and (close_last <= zone["top"] * 1.0015)
        in_fvg = (fvg is not None) and (close_last >= fvg["bot"]) and (close_last <= fvg["top"])
        if in_ob or in_fvg:
            return {
                "bos_price": best_bos["bos_price"],
                "bos_x": best_bos["bos_x"],
                "ob_zone": best_bos["ob"],
                "fvg_zone": fvg,
                "active_zone": fvg if (in_fvg and not in_ob) else best_bos["ob"],
                "zone_name": "5M FVG" if (in_fvg and not in_ob) else "5M Bearish OB"
            }
        return None

def get_clean_smc_overlays(sub_5m, active_trade=None):
    """
    Returns clean, non-overlapping BOS, FVG, and OB structures for the visible 5M chart window.
    """
    n = len(sub_5m)
    if n < 10:
        return None, None, None

    trade_ref = active_trade if active_trade is not None else {}
    direction = trade_ref.get("direction")
    if direction not in ("BUY", "SELL"):
        last_c = float(sub_5m.iloc[-2]["Close"])
        last_o = float(sub_5m.iloc[-2]["Open"])
        direction = "BUY" if last_c >= last_o else "SELL"

    highs = sub_5m["High"].values.astype(float)
    lows  = sub_5m["Low"].values.astype(float)
    opens = sub_5m["Open"].values.astype(float)
    closes = sub_5m["Close"].values.astype(float)

    bos_info = None
    if trade_ref.get("bos_line") is not None:
        bos_info = {"price": float(trade_ref["bos_line"]), "x_start": max(4, n - 18)}
    else:
        if direction == "BUY":
            for i in range(n - 2, max(5, n - 24), -1):
                lvl = float(np.max(highs[max(0, i - 6):i - 1]))
                if highs[i] > lvl and closes[i] > lvl:
                    bos_info = {"price": round(lvl, 2), "x_start": max(4, i - 4)}
                    break
        else:
            for i in range(n - 2, max(5, n - 24), -1):
                lvl = float(np.min(lows[max(0, i - 6):i - 1]))
                if lows[i] < lvl and closes[i] < lvl:
                    bos_info = {"price": round(lvl, 2), "x_start": max(4, i - 4)}
                    break

    fvg_info = None
    if trade_ref.get("fvg_zone") is not None:
        fz = trade_ref["fvg_zone"]
        fvg_info = {"top": float(fz["top"]), "bot": float(fz["bot"]), "x_start": max(4, n - 12)}
    else:
        for i in range(n - 2, max(2, n - 18), -1):
            if direction == "BUY":
                g_bot = float(highs[i - 2])
                g_top = float(lows[i])
                if g_top > g_bot:
                    fvg_info = {"top": g_top, "bot": g_bot, "x_start": i - 2}
                    break
            else:
                g_top = float(lows[i - 2])
                g_bot = float(highs[i])
                if g_top > g_bot:
                    fvg_info = {"top": g_top, "bot": g_bot, "x_start": i - 2}
                    break

    ob_info = None
    if trade_ref.get("ob_zone") is not None:
        oz = trade_ref["ob_zone"]
        ob_info = {"top": float(oz["top"]), "bot": float(oz["bot"]), "x_start": max(4, n - 15)}
    else:
        for i in range(n - 3, max(2, n - 20), -1):
            if direction == "BUY" and closes[i] < opens[i] and closes[i + 1] > highs[i]:
                ob_info = {"top": float(highs[i]), "bot": float(lows[i]), "x_start": i}
                break
            if direction == "SELL" and closes[i] > opens[i] and closes[i + 1] < lows[i]:
                ob_info = {"top": float(highs[i]), "bot": float(lows[i]), "x_start": i}
                break

    # Prevent OB and FVG boxes from sitting on top of each other with duplicate text
    if fvg_info and ob_info:
        ov_top = min(fvg_info["top"], ob_info["top"])
        ov_bot = max(fvg_info["bot"], ob_info["bot"])
        if ov_top > ov_bot:
            ob_info = None

    return bos_info, fvg_info, ob_info

def check_sweep_proximity_radar(pair_name, info, roadmap, live_spot, atr_5m):
    if roadmap["h1_bias"] == "RANGING" or atr_5m <= 0:
        return
    ny_ok, _, _, _ = get_active_sessions_info()
    spread_ok, _   = check_low_spread_window(info)
    if not ny_ok or not spread_ok:
        return
    if pending_pre_alerts.get(pair_name) is not None:
        return

    prox_band = atr_5m * 0.35
    for lv in roadmap["levels"]:
        aligned = (roadmap["h1_bias"] == "BULLISH" and lv["type"] == "LOW_POOL") or \
                  (roadmap["h1_bias"] == "BEARISH" and lv["type"] == "HIGH_POOL")
        if not aligned:
            continue
        if abs(live_spot - lv["price"]) <= prox_band:
            radar_key = f"{lv['short']}_{lv['price']}"
            if last_radar_key.get(pair_name) != radar_key:
                last_radar_key[pair_name] = radar_key
                dec = info["decimals"]
                send_telegram_message(
                    f"📡 <b>SWEEP RADAR — {info['mt5_name']} APPROACHING LEVEL</b>\n"
                    f"🧭 <b>1H Bias:</b> <b>{roadmap['h1_bias']}</b> | 🧲 <b>Level:</b> <b>{lv['short']} (<code>{format_price(lv['price'], dec)}</code>)</b>\n"
                    f"📍 <b>Live Price:</b> <code>{format_price(live_spot, dec)}</code> — Watch for sweep + MSS or BOS pullback."
                )
            break

def evaluate_roadmap_sweep_mss_setup(pair_name, info, df_1h, df_15m, df_5m, live_spot, ignore_locks=False, return_skip_reason=False):
    if not ignore_locks:
        sess_ok, _, _, _ = get_active_sessions_info()
        if not sess_ok:
            return (None, "OUTSIDE_ACTIVE_SESSION") if return_skip_reason else None
        spread_ok, _ = check_low_spread_window(info)
        if not spread_ok:
            return (None, "WIDE_SPREAD_WINDOW") if return_skip_reason else None
        news_ok, _ = check_news_window_for_pair(info)
        if not news_ok:
            return (None, "HIGH_IMPACT_NEWS") if return_skip_reason else None
        if get_utc_now() < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
            return (None, "COOLDOWN") if return_skip_reason else None

    m5_c0   = df_5m.iloc[-2]
    atr_5m  = float(m5_c0["ATR"])
    body_r  = float(m5_c0["Body_Ratio"])
    close_p = float(m5_c0["Close_Pos"])
    rng_5   = float(m5_c0["Range"])

    if np.isnan(atr_5m) or atr_5m <= 0:
        return (None, "INVALID_ATR") if return_skip_reason else None
    if body_r < 0.40:
        return (None, "WEAK_BODY") if return_skip_reason else None
    if rng_5 > (atr_5m * 1.85):
        return (None, "EXTENDED_CLIMAX_CANDLE") if return_skip_reason else None

    dec     = info["decimals"]
    roadmap = extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, dec)
    h1_bias = roadmap["h1_bias"]
    levels  = roadmap["levels"]

    if not ignore_locks:
        check_sweep_proximity_radar(pair_name, info, roadmap, live_spot, atr_5m)

    if h1_bias == "RANGING" and not ignore_locks:
        return (None, "1H_RANGING_CHOPPY") if return_skip_reason else None

    m15_recent = df_15m.iloc[-6:-1] if len(df_15m) >= 7 else df_15m.iloc[-5:]
    m15_c0     = df_15m.iloc[-2] if len(df_15m) >= 2 else df_15m.iloc[-1]

    m5_c1 = df_5m.iloc[-3]
    m5_c2 = df_5m.iloc[-4]
    m5_c3 = df_5m.iloc[-5]

    p_close = float(m5_c0["Close"])
    p_open  = float(m5_c0["Open"])

    candidates = []
    skipped_for_risk = False
    spread_cost_now = get_dynamic_spread_cost(info)
    buf_5m = round(max(atr_5m * 0.12, spread_cost_now * 0.5), dec)

    # -----------------------------------------------------------------
    # STRATEGY A: 15M LIQUIDITY SWEEP + 5M MSS + FVG
    # -----------------------------------------------------------------
    for lv in levels:
        lvl_price = lv["price"]
        lvl_name  = lv["name"]
        lvl_short = lv["short"]
        lvl_type  = lv["type"]

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
                    ob_info  = detect_5m_bos_and_order_block(df_5m, "BUY", dec)
                    ob_zone  = ob_info["ob_zone"] if ob_info else None
                    bos_line = ob_info["bos_price"] if ob_info else round(mss_neck_high, dec)

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
                            "ob_zone": ob_zone,
                            "bos_line": bos_line,
                            "rr": PRIMARY_RR_TARGET,
                            "score": 90 if mss_body_confirmed else 84,
                            "why_text": (
                                f"1️⃣ <b>1H Context:</b> <b>{h1_bias}</b> structure aligned.\n"
                                f"2️⃣ <b>15M Level Sweep:</b> Swept <b>{lvl_name} (<code>{format_price(lvl_price, dec)}</code>)</b> and closed back above.\n"
                                f"3️⃣ <b>5M Confirmation:</b> <b>{mss_desc}</b> with SL below sweep low (<code>{format_price(invalidation_sl, dec)}</code>)."
                            )
                        })

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
                    ob_info  = detect_5m_bos_and_order_block(df_5m, "SELL", dec)
                    ob_zone  = ob_info["ob_zone"] if ob_info else None
                    bos_line = ob_info["bos_price"] if ob_info else round(mss_neck_low, dec)

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
                            "ob_zone": ob_zone,
                            "bos_line": bos_line,
                            "rr": PRIMARY_RR_TARGET,
                            "score": 90 if mss_body_confirmed else 84,
                            "why_text": (
                                f"1️⃣ <b>1H Context:</b> <b>{h1_bias}</b> structure aligned.\n"
                                f"2️⃣ <b>15M Level Sweep:</b> Swept <b>{lvl_name} (<code>{format_price(lvl_price, dec)}</code>)</b> and closed back below.\n"
                                f"3️⃣ <b>5M Confirmation:</b> <b>{mss_desc}</b> with SL above sweep high (<code>{format_price(invalidation_sl, dec)}</code>)."
                            )
                        })

    # -----------------------------------------------------------------
    # STRATEGY B: HTF PULLBACK TO ORDER BLOCK / FVG AFTER 5M BOS
    # -----------------------------------------------------------------
    if h1_bias in ["BULLISH", "BEARISH"]:
        pb_info = detect_5m_bos_and_order_block(df_5m, "BUY" if h1_bias == "BULLISH" else "SELL", dec)
        if pb_info is not None:
            az = pb_info["active_zone"]
            ob_z = pb_info["ob_zone"]
            fvg_z = pb_info["fvg_zone"]
            if h1_bias == "BULLISH" and p_close > p_open and close_p >= 0.55:
                entry_spot = round(p_close, dec)
                invalidation_sl = round(min(float(df_5m.iloc[-6:-1]["Low"].min()), az["bot"]) - (atr_5m * 0.20), dec)
                sl_dist = round(abs(entry_spot - invalidation_sl), dec)
                if sl_dist < info["min_sl_dist"]:
                    sl_dist = info["min_sl_dist"]
                    invalidation_sl = round(entry_spot - sl_dist, dec)
                lot_size, planned_risk_usd, cost_usd = calculate_roadmap_position_size(pair_name, info, sl_dist, RISK_PER_TRADE_USD)
                if lot_size is None or sl_dist > info["max_sl_dist"]:
                    skipped_for_risk = True
                else:
                    tp1_price = round(entry_spot + (sl_dist * BREAKEVEN_LOCK_R), dec)
                    tp2_price = round(entry_spot + (sl_dist * PRIMARY_RR_TARGET), dec)
                    candidates.append({
                        "stage": "CONFIRMED_BREAKOUT",
                        "direction": "BUY",
                        "action": "BUY (LONG) 🟢",
                        "h1_bias": h1_bias,
                        "swept_level_name": f"5M BOS ({format_price(pb_info['bos_price'], dec)}) ➔ {pb_info['zone_name']}",
                        "swept_level_short": f"BOS➔{pb_info['zone_name']}",
                        "swept_level_price": pb_info["bos_price"],
                        "mss_confirmation": f"HTF Pullback to {pb_info['zone_name']} After 5M BOS",
                        "short_tag": f"1H BULL + 5M BOS + {pb_info['zone_name']}",
                        "setup_id": f"BOS_PB_BUY_{pb_info['bos_price']}",
                        "entry_spot": entry_spot,
                        "sl_price": invalidation_sl,
                        "sl_dist": sl_dist,
                        "lock_level": tp1_price,
                        "tp1_price": tp1_price,
                        "tp2_price": tp2_price,
                        "tp2_dist": round(sl_dist * PRIMARY_RR_TARGET, dec),
                        "lot_size": lot_size,
                        "planned_risk_usd": planned_risk_usd,
                        "cost_usd": cost_usd,
                        "fvg_zone": fvg_z,
                        "ob_zone": ob_z,
                        "bos_line": pb_info["bos_price"],
                        "rr": PRIMARY_RR_TARGET,
                        "score": 88,
                        "why_text": (
                            f"1️⃣ <b>1H Context:</b> <b>BULLISH</b> trend aligned.\n"
                            f"2️⃣ <b>5M BOS:</b> Broke structure above <code>{format_price(pb_info['bos_price'], dec)}</code>.\n"
                            f"3️⃣ <b>Pullback Entry:</b> Rejected from <b>{pb_info['zone_name']}</b> with SL at <code>{format_price(invalidation_sl, dec)}</code>."
                        )
                    })

            elif h1_bias == "BEARISH" and p_close < p_open and close_p <= 0.45:
                entry_spot = round(p_close, dec)
                invalidation_sl = round(max(float(df_5m.iloc[-6:-1]["High"].max()), az["top"]) + (atr_5m * 0.20), dec)
                sl_dist = round(abs(invalidation_sl - entry_spot), dec)
                if sl_dist < info["min_sl_dist"]:
                    sl_dist = info["min_sl_dist"]
                    invalidation_sl = round(entry_spot + sl_dist, dec)
                lot_size, planned_risk_usd, cost_usd = calculate_roadmap_position_size(pair_name, info, sl_dist, RISK_PER_TRADE_USD)
                if lot_size is None or sl_dist > info["max_sl_dist"]:
                    skipped_for_risk = True
                else:
                    tp1_price = round(entry_spot - (sl_dist * BREAKEVEN_LOCK_R), dec)
                    tp2_price = round(entry_spot - (sl_dist * PRIMARY_RR_TARGET), dec)
                    candidates.append({
                        "stage": "CONFIRMED_BREAKOUT",
                        "direction": "SELL",
                        "action": "SELL (SHORT) 🔴",
                        "h1_bias": h1_bias,
                        "swept_level_name": f"5M BOS ({format_price(pb_info['bos_price'], dec)}) ➔ {pb_info['zone_name']}",
                        "swept_level_short": f"BOS➔{pb_info['zone_name']}",
                        "swept_level_price": pb_info["bos_price"],
                        "mss_confirmation": f"HTF Pullback to {pb_info['zone_name']} After 5M BOS",
                        "short_tag": f"1H BEAR + 5M BOS + {pb_info['zone_name']}",
                        "setup_id": f"BOS_PB_SELL_{pb_info['bos_price']}",
                        "entry_spot": entry_spot,
                        "sl_price": invalidation_sl,
                        "sl_dist": sl_dist,
                        "lock_level": tp1_price,
                        "tp1_price": tp1_price,
                        "tp2_price": tp2_price,
                        "tp2_dist": round(sl_dist * PRIMARY_RR_TARGET, dec),
                        "lot_size": lot_size,
                        "planned_risk_usd": planned_risk_usd,
                        "cost_usd": cost_usd,
                        "fvg_zone": fvg_z,
                        "ob_zone": ob_z,
                        "bos_line": pb_info["bos_price"],
                        "rr": PRIMARY_RR_TARGET,
                        "score": 88,
                        "why_text": (
                            f"1️⃣ <b>1H Context:</b> <b>BEARISH</b> trend aligned.\n"
                            f"2️⃣ <b>5M BOS:</b> Broke structure below <code>{format_price(pb_info['bos_price'], dec)}</code>.\n"
                            f"3️⃣ <b>Pullback Entry:</b> Rejected from <b>{pb_info['zone_name']}</b> with SL at <code>{format_price(invalidation_sl, dec)}</code>."
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
# 8. ZERO-START LIVE PERFORMANCE & DAY-WISE (MON–FRI) ENGINE
# =====================================================================
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
        "total": total, "wins": win_cnt, "losses": loss_cnt, "breakevens": be_cnt,
        "win_rate": round(win_rate, 1), "decisive_wr": round(decisive_wr, 1),
        "non_loss_rate": round(non_loss_rate, 1), "avg_win_usd": round(avg_win_usd, 2),
        "avg_loss_usd": round(avg_loss_usd, 2), "avg_r": round(avg_r, 2),
        "expectancy_usd": round(expectancy_usd, 2), "profit_factor": round(profit_factor, 2),
        "net_pnl_usd": round(net_pnl_usd, 2), "total_costs_usd": round(total_costs, 2),
        "max_dd_usd": round(max_dd, 2), "max_dd_pct": round(max_dd_pct, 2),
        "final_equity": round(curr_eq, 2), "equity_curve": eq_curve, "dd_curve": dd_curve
    }

def compute_day_wise_breakdown(trades_list):
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
            "day_name": WEEKDAY_NAMES[d_idx], "day_short": WEEKDAY_SHORT[d_idx],
            "total": len(d_trades), "wins": wins, "breakevens": bes, "losses": losses,
            "wr": round(wr_pct, 0), "avg_r": avg_r, "net_pnl": net_pnl
        }

    active_weekdays = [day_stats[i] for i in range(5) if day_stats[i]["total"] > 0]
    best_day  = max(active_weekdays, key=lambda x: x["net_pnl"]) if active_weekdays else None
    worst_day = min(active_weekdays, key=lambda x: x["net_pnl"]) if active_weekdays else None

    return {"days": day_stats, "best_day": best_day, "worst_day": worst_day}

# =====================================================================
# 9. HIGH-SPEED CLEAN CHARTS & TELEGRAM REPORTS
# =====================================================================
def generate_visual_backtest_report_chart(combined_stats, xau_stats, btc_stats, day_breakdown):
    try:
        fig = plt.figure(figsize=(11.0, 6.2), dpi=100, facecolor="#ffffff")

        ax1 = fig.add_axes([0.06, 0.50, 0.90, 0.41], facecolor="#ffffff")
        eq_curve = combined_stats["equity_curve"]
        if len(eq_curve) == 1:
            eq_curve = [STARTING_CAPITAL_USD, STARTING_CAPITAL_USD]
        x_idx = np.arange(len(eq_curve))

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
            f"$5,000 Live Forward-Test Equity Curve (0-Start | Trades: {combined_stats['total']} | Final: ${combined_stats['final_equity']:,.2f} | Net: ${combined_stats['net_pnl_usd']:+.2f})",
            fontsize=10.0, fontweight="bold", color="#131722", pad=8
        )
        ax1.set_ylabel("Equity ($ USD)", fontsize=8.6, fontweight="bold", color="#131722")
        ax1.legend(loc="upper left", fontsize=8.0, frameon=True)

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
        ax_day.set_title("Day-Wise Net Profit ($ Mon – Fri)", fontsize=8.8, fontweight="bold", color="#131722")
        ax_day.set_ylabel("Net Profit ($)", fontsize=8.0, color="#131722")

        for b, wr_val in zip(d_bars, d_wrs):
            yv = b.get_height()
            ax_day.text(
                b.get_x() + b.get_width() / 2.0,
                yv + (0.5 if yv >= 0 else -2.5),
                f"${yv:+.0f}\n({wr_val:.0f}%)",
                ha="center", va="bottom" if yv >= 0 else "top", fontsize=7.0, fontweight="bold", color="#131722"
            )

        ax2 = fig.add_axes([0.46, 0.08, 0.23, 0.32], facecolor="#ffffff")
        for sp in ["top", "right"]:
            ax2.spines[sp].set_visible(False)
        ax2.grid(True, axis="y", linestyle="-", linewidth=0.45, color="#f0f3fa")

        markets = [f"XAUUSD\n({xau_stats['total']})", f"BTCUSD\n({btc_stats['total']})"]
        pnls    = [xau_stats["net_pnl_usd"], btc_stats["net_pnl_usd"]]
        colors  = ["#089981" if p >= 0 else "#f23645" for p in pnls]

        bars = ax2.bar(markets, pnls, width=0.48, color=colors, alpha=0.88)
        ax2.axhline(0, color="#475569", linewidth=0.8)
        ax2.set_title("XAUUSD vs BTCUSD", fontsize=8.8, fontweight="bold", color="#131722")

        for b, st in zip(bars, [xau_stats, btc_stats]):
            y_v = b.get_height()
            ax2.text(
                b.get_x() + b.get_width() / 2.0,
                y_v + (0.5 if y_v >= 0 else -2.5),
                f"${y_v:+.0f}\n({st['decisive_wr']:.0f}%)",
                ha="center", va="bottom" if y_v >= 0 else "top", fontsize=7.2, fontweight="bold", color="#131722"
            )

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
            fontsize=8.8, fontweight="bold", color="#131722"
        )
        for b in b3:
            h_v = int(b.get_height())
            ax3.text(b.get_x() + b.get_width() / 2.0, h_v + 0.05, str(h_v), ha="center", va="bottom", fontsize=8.0, fontweight="bold", color="#131722")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100, facecolor="#ffffff")
        plt.close(fig)
        buf.seek(0)
        return buf
    except Exception as e:
        print(f"Backtest Chart Error: {e}")
        return None

def run_and_send_master_backtest_report(target_pair=None):
    send_telegram_chat_action("upload_photo")

    xau_trades, btc_trades, all_trades = [], [], []
    for r in journal_memory_log:
        mkt = r.get("Market", "")
        if target_pair == "XAU/USD" and mkt != "XAUUSD":
            continue
        if target_pair == "BTC/USD" and mkt != "BTCUSD":
            continue
        t_obj = {
            "market": mkt,
            "weekday_idx": int(r.get("weekday_idx", 0)),
            "result_r": float(r.get("numeric_r", 0.0)),
            "net_pnl": float(r.get("numeric_pnl", 0.0)),
            "cost_usd": float(r.get("cost_usd", 0.35))
        }
        all_trades.append(t_obj)
        if mkt == "XAUUSD":
            xau_trades.append(t_obj)
        elif mkt == "BTCUSD":
            btc_trades.append(t_obj)

    comb_stats    = compute_stage6_metrics(all_trades)
    xau_stats     = compute_stage6_metrics(xau_trades)
    btc_stats     = compute_stage6_metrics(btc_trades)
    day_breakdown = compute_day_wise_breakdown(all_trades)

    day_lines = ""
    for d_idx in range(5):
        ds = day_breakdown["days"][d_idx]
        icon = "🟢" if ds["net_pnl"] > 0 else ("🔴" if ds["net_pnl"] < 0 else "⚪")
        day_lines += f"{icon} <b>{ds['day_name']}:</b> {ds['total']} trades | WR: <b>{ds['wr']:.0f}%</b> | Net: <b><code>${ds['net_pnl']:+.2f}</code></b>\n"

    status_note = (
        "✨ <i>0-Start Active: Only live trades taken AFTER bot start appear here (0 old trades).</i>"
        if comb_stats["total"] == 0 else
        f"✅ <i>Live Forward-Test Record: {comb_stats['total']} completed trades since 0-Start.</i>"
    )

    msg = (
        f"📊 <b>0-START LIVE BACKTEST & DAY-WISE REPORT (TELEGRAM)</b>\n"
        f"{status_note}\n\n"
        f"💰 <b>Account Equity:</b> <code>$5,000.00</code> ➔ <b><code>${comb_stats['final_equity']:,.2f}</code></b> (Net: <b><code>${comb_stats['net_pnl_usd']:+.2f}</code></b>)\n"
        f"🏆 <b>Total Trades:</b> <b>{comb_stats['total']}</b> (Wins: <b>{comb_stats['wins']}</b> | BE: <b>{comb_stats['breakevens']}</b> | Losses: <b>{comb_stats['losses']}</b>)\n"
        f"📈 <b>Win Rate:</b> <b>{comb_stats['decisive_wr']:.1f}%</b> | <b>PF:</b> <b>{comb_stats['profit_factor']:.2f}</b> | <b>Max DD:</b> <b><code>${comb_stats['max_dd_usd']:.2f}</code></b>\n\n"
        f"🥇 <b>1. XAUUSD (Gold Only — 0-Start):</b>\n"
        f"• Trades: <b>{xau_stats['total']}</b> (W:{xau_stats['wins']} | BE:{xau_stats['breakevens']} | L:{xau_stats['losses']}) | WR: <b>{xau_stats['decisive_wr']:.1f}%</b> | Net: <b><code>${xau_stats['net_pnl_usd']:+.2f}</code></b>\n\n"
        f"₿ <b>2. BTCUSD (Bitcoin Only — 0-Start):</b>\n"
        f"• Trades: <b>{btc_stats['total']}</b> (W:{btc_stats['wins']} | BE:{btc_stats['breakevens']} | L:{btc_stats['losses']}) | WR: <b>{btc_stats['decisive_wr']:.1f}%</b> | Net: <b><code>${btc_stats['net_pnl_usd']:+.2f}</code></b>\n\n"
        f"📅 <b>3. DAY-WISE PROFITABILITY (MON – FRI):</b>\n{day_lines}"
    )

    chart_buf = generate_visual_backtest_report_chart(comb_stats, xau_stats, btc_stats, day_breakdown)
    send_telegram_signal_with_chart(msg, chart_buf)

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

        fig = plt.figure(figsize=(10.2, 5.6), dpi=100, facecolor="#ffffff")
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
            fontsize=30, fontweight="bold", color="#131722", alpha=0.07,
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

        bos_info, fvg_info, ob_info = get_clean_smc_overlays(sub, active_trade=active_trade)

        all_y = list(highs) + list(lows) + [live_spot]
        for lv in visible_levels:
            all_y.extend([lv["top"], lv["bot"]])
        if bos_info:
            all_y.append(bos_info["price"])
        if fvg_info:
            all_y.extend([fvg_info["top"], fvg_info["bot"]])
        if ob_info:
            all_y.extend([ob_info["top"], ob_info["bot"]])
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

        for lv in visible_levels:
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
                alpha=0.07, edgecolor="none", zorder=2
            ))
            ax.plot([s_idx, right_edge_idx], [z_mid, z_mid], color=lbl_c, linewidth=0.80, linestyle="--", alpha=0.75, zorder=3)

            ax.text(
                s_idx + 1, z_top + total_span * 0.005,
                f"{lv['short']} ({format_price(z_mid, dec)})",
                fontsize=7.2, fontweight="bold", color=lbl_c, ha="left", va="bottom",
                bbox=dict(boxstyle="round,pad=0.14", facecolor="#ffffff", edgecolor=lbl_c, alpha=0.88, linewidth=0.6),
                clip_on=True, zorder=6
            )

        if bos_info is not None:
            bx0 = bos_info["x_start"]
            by  = bos_info["price"]
            ax.plot([bx0, n_bars], [by, by], color="#7c3aed", linestyle="-.", linewidth=1.25, zorder=6)
            ax.text(
                n_bars - 1, by + total_span * 0.005,
                f" BOS {format_price(by, dec)} ",
                fontsize=7.0, fontweight="bold", color="#ffffff", ha="right", va="bottom",
                bbox=dict(boxstyle="round,pad=0.14", facecolor="#7c3aed", edgecolor="none"),
                clip_on=True, zorder=8
            )

        if fvg_info is not None:
            fx0 = fvg_info["x_start"]
            f_h = max(fvg_info["top"] - fvg_info["bot"], min_body_h * 1.4)
            ax.add_patch(mpatches.Rectangle(
                (fx0, fvg_info["bot"]), max(4, n_bars - fx0), f_h,
                facecolor="#f59e0b", alpha=0.22, edgecolor="#d97706", linestyle="--", linewidth=0.9, zorder=3
            ))
            ax.text(
                fx0 + 0.5, fvg_info["top"] + total_span * 0.004,
                " FVG ",
                fontsize=7.0, fontweight="bold", color="#78350f", ha="left", va="bottom",
                bbox=dict(boxstyle="round,pad=0.14", facecolor="#fef3c7", edgecolor="#d97706", linewidth=0.7),
                clip_on=True, zorder=7
            )

        if ob_info is not None:
            ox0 = ob_info["x_start"]
            o_h = max(ob_info["top"] - ob_info["bot"], min_body_h * 1.6)
            ax.add_patch(mpatches.Rectangle(
                (ox0, ob_info["bot"]), max(4, n_bars - ox0), o_h,
                facecolor="#0284c7", alpha=0.20, edgecolor="#0369a1", linestyle="-", linewidth=0.9, zorder=3
            ))
            ax.text(
                ox0 + 0.5, ob_info["bot"] - total_span * 0.004,
                " OB ",
                fontsize=7.0, fontweight="bold", color="#ffffff", ha="left", va="top",
                bbox=dict(boxstyle="round,pad=0.14", facecolor="#0284c7", edgecolor="none"),
                clip_on=True, zorder=7
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
            f"{hdr_nam} ({mt5_sym}) · BOS / FVG / OB + Session Liquidity (5m)",
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

        fig.text(0.015, 0.018, "Telegram Bot · London + NY · Low-Spread · BOS / FVG / OB", fontsize=9.0, fontweight="bold", color="#131722")

        buf = io.BytesIO()
        plt.savefig(buf, format="png", dpi=100, facecolor="#ffffff")
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

        if len(caption_html) <= 1020:
            for attempt in range(2):
                try:
                    files = {"photo": ("chart.png", photo_bytes, "image/png")}
                    data  = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                    res   = TELEGRAM_SESSION.post(url, data=data, files=files, timeout=7) if attempt == 0 else requests.post(url, data=data, files=files, timeout=9)
                    if res.status_code == 200 and res.json().get("ok"):
                        return
                except Exception as e:
                    print(f"Photo Attempt {attempt + 1} Warning: {e}")

        for attempt in range(2):
            try:
                files = {"photo": ("chart.png", photo_bytes, "image/png")}
                data  = {"chat_id": target_chat_id}
                res   = TELEGRAM_SESSION.post(url, data=data, files=files, timeout=7) if attempt == 0 else requests.post(url, data=data, files=files, timeout=9)
                if res.status_code == 200 and res.json().get("ok"):
                    send_telegram_message(caption_html, target_chat_id)
                    return
            except Exception as e:
                print(f"Fallback Photo Attempt {attempt + 1} Warning: {e}")

    send_telegram_message(caption_html, target_chat_id)

def send_instant_preview_chart(pair_key=None):
    global chart_rotation_index
    send_telegram_chat_action("upload_photo")

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
    _, _, sess_status_txt, _ = get_active_sessions_info()
    _, spread_status_txt     = check_low_spread_window(info)

    if cand is not None:
        stage_lbl = "🚨 ENTER NOW (CONFIRMED)" if cand.get("stage") == "CONFIRMED_BREAKOUT" else "⏳ PRE-ALERT LOCKED"
        caption = (
            f"📊 <b>{info['mt5_name']} • 1H ➔ 15M ➔ 5M SETUP</b>\n"
            f"🔔 <b>Status:</b> <b>{stage_lbl}</b> | 💧 <b>Spread:</b> {spread_status_txt}\n\n"
            f"🧭 <b>1H Context:</b> <b>{roadmap['h1_bias']}</b> | 📢 <b>Action:</b> <b>{cand['action']}</b>\n"
            f"🧲 <b>Setup / Level:</b> {cand['swept_level_name']}\n"
            f"📍 <b>Live Price:</b> <code>{format_price(live_spot, dec)}</code>\n"
            f"🎯 <b>ENTRY SPOT:</b> <b><code>{format_price(cand['entry_spot'], dec)}</code></b>\n"
            f"💼 <b>Lot Size ($12.50 Risk):</b> <code>{cand['lot_size']:.2f} Lot</code> (Risk: ${cand['planned_risk_usd']:.2f})\n"
            f"🛑 <b>Invalidation SL:</b> <code>{format_price(cand['sl_price'], dec)}</code>\n"
            f"🛡️ <b>1:1 Lock (TP1):</b> <code>{format_price(cand['tp1_price'], dec)}</code> | 🎯 <b>1:2 Target (TP2):</b> <code>{format_price(cand['tp2_price'], dec)}</code>"
        )
    else:
        no_trade_note = "⚠️ <b>1H is RANGING/CHOPPY — Stand Aside!</b>\n" if roadmap["h1_bias"] == "RANGING" else ""
        caption = (
            f"📊 <b>{info['mt5_name']} • LIQUIDITY & BOS/FVG/OB MAP</b>\n"
            f"🕒 <b>Session:</b> {sess_status_txt}\n"
            f"💧 <b>Spread Window:</b> {spread_status_txt}\n"
            f"🧭 <b>1H Context:</b> <b>{roadmap['h1_bias']}</b> | 📍 <b>Live:</b> <code>{format_price(live_spot, dec)}</code>\n"
            f"{no_trade_note}\n"
            f"📌 <b>Pre-Marked Roadmap Levels:</b>\n"
            f"• <b>PDH / PDL:</b> <code>{format_price(roadmap['pdh'], dec)}</code> / <code>{format_price(roadmap['pdl'], dec)}</code>\n"
            f"• <b>London H / L:</b> <code>{format_price(roadmap['london_high'], dec)}</code> / <code>{format_price(roadmap['london_low'], dec)}</code>\n"
            f"• <b>Asian H / L:</b> <code>{format_price(roadmap['asian_high'], dec)}</code> / <code>{format_price(roadmap['asian_low'], dec)}</code>\n\n"
            f"⏳ <b>Rules:</b> Active Session + Low Spread + (15M Sweep + 5M MSS OR 1H Trend + 5M BOS ➔ FVG/OB Pullback)."
        )

    send_telegram_signal_with_chart(caption, chart_buf)

def send_live_status_summary():
    bal, eq, day_pnl, week_pnl = get_live_account_metrics()
    _, _, sess_status_txt, _     = get_active_sessions_info()
    can_tr, _, guard_reason    = check_funded_account_risk_rules()
    dd_from_peak               = max(STARTING_CAPITAL_USD, HIGH_WATER_MARK_USD) - min(bal, eq)
    today_name                 = WEEKDAY_NAMES[get_ny_weekday_index(get_utc_now())]

    report = (
        f"📊 <b>$5,000 TWO-STEP FUNDED STATUS (TELEGRAM 0-START)</b>\n\n"
        f"📅 <b>Day:</b> <b>{today_name}</b> | 🕒 <b>Session:</b> {sess_status_txt}\n"
        f"🚦 <b>Risk Guard:</b> <b>{'🟢 READY TO TRADE' if can_tr else '🔴 LOCKED (' + guard_reason + ')'}</b>\n"
        f"💰 <b>Balance / Equity:</b> <b>${bal:.2f} / ${eq:.2f}</b> (Peak: ${HIGH_WATER_MARK_USD:.2f})\n"
        f"📈 <b>Today's PnL:</b> <b>${day_pnl:+.2f}</b> (Limit: -${DAILY_STOP_LOSS_USD:.2f})\n"
        f"📅 <b>Weekly PnL:</b> <b>${week_pnl:+.2f}</b> (Limit: -${WEEKLY_STOP_LOSS_USD:.2f})\n"
        f"🛡️ <b>Drawdown from Peak:</b> <b>${dd_from_peak:.2f}</b> (Review Stop: ${MAX_DRAWDOWN_REVIEW_USD:.2f})\n"
        f"🔢 <b>Trades Today:</b> {trades_today}/{MAX_DAILY_TRADES} | <b>Losses Today:</b> {losses_today}/{MAX_DAILY_LOSS_COUNT}\n"
        f"📒 <b>Total Live Trades Since 0-Start:</b> {len(journal_memory_log)}\n\n"
    )
    for name, info in FOREX_PAIRS.items():
        _, sp_txt = check_low_spread_window(info)
        try:
            df_1h, df_15m, df_5m, live_spot = get_roadmap_3tf_spot_data(name, info, use_fast_cache=True)
            if df_1h is not None and df_15m is not None and live_spot is not None:
                df_1h  = enrich_price_action_metrics(df_1h)
                df_15m = enrich_price_action_metrics(df_15m)
                rm     = extract_roadmap_session_levels_and_1h_bias(df_1h, df_15m, live_spot, info["decimals"])
                pend   = pending_pre_alerts.get(name)
                st_str = f"⏳ Pre-Alert @ {format_price(pend['entry_spot'], info['decimals'])}" if pend else f"PDH:{format_price(rm['pdh'], info['decimals'])} | PDL:{format_price(rm['pdl'], info['decimals'])}"
                report += f"🔹 <b>{info['mt5_name']}:</b> <code>{format_price(live_spot, info['decimals'])}</code> | {sp_txt} | 1H: <b>{rm['h1_bias']}</b> ({st_str})\n"
            else:
                report += f"🔹 <b>{info['mt5_name']}:</b> {sp_txt}\n"
        except Exception:
            report += f"🔹 <b>{info['mt5_name']}:</b> {sp_txt}\n"
    report += "\n⚡ Commands: <code>/gold</code>, <code>/btc</code>, <code>/backtest</code>, <code>/reset</code>"
    send_telegram_message(report)

def flush_old_telegram_updates():
    global last_update_id
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return
    try:
        res = TELEGRAM_SESSION.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates", params={"timeout": 0}, timeout=2.5).json()
        if res.get("ok") and res.get("result"):
            last_update_id = res["result"][-1]["update_id"]
    except Exception:
        pass

def check_telegram_commands():
    global last_update_id
    if not BOT_TOKEN or BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
    params = {"offset": last_update_id + 1, "timeout": 0}
    try:
        response = TELEGRAM_SESSION.get(url, params=params, timeout=2.0).json()
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
                elif msg_text in ["/reset", "reset"]:
                    reset_live_account_to_zero()
                    send_telegram_message("✅ <b>Telegram Account & /backtest Reset to 0 ($5,000.00 Starting Balance).</b>")
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
# 10. TRADE EXECUTION, LOCKED PRE-ALERTS & TRADE MANAGEMENT
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
    _, sp_txt  = check_low_spread_window(info)

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
        "entry_candle_time": closed_time, "trailed_to_cost": False, "is_open": True,
        "max_r_seen": 0.0, "min_r_seen": 0.0
    })

    msg = (
        f"🚨 <b>ENTER NOW — {info['mt5_name']} (TRADE #{trades_today}/{MAX_DAILY_TRADES} · {day_name})</b> 🚨\n\n"
        f"📢 <b>Action:</b> <b>{signal}</b> | 💧 <b>Spread:</b> {sp_txt}\n"
        f"🎯 <b>ENTRY SPOT:</b> <b><code>{format_price(entry_spot, dec)}</code></b> (Live: <code>{format_price(live_spot, dec)}</code>)\n"
        f"💼 <b>Lot Size (0.25% Risk):</b> <b><code>{lot_size:.2f} Lot</code></b>\n\n"
        f"🛑 <b>Invalidation Stop-Loss:</b> <code>{format_price(sl_level, dec)}</code>\n"
        f"🛡️ <b>1:1 Breakeven Lock (TP1):</b> <code>{format_price(tp1_level, dec)}</code>\n"
        f"🎯 <b>1:2 Full Target (TP2):</b> <code>{format_price(tp2_level, dec)}</code>\n\n"
        f"🔍 <b>Setup Checklist:</b>\n{cand['why_text']}\n\n"
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
    _, sp_txt  = check_low_spread_window(info)

    cand["created_utc"]         = get_utc_now()
    pending_pre_alerts[name]    = cand
    last_pre_alert_candle[name] = closed_time
    last_setup_traded[name]     = cand["setup_id"]

    msg = (
        f"⏳ <b>PRE-ALERT LOCKED — {info['mt5_name']}</b> ⏳\n\n"
        f"📢 <b>Upcoming Action:</b> <b>{cand['action']}</b> | 💧 <b>Spread:</b> {sp_txt}\n"
        f"🧲 <b>Setup / Level:</b> <b>{cand['swept_level_name']}</b>\n"
        f"📍 <b>Current Live Price:</b> <code>{format_price(live_spot, dec)}</code>\n"
        f"🎯 <b>LOCKED ENTRY SPOT:</b> <b><code>{format_price(entry_spot, dec)}</code></b>\n\n"
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

        spread_ok, _ = check_low_spread_window(info)
        if not spread_ok:
            continue

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
                f"Price broke below invalidation SL (<code>{format_price(cand['sl_price'], dec)}</code>). No trade taken."
            )
            continue
        elif cand["direction"] == "SELL" and live_spot >= cand["sl_price"]:
            pending_pre_alerts[name] = None
            pre_alert_cooldown[name] = now_utc + timedelta(minutes=20)
            send_telegram_message(
                f"❌ <b>PRE-ALERT INVALIDATED — {info['mt5_name']}</b>\n"
                f"Price broke above invalidation SL (<code>{format_price(cand['sl_price'], dec)}</code>). No trade taken."
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

            if trade.get("sl_dist", 0.0) > 0:
                hi_r = ((current_high - trade["entry"]) / trade["sl_dist"]) if trade["direction"] == "BUY" else ((trade["entry"] - current_low) / trade["sl_dist"])
                lo_r = ((current_low - trade["entry"]) / trade["sl_dist"]) if trade["direction"] == "BUY" else ((trade["entry"] - current_high) / trade["sl_dist"])
                trade["max_r_seen"] = round(max(float(trade.get("max_r_seen", 0.0)), hi_r), 2)
                trade["min_r_seen"] = round(min(float(trade.get("min_r_seen", 0.0)), lo_r), 2)

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
                    f"📒 Saved to 0-Start Telegram <code>/backtest</code>!"
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
                    f"📒 Capital preserved & logged in Telegram <code>/backtest</code>."
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
                    f"Losses Today: {losses_today}/{MAX_DAILY_LOSS_COUNT}. Logged in Telegram <code>/backtest</code>."
                )

# =====================================================================
# 11. MASTER SCANNER LOOP (TELEGRAM ONLY)
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
        for k in last_radar_key:
            last_radar_key[k] = None

    if iso_week != current_week:
        current_week = iso_week
        weekly_realized_pnl = 0.0
        weekly_halt_announced = False

    _, is_pre_session, _, sessions_str = get_active_sessions_info()
    if is_pre_session and pre_session_brief_sent != today:
        pre_session_brief_sent = today
        today_name = WEEKDAY_NAMES[get_ny_weekday_index(get_utc_now())]
        send_telegram_message(
            f"🔔 <b>SESSION PRE-BRIEF ({today_name})</b> 🔔\n\n"
            f"🕒 <b>Active Windows:</b> {sessions_str}\n"
            f"💧 <b>Spread Filter:</b> Active (Low-spread hours only)\n"
            f"📋 <b>Risk:</b> <b>$12.50 (0.25%)</b> | Max <b>2 trades</b>/day\n"
            f"• Sending charts for <b>XAUUSD</b> and <b>BTCUSD</b>..."
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
            df_1h, df_15m, df_5m, live_spot = get_roadmap_3tf_spot_data(name, info, use_fast_cache=True)
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
    flush_old_telegram_updates()
    threading.Thread(target=fetch_live_economic_calendar, daemon=True).start()

    send_telegram_message(
        f"⚡ <b>$5,000 Two-Step Funded Bot Active (Telegram-Only · 0-Start)</b>\n"
        f"• <b>Sessions:</b> London + New York Active\n"
        f"• <b>Spread Filter:</b> Active (Low-Spread Hours Only)\n"
        f"• <b>Setups:</b> 15M Sweep + 5M MSS & HTF Pullback to OB/FVG After 5M BOS\n"
        f"• <b>Chart Tags:</b> Clean <code>BOS</code>, <code>FVG</code> & <code>OB</code> marked live\n"
        f"• <b>Commands:</b> <code>/gold</code>, <code>/btc</code>, <code>/backtest</code>, <code>/status</code>, <code>/reset</code>"
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
            time.sleep(0.3)
        except KeyboardInterrupt:
            print("Stopped manually.")
            break
        except Exception as e:
            print(f"Loop Error: {e}")
            time.sleep(2)
