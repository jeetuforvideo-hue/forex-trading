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
# 1. $100,000 PROP-FIRM — M5 HIGH-ACCURACY SCALPING ENGINE (9 PAIRS)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "5610520408"

TOTAL_CAPITAL_USD     = 100000.0
MAX_DAILY_DRAWDOWN    = 5000.0
MAX_TOTAL_DRAWDOWN    = 10000.0

# SCALPING RISK & REWARD SETTINGS
SCALP_RISK_USD        = 400.0     # $400 Risk (0.40%) with Tight M5 SL = Big Lot Size
SCALP_RR_RATIO        = 1.6       # 1:1.6 Fast Scalping Target (+$640 Profit per trade)
DAILY_PROTECTION_CAP  = 2000.0    # Daily drawdown safety cap
MAX_CONCURRENT_TRADES = 3         # एक समय पर अधिकतम 3 स्कैल्प ट्रेड्स चल सकते हैं

DXY_SYMBOL = "DX-Y.NYB"

# 9 HIGH-LIQUIDITY SCALPING PAIRS
FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "symbol": "GC=F",
        "mt5_name": "XAUUSD",
        "decimals": 2,
        "unit_name": "Points",
        "macro_mode": "USD_INVERSE",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 3.00,    # $3.00 (30 Points) Tight Scalp SL
        "max_sl_dist": 5.50,    # $5.50 (55 Points) Max Scalp SL
        "max_lot_cap": 2.00,
        "is_crypto": False
    },
    "EUR/USD": {
        "symbol": "EURUSD=X",
        "mt5_name": "EURUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "macro_mode": "USD_INVERSE",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0009,  # 9 Pips Tight Scalp SL
        "max_sl_dist": 0.0018,  # 18 Pips Max Scalp SL
        "max_lot_cap": 4.50,
        "is_crypto": False
    },
    "GBP/USD": {
        "symbol": "GBPUSD=X",
        "mt5_name": "GBPUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "macro_mode": "USD_INVERSE",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0010,  # 10 Pips Tight Scalp SL
        "max_sl_dist": 0.0020,  # 20 Pips Max Scalp SL
        "max_lot_cap": 4.50,
        "is_crypto": False
    },
    "USD/JPY": {
        "symbol": "JPY=X",
        "mt5_name": "USDJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "macro_mode": "USD_DIRECT",
        "usd_exposure": "LONG_USD_ON_BUY",
        "min_sl_dist": 0.100,   # 10 Pips Tight Scalp SL
        "max_sl_dist": 0.200,   # 20 Pips Max Scalp SL
        "max_lot_cap": 4.50,
        "is_crypto": False
    },
    "GBP/JPY": {
        "symbol": "GBPJPY=X",
        "mt5_name": "GBPJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "macro_mode": "CROSS_PAIR",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 0.140,   # 14 Pips Tight Scalp SL
        "max_sl_dist": 0.260,   # 26 Pips Max Scalp SL
        "max_lot_cap": 4.00,
        "is_crypto": False
    },
    "EUR/JPY": {
        "symbol": "EURJPY=X",
        "mt5_name": "EURJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "macro_mode": "CROSS_PAIR",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 0.120,   # 12 Pips Tight Scalp SL
        "max_sl_dist": 0.240,   # 24 Pips Max Scalp SL
        "max_lot_cap": 4.00,
        "is_crypto": False
    },
    "AUD/USD": {
        "symbol": "AUDUSD=X",
        "mt5_name": "AUDUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "macro_mode": "USD_INVERSE",
        "usd_exposure": "SHORT_USD_ON_BUY",
        "min_sl_dist": 0.0008,  # 8 Pips Tight Scalp SL
        "max_sl_dist": 0.0016,  # 16 Pips Max Scalp SL
        "max_lot_cap": 4.50,
        "is_crypto": False
    },
    "USD/CAD": {
        "symbol": "CAD=X",
        "mt5_name": "USDCAD",
        "decimals": 5,
        "unit_name": "Pips",
        "macro_mode": "USD_DIRECT",
        "usd_exposure": "LONG_USD_ON_BUY",
        "min_sl_dist": 0.0009,  # 9 Pips Tight Scalp SL
        "max_sl_dist": 0.0018,  # 18 Pips Max Scalp SL
        "max_lot_cap": 4.50,
        "is_crypto": False
    },
    "BTC/USD (BITCOIN)": {
        "symbol": "BTC-USD",
        "mt5_name": "BTCUSD",
        "decimals": 2,
        "unit_name": "Points",
        "macro_mode": "CRYPTO_PURE",
        "usd_exposure": "NEUTRAL",
        "min_sl_dist": 220.0,   # $220 Safe Scalp SL
        "max_sl_dist": 420.0,   # $420 Max Scalp SL
        "max_lot_cap": 2.50,
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

def get_scalping_session(is_crypto=False):
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)
    if is_crypto:
        return True, "Crypto 24/7 Fast Scalping"
    if 3.25 <= hr <= 4.75:
        return False, "Rollover Spread Pause"
    elif 4.75 < hr < 12.0:
        return True, "Asian Scalping Session"
    elif 12.0 <= hr < 18.0:
        return True, "London Fast Scalping"
    elif 18.0 <= hr < 22.5:
        return True, "London + NY Overlap Scalping"
    else:
        return True, "New York Scalping Session"

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

def calculate_scalp_lot_size(pair_name, pair_info, entry_price, sl_dist, risk_usd, live_usdjpy_rate=152.0):
    if sl_dist <= 0:
        return 0.20

    if "GOLD" in pair_name:
        loss_per_1_lot = sl_dist * 100.0
    elif "BTC" in pair_name:
        loss_per_1_lot = sl_dist * 1.0
    elif pair_name.endswith("JPY"):
        usdjpy = live_usdjpy_rate if live_usdjpy_rate > 80.0 else 152.0
        loss_per_1_lot = (sl_dist * 100000.0) / usdjpy
    elif "USD/CAD" in pair_name:
        usdcad = entry_price if entry_price > 1.0 else 1.38
        loss_per_1_lot = (sl_dist * 100000.0) / usdcad
    else:
        pips = sl_dist * 10000.0
        loss_per_1_lot = pips * 10.0

    if loss_per_1_lot <= 0:
        return 0.20

    raw_lot = risk_usd / loss_per_1_lot
    max_cap = pair_info.get("max_lot_cap", 4.00)
    return max(0.10, min(round(raw_lot, 2), max_cap))

def generate_trade_position_chart(df_5m, pair_name, direction, entry_p, sl_p, tp_p, half_p, decimals, lot_size, risk_usd, reward_usd, rr_ratio):
    if not CHART_AVAILABLE:
        return None
    try:
        sub = df_5m.iloc[-42:-1].copy()
        if sub.empty or len(sub) < 15:
            return None

        fig, ax = plt.subplots(figsize=(11, 6.2), dpi=130)
        fig.patch.set_facecolor('#f3f4f6')
        ax.set_facecolor('#f3f4f6')

        n_bars = len(sub)
        x_vals = np.arange(n_bars)

        if 'EMA9' in sub.columns and 'EMA20' in sub.columns:
            ax.plot(x_vals, sub['EMA9'].values, color='#22c55e', linewidth=1.3, label='EMA 9', zorder=4)
            ax.plot(x_vals, sub['EMA20'].values, color='#ef4444', linewidth=1.3, label='EMA 20', zorder=4)

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

        ax.text(box_center_x, tp_p, f" Scalp Target: {tp_s} ({tp_pct:.2f}%), Profit: +${reward_usd:.0f} ", color='white', fontweight='bold', fontsize=8.5, ha='center', va='bottom' if direction == "BUY" else 'top', bbox=dict(boxstyle='round,pad=0.35', facecolor='#16a34a', edgecolor='none'), zorder=7)
        ax.text(box_center_x, sl_p, f" Stop-Loss: {sl_s} ({sl_pct:.2f}%), Risk: -${risk_usd:.0f} ", color='white', fontweight='bold', fontsize=8.5, ha='center', va='top' if direction == "BUY" else 'bottom', bbox=dict(boxstyle='round,pad=0.35', facecolor='#dc2626', edgecolor='none'), zorder=7)

        pos_title = "SCALP LONG" if direction == "BUY" else "SCALP SHORT"
        ax.text(box_center_x, entry_p, f" {pos_title} @ {entry_s} | LOT: {lot_size:.2f}\n R:R = 1:{rr_ratio} | 0.8R Lock: {half_s} ", color='white', fontweight='bold', fontsize=8.2, ha='center', va='center', bbox=dict(boxstyle='round,pad=0.38', facecolor='#dc2626' if direction == "SELL" else '#1f2937', edgecolor='white', alpha=0.92), zorder=8)

        ax.yaxis.tick_right()
        right_edge_x = box_start_x + box_width + 4.5
        ax.set_xlim(-1, right_edge_x)
        all_y = list(sub['High'].values) + list(sub['Low'].values) + [tp_p, sl_p, entry_p]
        y_min, y_max = min(all_y), max(all_y)
        y_pad = (y_max - y_min) * 0.14
        ax.set_ylim(y_min - y_pad, y_max + y_pad)

        for tag_price, tag_bg in [(tp_p, '#16a34a'), (half_p, '#0284c7'), (entry_p, '#374151'), (sl_p, '#dc2626')]:
            ax.text(right_edge_x + 0.2, tag_price, f" {format_price(tag_price, decimals)} ", color='white', fontweight='bold', fontsize=8, va='center', ha='left', bbox=dict(boxstyle='square,pad=0.25', facecolor=tag_bg, edgecolor='none'), clip_on=False, zorder=9)

        ax.set_title(f"{pair_name} — M5 High-Accuracy Scalper (EMA 9/20 | {pos_title} | {lot_size:.2f} Lot)", loc='left', fontsize=11, fontweight='bold', color='#111827', pad=10)
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
                files = {"photo": ("scalp_setup.png", chart_buf, "image/png")}
                data = {"chat_id": target_chat_id, "caption": caption_html, "parse_mode": "HTML"}
                res = requests.post(url, data=data, files=files, timeout=15)
                if res.status_code == 200:
                    return
            else:
                files = {"photo": ("scalp_setup.png", chart_buf, "image/png")}
                data = {"chat_id": target_chat_id, "caption": "⚡ <b>M5 Live Scalping Chart (EMA 9 & 20)</b>", "parse_mode": "HTML"}
                requests.post(url, data=data, files=files, timeout=15)
                send_telegram_message(caption_html, target_chat_id)
                return
        except Exception as e:
            print(f"Photo Send Error: {e}")
    send_telegram_message(caption_html, target_chat_id)

# =====================================================================
# 2. M5/M1 SCALPING INDICATORS (EMA 9, EMA 20, EMA 50, ATR, RSI)
# =====================================================================
def calculate_scalp_metrics(df):
    df = df.copy()
    df['EMA9']  = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['Close'].ewm(span=50, adjust=False).mean()

    hl = df['High'] - df['Low']
    hc = np.abs(df['High'] - df['Close'].shift())
    lc = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    df['Body']       = np.abs(df['Close'] - df['Open'])
    df['Range']      = (df['High'] - df['Low']).replace(0, 1e-9)
    df['Body_Ratio'] = df['Body'] / df['Range']
    df['Upper_Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Close_Pos']  = (df['Close'] - df['Low']) / df['Range']

    avg_body = df['Body'].rolling(16).mean().replace(0, 1e-9)
    df['Disp_Power'] = df['Body'] / avg_body

    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/9, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/9, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50.0)

    return df

def get_fast_dxy_and_usdjpy(forex_open):
    if not forex_open:
        return "NEUTRAL", 152.0
    dxy_bias = "NEUTRAL"
    usdjpy_rate = 152.0
    try:
        df_dxy = yf.Ticker(DXY_SYMBOL).history(period="5d", interval="5m")
        if not df_dxy.empty and len(df_dxy) >= 25:
            df_dxy = calculate_scalp_metrics(df_dxy)
            c = df_dxy.iloc[-2]
            if float(c['Close']) > float(c['EMA9']) > float(c['EMA20']):
                dxy_bias = "BULLISH"
            elif float(c['Close']) < float(c['EMA9']) < float(c['EMA20']):
                dxy_bias = "BEARISH"
    except Exception:
        pass

    try:
        df_jpy = yf.Ticker("JPY=X").history(period="1d", interval="5m")
        if not df_jpy.empty:
            usdjpy_rate = float(df_jpy.iloc[-1]['Close'])
    except Exception:
        pass

    return dxy_bias, usdjpy_rate

def check_portfolio_and_dxy(pair_info, trade_dir, dxy_bias):
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
                    return False, "USD Clash Blocked"

    mode = pair_info.get("macro_mode", "USD_INVERSE")
    if mode == "USD_INVERSE":
        if trade_dir == "BUY" and dxy_bias == "BULLISH":
            return False, "DXY Bullish"
        if trade_dir == "SELL" and dxy_bias == "BEARISH":
            return False, "DXY Bearish"
        return True, f"DXY: {dxy_bias}"
    elif mode == "USD_DIRECT":
        if trade_dir == "BUY" and dxy_bias == "BEARISH":
            return False, "DXY Bearish"
        if trade_dir == "SELL" and dxy_bias == "BULLISH":
            return False, "DXY Bullish"
        return True, f"DXY: {dxy_bias}"

    return True, "Cross/Crypto Flow"

# =====================================================================
# 3. HIGH-ACCURACY M5 SCALPING SETUPS (NO LAGGING CROSSOVERS)
# =====================================================================
def evaluate_m5_scalp_strategy(pair_name, pair_info, df_15m, df_5m, dxy_bias):
    """
    केवल सबसे हाई-एक्यूरेसी वाले सेटअप (Lagging Crossover हटा दिया गया है):
    • Setup 1: EMA 9/20 Pullback Continuation (Trend Pullback Bounce)
    • Setup 2: 6-Bar Stop-Hunt Liquidity Sweep & Reclaim (Operator Trap)
    • Setup 3: 4-Bar Tight Micro-Box Displacement Breakout
    """
    now_utc = get_utc_now()
    if now_utc < pair_cooldown_until.get(pair_name, datetime.min.replace(tzinfo=timezone.utc)):
        return None, None, None, None, ""

    session_ok, _ = get_scalping_session(pair_info.get("is_crypto", False))
    if not session_ok:
        return None, None, None, None, ""

    # 15M Directional Bias (Allows Pullback & Sweep Setups cleanly)
    m15_c = df_15m.iloc[-2]
    m15_close = float(m15_c['Close'])
    m15_ema9  = float(m15_c['EMA9'])
    m15_ema20 = float(m15_c['EMA20'])
    m15_ema50 = float(m15_c['EMA50'])

    m15_bull_ok = (m15_close >= m15_ema20) or (m15_ema9 >= m15_ema20) or (m15_close >= m15_ema50)
    m15_bear_ok = (m15_close <= m15_ema20) or (m15_ema9 <= m15_ema20) or (m15_close <= m15_ema50)

    # 5M Closed Candles
    c0 = df_5m.iloc[-2]  # आखिरी बंद हुई 5-मिनट कैंडल
    c1 = df_5m.iloc[-3]

    price      = float(c0['Close'])
    open_0     = float(c0['Open'])
    high_0     = float(c0['High'])
    low_0      = float(c0['Low'])
    atr_5m     = float(c0['ATR'])
    disp_power = float(c0['Disp_Power'])
    close_pos  = float(c0['Close_Pos'])
    body_ratio = float(c0['Body_Ratio'])
    u_wick     = float(c0['Upper_Wick'])
    l_wick     = float(c0['Lower_Wick'])
    ema9       = float(c0['EMA9'])
    ema20      = float(c0['EMA20'])
    ema50      = float(c0['EMA50'])
    rsi        = float(c0['RSI'])

    if np.isnan(atr_5m) or atr_5m <= 0:
        return None, None, None, None, ""

    # असामान्य न्यूज़ स्पाइक या एकदम मरी हुई कैंडल को छोड़ें
    if float(c0['Range']) > (atr_5m * 2.8) or float(c0['Range']) < (atr_5m * 0.25):
        return None, None, None, None, ""

    # पिछली 6 कैंडल्स (30 मिनट) का स्विंग हाई और लो (Stop-Hunt Sweep के लिए)
    micro_pool = df_5m.iloc[-8:-2]
    micro_high = float(micro_pool['High'].max())
    micro_low  = float(micro_pool['Low'].min())

    # पिछली 4 कैंडल्स का टाइट कंसोलिडेशन बॉक्स
    box_4     = df_5m.iloc[-6:-2]
    box_4_hi  = float(box_4['High'].max())
    box_4_lo  = float(box_4['Low'].min())
    box_4_rng = box_4_hi - box_4_lo

    # =========================================================================
    # BULLISH M5 SCALPING SETUPS (BUY)
    # =========================================================================
    if m15_bull_ok and now_utc >= sl_direction_lock[pair_name]["BUY"]:
        macro_ok, macro_label = check_portfolio_and_dxy(pair_info, "BUY", dxy_bias)
        if macro_ok and (34 <= rsi <= 74):

            # SETUP 1 (HIGH ACCURACY): EMA 9/20 Pullback Bounce
            ema_bull_aligned = (ema9 >= ema20 * 0.9998) and (price > ema20)
            pulled_to_ema    = (low_0 <= ema9 * 1.0006 and low_0 >= ema50 * 0.9985) or (float(c1['Low']) <= ema20 * 1.0006)
            bull_bounce      = (price > open_0) and (price >= ema9) and (close_pos >= 0.56) and (price > float(c1['High']) or body_ratio >= 0.45)
            if ema_bull_aligned and pulled_to_ema and bull_bounce:
                sl_price = min(low_0, float(c1['Low'])) - (atr_5m * 0.25)
                return "SCALP BUY (LONG) 🟢", "BUY", "M5 Setup 1: EMA 9/20 Pullback Bounce", sl_price, macro_label

            # SETUP 2 (HIGH ACCURACY): 6-Bar Stop-Hunt Liquidity Sweep & Reclaim
            swept_low = (low_0 < micro_low and price > micro_low) or (float(c1['Low']) < micro_low and price > float(c1['High']) and price > micro_low)
            reclaimed = (price > open_0) and (close_pos >= 0.58) and (l_wick >= float(c0['Range']) * 0.25 or disp_power >= 0.85)
            if swept_low and reclaimed:
                sl_price = min(low_0, float(c1['Low'])) - (atr_5m * 0.20)
                return "SCALP BUY (LONG) 🟢", "BUY", "M5 Setup 2: Stop-Hunt Liquidity Sweep & Reclaim", sl_price, macro_label

            # SETUP 3 (HIGH ACCURACY): 4-Bar Micro-Box Displacement Breakout
            if box_4_rng <= (atr_5m * 2.4) and price > box_4_hi and price > open_0 and ema9 >= ema20 and body_ratio >= 0.55 and close_pos >= 0.68 and disp_power >= 1.05:
                sl_price = max(low_0 - (atr_5m * 0.20), box_4_lo)
                return "SCALP BUY (LONG) 🟢", "BUY", "M5 Setup 3: Micro-Box Displacement Breakout", sl_price, macro_label

    # =========================================================================
    # BEARISH M5 SCALPING SETUPS (SELL)
    # =========================================================================
    if m15_bear_ok and now_utc >= sl_direction_lock[pair_name]["SELL"]:
        macro_ok, macro_label = check_portfolio_and_dxy(pair_info, "SELL", dxy_bias)
        if macro_ok and (26 <= rsi <= 66):

            # SETUP 1 (HIGH ACCURACY): EMA 9/20 Pullback Rejection
            ema_bear_aligned = (ema9 <= ema20 * 1.0002) and (price < ema20)
            pulled_to_ema    = (high_0 >= ema9 * 0.9994 and high_0 <= ema50 * 1.0015) or (float(c1['High']) >= ema20 * 0.9994)
            bear_reject      = (price < open_0) and (price <= ema9) and (close_pos <= 0.44) and (price < float(c1['Low']) or body_ratio >= 0.45)
            if ema_bear_aligned and pulled_to_ema and bear_reject:
                sl_price = max(high_0, float(c1['High'])) + (atr_5m * 0.25)
                return "SCALP SELL (SHORT) 🔴", "SELL", "M5 Setup 1: EMA 9/20 Pullback Rejection", sl_price, macro_label

            # SETUP 2 (HIGH ACCURACY): 6-Bar High Sweep & Stop-Hunt Breakdown
            swept_high = (high_0 > micro_high and price < micro_high) or (float(c1['High']) > micro_high and price < float(c1['Low']) and price < micro_high)
            collapsed  = (price < open_0) and (close_pos <= 0.42) and (u_wick >= float(c0['Range']) * 0.25 or disp_power >= 0.85)
            if swept_high and collapsed:
                sl_price = max(high_0, float(c1['High'])) + (atr_5m * 0.20)
                return "SCALP SELL (SHORT) 🔴", "SELL", "M5 Setup 2: Stop-Hunt High Sweep & Breakdown", sl_price, macro_label

            # SETUP 3 (HIGH ACCURACY): 4-Bar Micro-Box Displacement Breakdown
            if box_4_rng <= (atr_5m * 2.4) and price < box_4_lo and price < open_0 and ema9 <= ema20 and body_ratio >= 0.55 and close_pos <= 0.32 and disp_power >= 1.05:
                sl_price = min(high_0 + (atr_5m * 0.20), box_4_hi)
                return "SCALP SELL (SHORT) 🔴", "SELL", "M5 Setup 3: Micro-Box Displacement Breakdown", sl_price, macro_label

    return None, None, None, None, ""

def send_live_forex_summary():
    forex_open = is_forex_market_open()
    market_state = "🟢 ALL 9 PAIRS ACTIVE (M5 SCALPER)" if forex_open else "🟡 WEEKEND: BTC/USD 24/7 ACTIVE"
    _, session_name = get_scalping_session(True)
    dxy_bias, _ = get_fast_dxy_and_usdjpy(forex_open)

    report = (
        f"⚡ <b>M5 MULTI-PAIR SCALPER STATUS</b> ⚡\n\n"
        f"📢 <b>Status:</b> {market_state}\n"
        f"🕒 <b>Session:</b> {session_name} | <b>DXY:</b> {dxy_bias}\n\n"
    )

    for name, info in FOREX_PAIRS.items():
        try:
            ticker = yf.Ticker(info["symbol"])
            df_5m  = ticker.history(period="5d", interval="5m")
            if df_5m.empty or len(df_5m) < 30:
                continue

            df_5m = calculate_scalp_metrics(df_5m)
            c = df_5m.iloc[-1]
            price = float(c['Close'])
            ema9  = float(c['EMA9'])
            ema20 = float(c['EMA20'])
            rsi   = float(c['RSI'])
            state = "BULLISH (EMA9>20)" if ema9 > ema20 else "BEARISH (EMA9<20)"
            p_str = format_price(price, info["decimals"])

            report += f"🔹 <b>{name}:</b> <code>{p_str}</code> | {state} | RSI: {rsi:.0f}\n"
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

def manage_active_scalp_trades(pair_name, candle_time, df_1m, latest_live_5m, closed_5m, decimals):
    global daily_realized_pnl, total_account_pnl, pair_cooldown_until, sl_direction_lock

    ema20 = float(closed_5m['EMA20'])
    ema50 = float(closed_5m['EMA50'])
    now_utc = get_utc_now()

    if df_1m is not None and not df_1m.empty:
        price = float(df_1m.iloc[-1]['Close'])
        now_time = to_utc_timestamp(df_1m.index[-1])
    else:
        price = float(latest_live_5m['Close'])
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

            # 1. 0.8R पर तुरंत Breakeven और 50% प्रॉफिट लॉक
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
                    half_prof                  = round(trade["max_risk"] * 0.5, 0)
                    trade["status"]            = "0.8R LOCKED"
                    send_telegram_message(
                        f"🛡️ <b>SCALP 0.8R HIT — MOVE SL TO ENTRY</b> 🛡️\n\n"
                        f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                        f"📍 <b>Entry:</b> {entry_str}  |  <b>Live:</b> {price_str}\n"
                        f"📢 <b>Action:</b> 50% लॉट बुक करें (<b>+${half_prof:.0f}</b>) और बचे हुए ट्रेड का SL <b>Entry ({entry_str})</b> पर लगा दें।"
                    )
                    continue

            # 2. FULL TARGET या SL चेक करें
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
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=10)
                net_win = trade["expected_profit"]
                daily_realized_pnl += net_win
                total_account_pnl += net_win
                trade["status"] = "TARGET HIT"
                send_telegram_message(
                    f"🎯 <b>SCALP TARGET HIT (+${net_win:.0f})</b> 🎯\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target:</b> {target_str}\n"
                    f"💰 <b>Profit:</b> <b>+${net_win:.0f}</b>"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=10)
                half_prof = round(trade["max_risk"] * 0.5, 0)
                daily_realized_pnl += half_prof
                total_account_pnl += half_prof
                trade["status"] = "BREAKEVEN EXIT"
                send_telegram_message(
                    f"🛡️ <b>SCALP BREAKEVEN EXIT (+${half_prof:.0f})</b> 🛡️\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                    f"📍 <b>Exit at Cost:</b> {entry_str}  |  <b>Locked Profit:</b> <b>+${half_prof:.0f}</b>"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=15)
                sl_direction_lock[pair_name][trade["direction"]] = now_utc + timedelta(minutes=30)

                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_account_pnl -= loss_amt
                trade["status"] = "SL HIT"
                send_telegram_message(
                    f"🛑 <b>SCALP STOP-LOSS HIT</b> 🛑\n\n"
                    f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>SL:</b> {sl_str}\n"
                    f"⚖️ <b>Risk:</b> -${loss_amt:.0f}"
                )
                continue

            # 3. 15 मिनट बाद अगर M5 ट्रेंड पूरी तरह उल्टा हो जाए तो Early Exit
            hold_minutes = (to_utc_timestamp(candle_time) - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= 15 and not trade["trailed_to_cost"]:
                closed_price = float(closed_5m['Close'])
                trend_shifted = False
                if trade["direction"] == "BUY" and closed_price < ema20 and closed_price < ema50 and closed_price < (trade["entry"] - trade["sl_dist"] * 0.55):
                    trend_shifted = True
                elif trade["direction"] == "SELL" and closed_price > ema20 and closed_price > ema50 and closed_price > (trade["entry"] + trade["sl_dist"] * 0.55):
                    trend_shifted = True

                if trend_shifted:
                    trade["is_open"] = False
                    pair_cooldown_until[pair_name] = now_utc + timedelta(minutes=15)
                    early_cut = round(trade["max_risk"] * 0.55, 0)
                    daily_realized_pnl -= early_cut
                    total_account_pnl -= early_cut
                    trade["status"] = "EARLY EXIT"
                    send_telegram_message(
                        f"🔄 <b>FAST SCALP EXIT (STRUCTURE SHIFT)</b> 🔄\n\n"
                        f"🌍 <b>Asset:</b> {trade['pair']} (<b>{trade['lot_size']} Lot</b>)\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Close Now:</b> {price_str}"
                    )
                    continue

def scan_scalping_market():
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

    forex_open = is_forex_market_open()
    dxy_bias, usdjpy_rate = get_fast_dxy_and_usdjpy(forex_open)

    for name, info in FOREX_PAIRS.items():
        if not forex_open and not info.get("is_crypto", False):
            continue

        try:
            ticker = yf.Ticker(info["symbol"])
            df_15m = ticker.history(period="5d", interval="15m")
            df_5m  = ticker.history(period="5d", interval="5m")
            df_1m  = ticker.history(period="1d", interval="1m")

            if df_5m.empty or len(df_5m) < 45 or df_15m.empty or len(df_15m) < 30:
                continue

            df_15m = calculate_scalp_metrics(df_15m)
            df_5m  = calculate_scalp_metrics(df_5m)

            latest_live_5m = df_5m.iloc[-1]
            closed_5m      = df_5m.iloc[-2]
            closed_time    = to_utc_timestamp(df_5m.index[-2])

            live_price     = float(df_1m.iloc[-1]['Close']) if (df_1m is not None and not df_1m.empty) else float(latest_live_5m['Close'])
            signal_close_p = float(closed_5m['Close'])
            exact_now_time = to_utc_timestamp(df_1m.index[-1]) if (df_1m is not None and not df_1m.empty) else get_utc_now()
            atr_5m         = float(closed_5m['ATR'])
            dec            = info["decimals"]

            manage_active_scalp_trades(name, closed_time, df_1m, latest_live_5m, closed_5m, dec)

            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            open_trades_count = sum(1 for t in daily_trades_log if t["is_open"])
            if open_trades_count >= MAX_CONCURRENT_TRADES:
                continue

            if last_candle_traded[name] == closed_time:
                continue

            if abs(live_price - signal_close_p) > (atr_5m * 0.90):
                continue

            signal, direction, strategy_used, custom_sl, macro_note = evaluate_m5_scalp_strategy(
                name, info, df_15m, df_5m, dxy_bias
            )

            if signal and custom_sl is not None:
                if direction == "BUY" and custom_sl >= live_price:
                    continue
                if direction == "SELL" and custom_sl <= live_price:
                    continue

                raw_sl_dist = abs(live_price - custom_sl)
                min_sl      = max(atr_5m * 0.85, info.get("min_sl_dist", atr_5m * 0.85))
                max_sl      = max(atr_5m * 1.85, info.get("max_sl_dist", atr_5m * 1.85))
                sl_dist     = round(max(min_sl, min(raw_sl_dist, max_sl)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                trades_today += 1

                rr_ratio    = SCALP_RR_RATIO
                target_dist = round(sl_dist * rr_ratio, dec)
                lock_dist   = round(sl_dist * 0.80, dec)

                if direction == "BUY":
                    sl_level          = round(live_price - sl_dist, dec)
                    half_target_level = round(live_price + lock_dist, dec)
                    target_level      = round(live_price + target_dist, dec)
                else:
                    sl_level          = round(live_price + sl_dist, dec)
                    half_target_level = round(live_price - lock_dist, dec)
                    target_level      = round(live_price - target_dist, dec)

                max_risk_usd  = SCALP_RISK_USD
                expected_prof = round(max_risk_usd * rr_ratio, 0)
                exact_lot     = calculate_scalp_lot_size(name, info, live_price, sl_dist, max_risk_usd, usdjpy_rate)

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

                exec_note = ""
                if "GOLD" in name:
                    exec_note = f"\n📌 <b>Note:</b> MT5 Spot Price पर एंट्री लें ({sl_pips_str} SL | {target_pips_str} Target)।"

                msg = (
                    f"⚡ <b>M5 PRO SCALPING SIGNAL</b> ⚡\n\n"
                    f"🌍 <b>Asset:</b> <b>{name}</b> (<code>{info['mt5_name']}</code>)\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n"
                    f"📊 <b>Timeframe:</b> M5 Trigger + M15 Flow ({macro_note})\n"
                    f"🧠 <b>Setup:</b> {strategy_used}\n\n"
                    f"📍 <b>Entry Price:</b> <code>{entry_s}</code>\n"
                    f"🛑 <b>Tight SL:</b> <code>{sl_s}</code>  <i>({sl_pips_str})</i>\n"
                    f"🛡️ <b>0.8R Safe Lock:</b> <code>{half_s}</code>\n"
                    f"🎯 <b>Scalp Target (1:1.6):</b> <code>{target_s}</code>  <i>({target_pips_str})</i>\n\n"
                    f"🔥 <b>MT5 Lot Size:</b> <b><code>{exact_lot:.2f} Lot</code></b>\n"
                    f"💰 <b>Risk:</b> ${max_risk_usd:.0f}  ➡️  <b>Reward:</b> <b>+${expected_prof:.0f}</b>"
                    f"{exec_note}"
                )

                chart_buf = generate_trade_position_chart(
                    df_5m, name, direction, live_price, sl_level, target_level,
                    half_target_level, dec, exact_lot, max_risk_usd, expected_prof, rr_ratio
                )
                send_telegram_signal_with_chart(msg, chart_buf)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    send_telegram_message(
        "⚡ <b>M5 High-Accuracy Scalping Engine Online</b> ⚡\n\n"
        "• <b>Timeframe:</b> 5-Minute (M5) & 1-Minute (M1) Live Scalping\n"
        "• <b>Pairs (9):</b> XAUUSD, EURUSD, GBPUSD, USDJPY, GBPJPY, EURJPY, AUDUSD, USDCAD, BTCUSD\n"
        "• <b>Active Setups:</b> EMA 9/20 Pullback, Stop-Hunt Sweep & Displacement Box Breakout"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 20:
            scan_scalping_market()
            last_scan_time = time.time()
        time.sleep(2)
