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
# 1. $100,000 INSTITUTIONAL TRADING ENGINE (FRESH & CLEAN EDITION)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "5610520408"

TOTAL_CAPITAL_USD     = 100000.0
MAX_DAILY_DRAWDOWN    = 5000.0
MAX_TOTAL_DRAWDOWN    = 10000.0

RISK_PER_TRADE_USD    = 400.0     # 0.40% Standard Risk ($400)
DAILY_PROTECTION_CAP  = 1600.0
MAX_CONCURRENT_TRADES = 2
MAX_HOLD_MINUTES      = 180

MACRO_SYMBOLS = {
    "DXY": "DX-Y.NYB",
    "US10Y": "^TNX",
    "SP500": "ES=F",
    "VIX": "^VIX"
}

FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "symbol": "GC=F",
        "mt5_name": "XAUUSD",
        "decimals": 2,
        "unit_name": "Points",
        "macro_mode": "GOLD_MACRO",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_price_dist": 6.50,
        "max_lot_cap": 0.65,
        "is_crypto": False
    },
    "EUR/USD": {
        "symbol": "EURUSD=X",
        "mt5_name": "EURUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "macro_mode": "USD_INVERSE",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_price_dist": 0.0020,
        "max_lot_cap": 1.20,
        "is_crypto": False
    },
    "GBP/USD": {
        "symbol": "GBPUSD=X",
        "mt5_name": "GBPUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "macro_mode": "USD_INVERSE",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_price_dist": 0.0024,
        "max_lot_cap": 1.20,
        "is_crypto": False
    },
    "USD/JPY": {
        "symbol": "JPY=X",
        "mt5_name": "USDJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "macro_mode": "JPY_MACRO",
        "usd_exposure": "LONG_USD_ON_BUY",
        "min_sl_price_dist": 0.240,
        "max_lot_cap": 1.20,
        "is_crypto": False
    },
    "GBP/JPY": {
        "symbol": "GBPJPY=X",
        "mt5_name": "GBPJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "macro_mode": "SYNTHETIC_GBPJPY",
        "usd_exposure": "NEUTRAL",
        "min_sl_price_dist": 0.300,
        "max_lot_cap": 1.00,
        "is_crypto": False
    },
    "BTC/USD (BITCOIN)": {
        "symbol": "BTC-USD",
        "mt5_name": "BTCUSD",
        "decimals": 2,
        "unit_name": "Points",
        "macro_mode": "CRYPTO_MACRO",
        "usd_exposure": "NEUTRAL",
        "min_sl_price_dist": 380.0,
        "max_lot_cap": 0.80,
        "is_crypto": True
    }
}

last_candle_traded  = {name: None for name in FOREX_PAIRS}
pair_cooldown_until = {name: datetime.min.replace(tzinfo=timezone.utc) for name in FOREX_PAIRS}
sl_direction_lock   = {name: {"BUY": datetime.min.replace(tzinfo=timezone.utc), "SELL": datetime.min.replace(tzinfo=timezone.utc)} for name in FOREX_PAIRS}

trades_today = 0
last_update_id = 0
daily_trades_log = []
daily_realized_pnl = 0.0
total_account_pnl = 0.0

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

def get_institutional_session(is_crypto=False):
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)

    if 3.0 <= hr <= 6.0:
        if is_crypto:
            return True, "Crypto 24/7 Session"
        return False, "Rollover Pause"

    if (17.91 <= hr <= 18.20) or (18.91 <= hr <= 19.17):
        return False, "Macro News Release Window"

    if 6.0 < hr < 12.25:
        return True, "Asian Institutional Session"
    elif 12.25 <= hr < 18.0:
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

def calculate_exact_mt5_lot_size(pair_name, pair_info, entry_price, sl_dist, risk_usd, live_usdjpy_rate=152.0):
    if sl_dist <= 0:
        return 0.05

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
        return 0.05

    raw_lot = risk_usd / loss_per_1_lot
    max_cap = pair_info.get("max_lot_cap", 1.00)
    return max(0.05, min(round(raw_lot, 2), max_cap))

def generate_trade_position_chart(df_15m, pair_name, direction, entry_p, sl_p, tp_p, half_p, decimals, lot_size, risk_usd, reward_usd, rr_ratio):
    if not CHART_AVAILABLE:
        return None
    try:
        sub = df_15m.iloc[-42:-1].copy()
        if sub.empty or len(sub) < 15:
            return None

        fig, ax = plt.subplots(figsize=(11, 6.2), dpi=130)
        fig.patch.set_facecolor('#f3f4f6')
        ax.set_facecolor('#f3f4f6')

        n_bars = len(sub)
        for idx in range(n_bars):
            row = sub.iloc[idx]
            o, h, l, c = float(row['Open']), float(row['High']), float(row['Low']), float(row['Close'])
            color = '#16a34a' if c >= o else '#dc2626'
            ax.plot([idx, idx], [l, h], color=color, linewidth=1.1, zorder=2)
            body_low = min(o, c)
            body_h = max(abs(c - o), (h - l) * 0.04, 1e-6)
            ax.add_patch(mpatches.Rectangle((idx - 0.32, body_low), 0.64, body_h, facecolor=color, edgecolor=color, linewidth=0.8, zorder=3))

        box_start_x = n_bars - 1
        box_width = 16
        box_center_x = box_start_x + (box_width / 2.0)

        ax.add_patch(mpatches.Rectangle((box_start_x, min(entry_p, tp_p)), box_width, abs(tp_p - entry_p), facecolor='#22c55e', alpha=0.28, edgecolor='#15803d', linewidth=1.2, zorder=4))
        ax.add_patch(mpatches.Rectangle((box_start_x, min(entry_p, sl_p)), box_width, abs(entry_p - sl_p), facecolor='#ef4444', alpha=0.28, edgecolor='#b91c1c', linewidth=1.2, zorder=4))

        ax.plot([box_start_x, box_start_x + box_width], [entry_p, entry_p], color='#374151', linewidth=1.6, zorder=5)
        ax.plot([box_start_x, box_start_x + box_width], [half_p, half_p], color='#0284c7', linestyle='--', linewidth=1.1, alpha=0.85, zorder=5)

        entry_s, sl_s, tp_s, half_s = format_price(entry_p, decimals), format_price(sl_p, decimals), format_price(tp_p, decimals), format_price(half_p, decimals)
        tp_pct = (abs(tp_p - entry_p) / max(entry_p, 1e-6)) * 100.0
        sl_pct = (abs(entry_p - sl_p) / max(entry_p, 1e-6)) * 100.0

        ax.text(box_center_x, tp_p, f" Target: {tp_s} ({tp_pct:.2f}%), Profit: +${reward_usd:.0f} ", color='white', fontweight='bold', fontsize=8.5, ha='center', va='bottom' if direction == "BUY" else 'top', bbox=dict(boxstyle='round,pad=0.35', facecolor='#16a34a', edgecolor='none'), zorder=7)
        ax.text(box_center_x, sl_p, f" Stop: {sl_s} ({sl_pct:.2f}%), Risk: -${risk_usd:.0f} ", color='white', fontweight='bold', fontsize=8.5, ha='center', va='top' if direction == "BUY" else 'bottom', bbox=dict(boxstyle='round,pad=0.35', facecolor='#dc2626', edgecolor='none'), zorder=7)

        pos_title = "LONG POSITION" if direction == "BUY" else "SHORT POSITION"
        ax.text(box_center_x, entry_p, f" {pos_title} @ {entry_s} | Lot: {lot_size:.2f}\n Risk/Reward: 1:{rr_ratio} | 1:1 Lock: {half_s} ", color='white', fontweight='bold', fontsize=8.2, ha='center', va='center', bbox=dict(boxstyle='round,pad=0.38', facecolor='#dc2626' if direction == "SELL" else '#1f2937', edgecolor='white', alpha=0.92), zorder=8)

        ax.yaxis.tick_right()
        right_edge_x = box_start_x + box_width + 4.5
        ax.set_xlim(-1, right_edge_x)
        all_y = list(sub['High'].values) + list(sub['Low'].values) + [tp_p, sl_p, entry_p]
        y_min, y_max = min(all_y), max(all_y)
        y_pad = (y_max - y_min) * 0.14
        ax.set_ylim(y_min - y_pad, y_max + y_pad)

        for tag_price, tag_bg in [(tp_p, '#16a34a'), (half_p, '#0284c7'), (entry_p, '#374151'), (sl_p, '#dc2626')]:
            ax.text(right_edge_x + 0.2, tag_price, f" {format_price(tag_price, decimals)} ", color='white', fontweight='bold', fontsize=8, va='center', ha='left', bbox=dict(boxstyle='square,pad=0.25', facecolor=tag_bg, edgecolor='none'), clip_on=False, zorder=9)

        ax.set_title(f"{pair_name} — 15M Live Setup ({pos_title} | 1:{rr_ratio} R:R)", loc='left', fontsize=11, fontweight='bold', color='#111827', pad=10)
        ax.grid(True, linestyle='--', alpha=0.32, color='#9ca3af')

        buf = io.BytesIO()
        plt.tight_layout()
        plt.savefig(buf, format='png', bbox_inches='tight', facecolor=fig.get_facecolor())
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
                files = {"photo": ("trade_setup.png", chart_buf, "image/png")}
                data = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                res = requests.post(url, data=data, files=files, timeout=15)
                if res.status_code == 200:
                    return
            else:
                files = {"photo": ("trade_setup.png", chart_buf, "image/png")}
                data = {"chat_id": target_chat_id, "caption": "📊 <b>Live Position Chart</b>", "parse_mode": "HTML"}
                requests.post(url, data=data, files=files, timeout=15)
                send_telegram_message(caption_html, target_chat_id)
                return
        except Exception as e:
            print(f"Photo Send Error: {e}")
    send_telegram_message(caption_html, target_chat_id)

# =====================================================================
# 2. QUANTITATIVE & GLOBAL MACRO-REGIME ENGINE
# =====================================================================
def calculate_proprietary_metrics(df):
    df = df.copy()
    df['EMA9']   = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA20']  = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50']  = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

    hl = df['High'] - df['Low']
    hc = np.abs(df['High'] - df['Close'].shift())
    lc = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    direction_move = np.abs(df['Close'] - df['Close'].shift(10))
    volatility_sum = np.abs(df['Close'] - df['Close'].shift(1)).rolling(10).sum().replace(0, 1e-9)
    df['ER'] = (direction_move / volatility_sum).fillna(0.0)

    df['Body']       = np.abs(df['Close'] - df['Open'])
    df['Range']      = (df['High'] - df['Low']).replace(0, 1e-9)
    df['Body_Ratio'] = df['Body'] / df['Range']
    df['Upper_Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Close_Pos']  = (df['Close'] - df['Low']) / df['Range']

    avg_body = df['Body'].rolling(20).mean().replace(0, 1e-9)
    df['Disp_Power'] = df['Body'] / avg_body

    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50.0)

    df['Deal_High']   = df['High'].rolling(32).max()
    df['Deal_Low']    = df['Low'].rolling(32).min()
    df['Equilibrium'] = (df['Deal_High'] + df['Deal_Low']) / 2.0

    return df

def analyze_leg_vector(symbol_str):
    try:
        tkr = yf.Ticker(symbol_str)
        df = tkr.history(period="5d", interval="15m")
        if df.empty or len(df) < 35:
            return {"state": "NEUTRAL", "shock": False, "price": 0.0}

        df = calculate_proprietary_metrics(df)
        c = df.iloc[-2]
        close_p = float(c['Close'])
        ema20   = float(c['EMA20'])
        ema50   = float(c['EMA50'])
        atr     = float(c['ATR']) if not np.isnan(c['ATR']) else 0.0
        rng     = float(c['Range'])
        slope_3 = close_p - float(df.iloc[-5]['Close'])

        is_shock = (atr > 0) and (rng > atr * 2.3)

        if close_p > ema20 and ema20 >= ema50 and slope_3 > 0:
            state = "BULLISH"
        elif close_p < ema20 and ema20 <= ema50 and slope_3 < 0:
            state = "BEARISH"
        else:
            state = "NEUTRAL"

        return {"state": state, "shock": is_shock, "price": close_p}
    except Exception:
        return {"state": "NEUTRAL", "shock": False, "price": 0.0}

def build_global_macro_radar(forex_open):
    radar = {
        "DXY": {"state": "NEUTRAL", "shock": False, "price": 0.0},
        "US10Y": {"state": "NEUTRAL", "shock": False, "price": 0.0},
        "SP500": {"state": "NEUTRAL", "shock": False, "price": 0.0},
        "GBPUSD": {"state": "NEUTRAL", "shock": False, "price": 0.0},
        "USDJPY": {"state": "NEUTRAL", "shock": False, "price": 152.0},
        "GLOBAL_SHOCK": False
    }
    if not forex_open:
        radar["SP500"] = analyze_leg_vector(MACRO_SYMBOLS["SP500"])
        return radar

    radar["DXY"]    = analyze_leg_vector(MACRO_SYMBOLS["DXY"])
    radar["US10Y"]  = analyze_leg_vector(MACRO_SYMBOLS["US10Y"])
    radar["SP500"]  = analyze_leg_vector(MACRO_SYMBOLS["SP500"])
    radar["GBPUSD"] = analyze_leg_vector("GBPUSD=X")
    radar["USDJPY"] = analyze_leg_vector("JPY=X")

    if radar["DXY"]["shock"] or radar["US10Y"]["shock"]:
        radar["GLOBAL_SHOCK"] = True

    return radar

def verify_gate1_macro_and_portfolio(pair_name, pair_info, trade_dir, macro_radar):
    if macro_radar.get("GLOBAL_SHOCK", False):
        return False, "Macro Volatility Pause"

    exp_type = pair_info.get("usd_exposure", "NEUTRAL")
    new_usd_bet = "NONE"
    if exp_type == "SHORT_USD_ON_BUY":
        new_usd_bet = "SHORT_USD" if trade_dir == "BUY" else "LONG_USD"
    elif exp_type == "LONG_USD_ON_BUY":
        new_usd_bet = "LONG_USD" if trade_dir == "BUY" else "SHORT_USD"

    if new_usd_bet != "NONE":
        for t in daily_trades_log:
            if t["is_open"]:
                t_info = FOREX_PAIRS.get(t["pair"], {})
                t_exp  = t_info.get("usd_exposure", "NEUTRAL")
                existing_usd_bet = "NONE"
                if t_exp == "SHORT_USD_ON_BUY":
                    existing_usd_bet = "SHORT_USD" if t["direction"] == "BUY" else "LONG_USD"
                elif t_exp == "LONG_USD_ON_BUY":
                    existing_usd_bet = "LONG_USD" if t["direction"] == "BUY" else "SHORT_USD"

                if existing_usd_bet != "NONE" and existing_usd_bet != new_usd_bet:
                    return False, "USD Exposure Filter"

    mode  = pair_info.get("macro_mode", "USD_INVERSE")
    dxy   = macro_radar["DXY"]["state"]
    us10y = macro_radar["US10Y"]["state"]
    sp500 = macro_radar["SP500"]["state"]
    gbp   = macro_radar["GBPUSD"]["state"]
    jpy   = macro_radar["USDJPY"]["state"]

    if mode == "GOLD_MACRO":
        if trade_dir == "BUY":
            if dxy == "BULLISH" or us10y == "BULLISH":
                return False, "Macro Filter"
            return True, f"DXY: {dxy} | US10Y: {us10y}"
        else:
            if dxy == "BEARISH" or us10y == "BEARISH":
                return False, "Macro Filter"
            return True, f"DXY: {dxy} | US10Y: {us10y}"

    elif mode == "USD_INVERSE":
        if trade_dir == "BUY" and dxy == "BULLISH":
            return False, "Macro Filter"
        if trade_dir == "SELL" and dxy == "BEARISH":
            return False, "Macro Filter"
        return True, f"DXY: {dxy}"

    elif mode == "JPY_MACRO":
        if trade_dir == "BUY" and (dxy == "BEARISH" or us10y == "BEARISH"):
            return False, "Macro Filter"
        if trade_dir == "SELL" and (dxy == "BULLISH" or us10y == "BULLISH"):
            return False, "Macro Filter"
        return True, f"DXY: {dxy} | US10Y: {us10y}"

    elif mode == "SYNTHETIC_GBPJPY":
        if trade_dir == "BUY":
            if gbp == "BEARISH" or jpy == "BEARISH":
                return False, "Cross-Leg Filter"
            if gbp == "BULLISH" or jpy == "BULLISH":
                return True, f"GBPUSD: {gbp} | USDJPY: {jpy}"
            return False, "Neutral Legs"
        else:
            if gbp == "BULLISH" or jpy == "BULLISH":
                return False, "Cross-Leg Filter"
            if gbp == "BEARISH" or jpy == "BEARISH":
                return True, f"GBPUSD: {gbp} | USDJPY: {jpy}"
            return False, "Neutral Legs"

    elif mode == "CRYPTO_MACRO":
        if trade_dir == "BUY" and sp500 == "BEARISH":
            return False, "Risk-Off Filter"
        return True, f"S&P500: {sp500}"

    return True, "Aligned"

# =====================================================================
# 3. 7-GATE INSTITUTIONAL STRATEGY ENGINE
# =====================================================================
def analyze_4h_and_1h_regime(df_1h):
    c1 = df_1h.iloc[-2]
    c2 = df_1h.iloc[-3]

    close_1h  = float(c1['Close'])
    ema20_1h  = float(c1['EMA20'])
    ema50_1h  = float(c1['EMA50'])
    ema200_1h = float(c1['EMA200'])
    atr_1h    = float(c1['ATR']) if not np.isnan(c1['ATR']) else 0.0

    day_window = df_1h.iloc[-26:-2]
    day_high   = float(day_window['High'].max())
    day_low    = float(day_window['Low'].min())
    day_mid    = (day_high + day_low) / 2.0

    active_1h_rising  = (close_1h > float(c2['Close'])) and (float(c1['Low']) >= float(c2['Low']))
    active_1h_falling = (close_1h < float(c2['Close'])) and (float(c1['High']) <= float(c2['High']))

    if close_1h > ema20_1h and ema20_1h > ema50_1h and close_1h > ema200_1h and close_1h >= day_mid and not active_1h_falling:
        regime = "BULLISH"
    elif close_1h < ema20_1h and ema20_1h < ema50_1h and close_1h < ema200_1h and close_1h <= day_mid and not active_1h_rising:
        regime = "BEARISH"
    else:
        regime = "NEUTRAL"

    recent_16 = df_1h.iloc[-18:-2]
    major_res = float(recent_16['High'].max())
    major_sup = float(recent_16['Low'].min())

    return {
        "regime": regime,
        "major_res": major_res,
        "major_sup": major_sup,
        "day_high": day_high,
        "day_low": day_low,
        "atr_1h": atr_1h
    }

def verify_5m_micro_bos(df_5m, direction):
    if df_5m is None or len(df_5m) < 30:
        return False

    df_5m_m = calculate_proprietary_metrics(df_5m)
    m0 = df_5m_m.iloc[-2]
    m1 = df_5m_m.iloc[-3]
    m2 = df_5m_m.iloc[-4]

    c0, c1, c2 = float(m0['Close']), float(m1['Close']), float(m2['Close'])
    l0, l1, l2 = float(m0['Low']), float(m1['Low']), float(m2['Low'])
    h0, h1, h2 = float(m0['High']), float(m1['High']), float(m2['High'])
    ema20_5m   = float(m0['EMA20'])
    ema50_5m   = float(m0['EMA50'])
    rsi_5m     = float(m0['RSI'])

    if direction == "BUY":
        return (c0 > ema20_5m >= ema50_5m) and (c0 > float(m0['Open'])) and (l0 >= l1 or l1 >= l2) and (c0 >= max(c1, c2)) and (48 <= rsi_5m <= 68)
    else:
        return (c0 < ema20_5m <= ema50_5m) and (c0 < float(m0['Open'])) and (h0 <= h1 or h1 <= h2) and (c0 <= min(c1, c2)) and (32 <= rsi_5m <= 52)

def evaluate_sovereign_atoz_strategy(pair_name, pair_info, df_1h, df_15m, df_5m, macro_radar):
    now_utc = get_utc_now()

    if now_utc < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
        return None, None, None, None, ""

    session_ok, _ = get_institutional_session(pair_info.get("is_crypto", False))
    if not session_ok:
        return None, None, None, None, ""

    htf = analyze_4h_and_1h_regime(df_1h)
    regime = htf["regime"]
    if regime == "NEUTRAL":
        return None, None, None, None, ""

    c0 = df_15m.iloc[-2]
    c1 = df_15m.iloc[-3]
    c2 = df_15m.iloc[-4]

    price      = float(c0['Close'])
    open_0     = float(c0['Open'])
    high_0     = float(c0['High'])
    low_0      = float(c0['Low'])
    atr        = float(c0['ATR'])
    er         = float(c0['ER'])
    disp_power = float(c0['Disp_Power'])
    close_pos  = float(c0['Close_Pos'])
    u_wick     = float(c0['Upper_Wick'])
    l_wick     = float(c0['Lower_Wick'])
    ema20      = float(c0['EMA20'])
    ema50      = float(c0['EMA50'])
    ema200     = float(c0['EMA200'])
    rsi        = float(c0['RSI'])

    if np.isnan(atr) or atr <= 0:
        return None, None, None, None, ""

    if float(c0['Range']) > (atr * 1.75) or float(c0['Range']) < (atr * 0.50):
        return None, None, None, None, ""

    if abs(price - ema20) > (atr * 1.20) or er < 0.32:
        return None, None, None, None, ""

    pool_10   = df_15m.iloc[-12:-3]
    pool_high = float(pool_10['High'].max())
    pool_low  = float(pool_10['Low'].min())

    # BULLISH VERIFICATION
    if regime == "BULLISH":
        if now_utc < sl_direction_lock[pair_name]["BUY"]:
            return None, None, None, None, ""

        macro_ok, macro_label = verify_gate1_macro_and_portfolio(pair_name, pair_info, "BUY", macro_radar)
        m5_ok = verify_5m_micro_bos(df_5m, "BUY")

        not_at_exhausted_top = (htf["day_high"] - price) >= (atr * 0.60)
        vacuum_room_up       = (htf["major_res"] - price) >= (atr * 1.65) or (price > htf["major_res"])

        if macro_ok and m5_ok and not_at_exhausted_top and vacuum_room_up and (42 <= rsi <= 61):
            score = 0
            reasons = []

            if price > ema20 and ema20 > ema50 and ema50 >= ema200:
                score += 25

            touched_value_zone = (low_0 <= ema20 * 1.0008 and low_0 >= ema50 * 0.9990) or (float(c1['Low']) <= ema20 * 1.0008 and float(c1['Low']) >= ema50 * 0.9990)
            swept_inducement   = (low_0 < min(float(c1['Low']), float(c2['Low'])) and price > float(c1['High'])) or (low_0 < pool_low and price > pool_low)
            if touched_value_zone or swept_inducement:
                score += 30
                reasons.append("Liquidity Sweep")

            no_upper_rejection = u_wick <= (float(c0['Range']) * 0.22)
            strong_bull_close  = (price > open_0) and (price > float(c1['High'])) and (close_pos >= 0.72) and (disp_power >= 1.05 or l_wick >= float(c0['Range']) * 0.35)
            if no_upper_rejection and strong_bull_close:
                score += 25
                reasons.append("Order-Flow Absorption")

            if er >= 0.36 and float(c0['Low']) >= float(c2['Low']) * 0.9995:
                score += 20
                reasons.append("5M Micro-BOS")

            if score >= 85:
                swing_low_ref = min(low_0, float(c1['Low']), float(c2['Low']))
                sl_price = swing_low_ref - (atr * 0.35)
                setup_desc = f"Institutional Confluence ({score}%) — " + " + ".join(reasons)
                return "BUY (LONG) 🟢", "BUY", setup_desc, sl_price, macro_label

    # BEARISH VERIFICATION
    if regime == "BEARISH":
        if now_utc < sl_direction_lock[pair_name]["SELL"]:
            return None, None, None, None, ""

        macro_ok, macro_label = verify_gate1_macro_and_portfolio(pair_name, pair_info, "SELL", macro_radar)
        m5_ok = verify_5m_micro_bos(df_5m, "SELL")

        not_at_exhausted_bottom = (price - htf["day_low"]) >= (atr * 0.60)
        vacuum_room_down        = (price - htf["major_sup"]) >= (atr * 1.65) or (price < htf["major_sup"])

        if macro_ok and m5_ok and not_at_exhausted_bottom and vacuum_room_down and (39 <= rsi <= 58):
            score = 0
            reasons = []

            if price < ema20 and ema20 < ema50 and ema50 <= ema200:
                score += 25

            touched_supply_zone = (high_0 >= ema20 * 0.9992 and high_0 <= ema50 * 1.0010) or (float(c1['High']) >= ema20 * 0.9992 and float(c1['High']) <= ema50 * 1.0010)
            swept_inducement    = (high_0 > max(float(c1['High']), float(c2['High'])) and price < float(c1['Low'])) or (high_0 > pool_high and price < pool_high)
            if touched_supply_zone or swept_inducement:
                score += 30
                reasons.append("Supply Sweep")

            no_lower_rejection = l_wick <= (float(c0['Range']) * 0.22)
            strong_bear_close  = (price < open_0) and (price < float(c1['Low'])) and (close_pos <= 0.28) and (disp_power >= 1.05 or u_wick >= float(c0['Range']) * 0.35)
            if no_lower_rejection and strong_bear_close:
                score += 25
                reasons.append("Supply Absorption")

            if er >= 0.36 and float(c0['High']) <= float(c2['High']) * 1.0005:
                score += 20
                reasons.append("5M Micro-BOS")

            if score >= 85:
                swing_high_ref = max(high_0, float(c1['High']), float(c2['High']))
                sl_price = swing_high_ref + (atr * 0.35)
                setup_desc = f"Institutional Confluence ({score}%) — " + " + ".join(reasons)
                return "SELL (SHORT) 🔴", "SELL", setup_desc, sl_price, macro_label

    return None, None, None, None, ""

def send_live_forex_summary():
    forex_open = is_forex_market_open()
    market_state = "🟢 ACTIVE" if forex_open else "🟡 WEEKEND CRYPTO MODE"
    _, session_name = get_institutional_session(True)
    radar = build_global_macro_radar(forex_open)

    report = (
        f"🏦 <b>LIVE MARKET STATUS</b> 🏦\n\n"
        f"📢 <b>Market:</b> {market_state} ({session_name})\n"
        f"🌐 <b>Macro:</b> DXY: <b>{radar['DXY']['state']}</b> | US10Y: <b>{radar['US10Y']['state']}</b> | S&P500: <b>{radar['SP500']['state']}</b>\n\n"
    )

    for name, info in FOREX_PAIRS.items():
        try:
            ticker = yf.Ticker(info["symbol"])
            df_15m = ticker.history(period="5d", interval="15m")
            df_1h  = ticker.history(period="1mo", interval="1h")
            if df_15m.empty or len(df_15m) < 50 or df_1h.empty or len(df_1h) < 40:
                continue

            df_15m = calculate_proprietary_metrics(df_15m)
            df_1h  = calculate_proprietary_metrics(df_1h)
            htf    = analyze_4h_and_1h_regime(df_1h)

            latest = df_15m.iloc[-1]
            price  = float(latest['Close'])
            rsi    = float(latest['RSI'])
            p_str  = format_price(price, info["decimals"])

            report += f"🔹 <b>{name}:</b> <code>{p_str}</code> | Trend: <b>{htf['regime']}</b> | RSI: {rsi:.0f}\n"
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
                    send_live_forex_summary()
    except Exception:
        pass

def manage_active_forex_trades(pair_name, candle_time, df_1m, latest_live_15m, closed_15m, decimals):
    """हर अपडेट मैसेज अब 100% फ्रेश और सिंगल आएगा (पुरानी लिस्ट नहीं चिपकेगी)"""
    global daily_realized_pnl, total_account_pnl, pair_cooldown_until, sl_direction_lock

    ema20 = float(closed_15m['EMA20'])
    ema50 = float(closed_15m['EMA50'])
    now_utc = get_utc_now()

    if df_1m is not None and not df_1m.empty:
        price = float(df_1m.iloc[-1]['Close'])
        now_time = to_utc_timestamp(df_1m.index[-1])
    else:
        price = float(latest_live_15m['Close'])
        now_time = to_utc_timestamp(candle_time)

    for trade in daily_trades_log:
        if trade["pair"] == pair_name and trade["is_open"]:
            if to_utc_timestamp(candle_time) < trade["entry_candle_time"]:
                continue

            entry_str  = format_price(trade["entry"], decimals)
            target_str = format_price(trade["target"], decimals)
            sl_str     = format_price(trade["sl"], decimals)
            price_str  = format_price(price, decimals)

            check_since = trade.get("be_activated_time", trade["entry_exact_time"])

            if df_1m is not None and not df_1m.empty:
                idx_utc = df_1m.index.tz_convert(timezone.utc) if getattr(df_1m.index, "tz", None) is not None else df_1m.index
                recent_ticks = df_1m[idx_utc > check_since]
                if not recent_ticks.empty:
                    current_high = float(recent_ticks['High'].max())
                    current_low  = float(recent_ticks['Low'].min())
                else:
                    current_high = price
                    current_low  = price
            else:
                current_high = price
                current_low  = price

            # 1. 1:1 आने पर Breakeven अपडेट
            if not trade["trailed_to_cost"]:
                half_reached = False
                if trade["direction"] == "BUY" and current_high >= trade["half_target"]:
                    half_reached = True
                elif trade["direction"] == "SELL" and current_low <= trade["half_target"]:
                    half_reached = True

                if half_reached:
                    trade["trailed_to_cost"]   = True
                    trade["sl"]                = trade["entry"]
                    trade["be_activated_time"] = now_time
                    half_prof                  = trade["max_risk"] * 0.5
                    trade["status"]            = "1:1 LOCKED"
                    send_telegram_message(
                        f"🛡️ <b>1:1 TARGET REACHED — MOVE SL TO ENTRY</b> 🛡️\n\n"
                        f"🌍 <b>Asset:</b> {trade['pair']} (<code>{trade['mt5_name']}</code>)\n"
                        f"📍 <b>Entry Price:</b> {entry_str}  |  <b>Live Price:</b> {price_str}\n"
                        f"📢 <b>Action:</b> 50% लॉट (+${half_prof:.0f}) बुक करें और बचे हुए लॉट का SL <b>{entry_str}</b> पर सेट करें।"
                    )
                    continue

            # 2. FULL TARGET या STOP-LOSS अपडेट (एकदम फ्रेश मैसेज)
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
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=60)
                net_win = trade["expected_profit"]
                daily_realized_pnl += net_win
                total_account_pnl += net_win
                trade["status"] = "TARGET HIT"
                send_telegram_message(
                    f"🎯 <b>FULL TARGET HIT</b> 🎯\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<code>{trade['mt5_name']}</code>)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target:</b> {target_str}\n"
                    f"💰 <b>Profit:</b> <b>+${net_win:.0f}</b>"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=60)
                half_prof = trade["max_risk"] * 0.5
                daily_realized_pnl += half_prof
                total_account_pnl += half_prof
                trade["status"] = "BREAKEVEN EXIT"
                send_telegram_message(
                    f"🛡️ <b>BREAKEVEN EXIT</b> 🛡️\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<code>{trade['mt5_name']}</code>)\n"
                    f"📍 <b>Exit at Cost:</b> {entry_str}  |  <b>Locked Profit:</b> <b>+${half_prof:.0f}</b>"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(hours=2)
                sl_direction_lock[pair_name][trade["direction"]] = now_utc + timedelta(hours=6)

                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_account_pnl -= loss_amt
                trade["status"] = "SL HIT"
                send_telegram_message(
                    f"🛑 <b>STOP-LOSS HIT</b> 🛑\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<code>{trade['mt5_name']}</code>)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>SL:</b> {sl_str}\n"
                    f"⚖️ <b>Risk:</b> -${loss_amt:.0f}"
                )
                continue

            # 3. 15M Structure Invalidation Exit
            hold_minutes = (to_utc_timestamp(candle_time) - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= 45 and not trade["trailed_to_cost"]:
                closed_price = float(closed_15m['Close'])
                trend_shifted = False
                if trade["direction"] == "BUY" and closed_price < ema20 and closed_price < ema50 and closed_price < (trade["entry"] - trade["sl_dist"] * 0.55):
                    trend_shifted = True
                elif trade["direction"] == "SELL" and closed_price > ema20 and closed_price > ema50 and closed_price > (trade["entry"] + trade["sl_dist"] * 0.55):
                    trend_shifted = True

                if trend_shifted:
                    trade["is_open"] = False
                    pair_cooldown_until[pair_name] = now_utc + timedelta(hours=2)
                    sl_direction_lock[pair_name][trade["direction"]] = now_utc + timedelta(hours=4)
                    early_cut = round(trade["max_risk"] * 0.55, 0)
                    daily_realized_pnl -= early_cut
                    total_account_pnl -= early_cut
                    trade["status"] = "EARLY EXIT"
                    send_telegram_message(
                        f"🔄 <b>EARLY STRUCTURE EXIT</b> 🔄\n\n"
                        f"🌍 <b>Asset:</b> {trade['pair']} (<code>{trade['mt5_name']}</code>)\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Close Now:</b> {price_str}\n"
                        f"📢 <b>Note:</b> 15M स्ट्रक्चर शिफ्ट होने के कारण ट्रेड अभी क्लोज करें।"
                    )
                    continue

def scan_forex_market():
    global trades_today, current_day, last_candle_traded, daily_trades_log, daily_realized_pnl

    today = get_ist_now().date()
    if today != current_day:
        current_day = today
        trades_today = 0
        daily_trades_log = []
        daily_realized_pnl = 0.0
        last_candle_traded = {name: None for name in FOREX_PAIRS}

    if daily_realized_pnl <= -DAILY_PROTECTION_CAP:
        return

    forex_open  = is_forex_market_open()
    macro_radar = build_global_macro_radar(forex_open)
    usdjpy_rate = macro_radar["USDJPY"]["price"] if macro_radar["USDJPY"]["price"] > 80.0 else 152.0

    for name, info in FOREX_PAIRS.items():
        if not forex_open and not info.get("is_crypto", False):
            continue

        try:
            ticker = yf.Ticker(info["symbol"])
            df_1h  = ticker.history(period="1mo", interval="1h")
            df_15m = ticker.history(period="5d", interval="15m")
            df_5m  = ticker.history(period="5d", interval="5m")
            df_1m  = ticker.history(period="1d", interval="1m")

            if df_15m.empty or len(df_15m) < 60 or df_1h.empty or len(df_1h) < 40:
                continue

            df_1h  = calculate_proprietary_metrics(df_1h)
            df_15m = calculate_proprietary_metrics(df_15m)

            latest_live_15m = df_15m.iloc[-1]
            closed_15m      = df_15m.iloc[-2]
            closed_time     = to_utc_timestamp(df_15m.index[-2])
            live_bar_open   = to_utc_timestamp(df_15m.index[-1])

            live_price     = float(df_1m.iloc[-1]['Close']) if (df_1m is not None and not df_1m.empty) else float(latest_live_15m['Close'])
            signal_close_p = float(closed_15m['Close'])
            exact_now_time = to_utc_timestamp(df_1m.index[-1]) if (df_1m is not None and not df_1m.empty) else get_utc_now()
            atr_15m        = float(closed_15m['ATR'])
            atr_1h         = float(df_1h.iloc[-2]['ATR']) if not np.isnan(df_1h.iloc[-2]['ATR']) else (atr_15m * 2.0)
            dec            = info["decimals"]

            manage_active_forex_trades(name, closed_time, df_1m, latest_live_15m, closed_15m, dec)

            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            open_trades_count = sum(1 for t in daily_trades_log if t["is_open"])
            if open_trades_count >= MAX_CONCURRENT_TRADES:
                continue

            if last_candle_traded[name] == closed_time:
                continue

            seconds_since_close = (exact_now_time - live_bar_open).total_seconds()
            if seconds_since_close > 240 or abs(live_price - signal_close_p) > (atr_15m * 0.18):
                continue

            signal, direction, strategy_used, custom_sl, macro_note = evaluate_sovereign_atoz_strategy(
                name, info, df_1h, df_15m, df_5m, macro_radar
            )

            if signal and custom_sl is not None:
                if direction == "BUY" and custom_sl >= live_price:
                    continue
                if direction == "SELL" and custom_sl <= live_price:
                    continue

                raw_sl_dist  = abs(live_price - custom_sl)
                safe_min_sl  = max(atr_15m * 1.35, atr_1h * 0.45, info.get("min_sl_price_dist", atr_15m * 1.35))
                safe_max_sl  = max(atr_15m * 2.40, safe_min_sl * 1.35)
                sl_dist      = round(max(safe_min_sl, min(raw_sl_dist, safe_max_sl)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                trades_today += 1

                rr_ratio    = 2.2
                target_dist = round(sl_dist * rr_ratio, dec)

                if direction == "BUY":
                    sl_level          = round(live_price - sl_dist, dec)
                    half_target_level = round(live_price + sl_dist, dec)
                    target_level      = round(live_price + target_dist, dec)
                else:
                    sl_level          = round(live_price + sl_dist, dec)
                    half_target_level = round(live_price - sl_dist, dec)
                    target_level      = round(live_price - target_dist, dec)

                max_risk_usd  = RISK_PER_TRADE_USD
                expected_prof = round(max_risk_usd * rr_ratio, 0)
                exact_lot     = calculate_exact_mt5_lot_size(name, info, live_price, sl_dist, max_risk_usd, usdjpy_rate)

                trade_id = trades_today
                daily_trades_log.append({
                    "id": trade_id,
                    "pair": name,
                    "mt5_name": info["mt5_name"],
                    "action": signal,
                    "direction": direction,
                    "strategy": strategy_used,
                    "lot_size": exact_lot,
                    "entry": live_price,
                    "sl": sl_level,
                    "sl_dist": sl_dist,
                    "half_target": half_target_level,
                    "target": target_level,
                    "max_risk": max_risk_usd,
                    "expected_profit": expected_prof,
                    "entry_candle_time": closed_time,
                    "entry_exact_time": exact_now_time,
                    "be_activated_time": exact_now_time,
                    "trailed_to_cost": False,
                    "is_open": True,
                    "status": "RUNNING"
                })

                entry_s  = format_price(live_price, dec)
                sl_s     = format_price(sl_level, dec)
                half_s   = format_price(half_target_level, dec)
                target_s = format_price(target_level, dec)

                sl_pips_str     = calculate_pips_or_points(name, sl_dist, dec)
                target_pips_str = calculate_pips_or_points(name, target_dist, dec)
                htf_info        = analyze_4h_and_1h_regime(df_1h)

                exec_note = ""
                if "GOLD" in name:
                    exec_note = f"\n📌 <b>Note:</b> MT5 Spot Price पर एंट्री लें ({sl_pips_str} SL | {target_pips_str} Target)।"

                # 100% FRESH & STANDALONE SIGNAL MESSAGE (कोई पुरानी हिस्ट्री नहीं चिपकेगी)
                msg = (
                    f"🏦 <b>INSTITUTIONAL TRADE SIGNAL</b> 🏦\n\n"
                    f"🌍 <b>Asset:</b> <b>{name}</b> (<code>{info['mt5_name']}</code>)\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n"
                    f"📊 <b>Structure:</b> 4H/1H {htf_info['regime']} | {macro_note}\n"
                    f"🧠 <b>Setup:</b> {strategy_used}\n\n"
                    f"📍 <b>Entry Price:</b> <code>{entry_s}</code>\n"
                    f"🛑 <b>Stop-Loss:</b> <code>{sl_s}</code>  <i>({sl_pips_str})</i>\n"
                    f"🛡️ <b>1:1 Lock:</b> <code>{half_s}</code>\n"
                    f"🎯 <b>Target (1:2.2):</b> <code>{target_s}</code>  <i>({target_pips_str})</i>\n\n"
                    f"💼 <b>MT5 Lot Size:</b> <b><code>{exact_lot:.2f} Lot</code></b>\n"
                    f"⚖️ <b>Risk:</b> ${max_risk_usd:.0f}  ➡️  <b>Reward:</b> +${expected_prof:.0f}"
                    f"{exec_note}"
                )

                chart_buf = generate_trade_position_chart(
                    df_15m, name, direction, live_price, sl_level, target_level,
                    half_target_level, dec, exact_lot, max_risk_usd, expected_prof, rr_ratio
                )
                send_telegram_signal_with_chart(msg, chart_buf)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    # एकदम फ्रेश और छोटा स्टार्टअप मैसेज (पिछली किसी बात का कोई जिक्र नहीं)
    send_telegram_message(
        "🏦 <b>Institutional Trading Engine Online</b> 🏦\n\n"
        "• <b>Account:</b> $100,000 Standard Mode\n"
        "• <b>Markets:</b> Forex Majors, Gold & BTC/USD\n"
        "• <b>Scanner:</b> Live 4H + 1H + 15M + 5M Active"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 25:
            scan_forex_market()
            last_scan_time = time.time()
        time.sleep(3)
