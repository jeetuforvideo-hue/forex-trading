import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# =====================================================================
# 1. FUNDING PIPS $100,000 — PROPRIETARY INSTITUTIONAL EDITION
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "5610520408"

# $100,000 Prop-Firm Account Parameters
TOTAL_CAPITAL_USD     = 100000.0  # $1,00,000 Account Capital
MAX_DAILY_DRAWDOWN    = 5000.0    # $5,000 (5%) Daily Drawdown Limit
MAX_TOTAL_DRAWDOWN    = 10000.0   # $10,000 (10%) Maximum Drawdown Limit

# Standard Institutional Risk Allocation (0.40% per trade = $400)
RISK_PER_TRADE_USD    = 400.0     # प्रति ट्रेड $400 (0.40%) का संतुलित प्रोफेशनल रिस्क
DAILY_PROTECTION_CAP  = 1600.0    # दैनिक सुरक्षा कैप ($5,000 लिमिट से बहुत नीचे)
MAX_TRADES_PER_DAY    = 4         # दिन भर में अधिकतम 4 सबसे उच्च गुणवत्ता वाले ट्रेड्स
MAX_HOLD_MINUTES      = 180       # अधिकतम 3 घंटे (180 मिनट) होल्ड टाइम

# Intermarket US Dollar Index Symbol
DXY_SYMBOL = "DX-Y.NYB"

# फॉरेक्स के 5 सबसे लिक्विड पेयर्स (Gold और EUR/USD सबसे ऊपर)
FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "symbol": "GC=F",
        "mt5_name": "XAUUSD",
        "decimals": 2,
        "unit_name": "Points",
        "dxy_mode": "INVERSE"
    },
    "EUR/USD": {
        "symbol": "EURUSD=X",
        "mt5_name": "EURUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "dxy_mode": "INVERSE"
    },
    "GBP/USD": {
        "symbol": "GBPUSD=X",
        "mt5_name": "GBPUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "dxy_mode": "INVERSE"
    },
    "USD/JPY": {
        "symbol": "JPY=X",
        "mt5_name": "USDJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "dxy_mode": "DIRECT"
    },
    "GBP/JPY": {
        "symbol": "GBPJPY=X",
        "mt5_name": "GBPJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "dxy_mode": "CROSS"
    }
}

last_candle_traded = {name: None for name in FOREX_PAIRS}
trades_today = 0
last_update_id = 0
daily_trades_log = []
daily_realized_pnl = 0.0
total_account_pnl = 0.0

def get_ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

current_day = get_ist_now().date()

def is_forex_market_open():
    """फॉरेक्स मार्केट सोमवार सुबह 3:30 AM IST से शनिवार सुबह 3:30 AM IST तक खुला रहता है"""
    now = get_ist_now()
    wd = now.weekday()
    if wd == 6:
        return False
    if wd == 5 and now.hour >= 4:
        return False
    return True

def get_institutional_session():
    """लिक्विडिटी और सेशन टाइमिंग फिल्टर (रोलओवर और डेड ज़ोन में एंट्री बंद)"""
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)
    if 3.0 <= hr <= 5.5:
        return False, "Rollover Spread Zone (No Execution)"
    elif 5.5 < hr < 12.0:
        return True, "Asian Institutional Session"
    elif 12.0 <= hr < 18.0:
        return True, "London Killzone Session"
    elif 18.0 <= hr < 22.5:
        return True, "London + New York Overlap (Prime Liquidity)"
    else:
        return True, "New York Institutional Session"

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
    if "GOLD" in pair_name:
        return f"${price_diff:.2f} ({price_diff * 10:.0f} Points)"
    elif decimals == 3:
        return f"{price_diff * 100:.1f} Pips"
    else:
        return f"{price_diff * 10000:.1f} Pips"

def calculate_exact_mt5_lot_size(pair_name, entry_price, sl_dist, risk_usd):
    """$100,000 अकाउंट और स्टॉप-लॉस की दूरी के आधार पर सटीक MT5 लॉट साइज"""
    if sl_dist <= 0:
        return 0.10
    if "GOLD" in pair_name:
        loss_per_1_lot = sl_dist * 100.0
    elif pair_name.endswith("JPY"):
        loss_per_1_lot = (sl_dist * 100000.0) / max(entry_price, 100.0)
    else:
        pips = sl_dist * 10000.0
        loss_per_1_lot = pips * 10.0

    if loss_per_1_lot <= 0:
        return 0.10
    raw_lot = risk_usd / loss_per_1_lot
    return max(0.05, min(round(raw_lot, 2), 2.00))

def get_account_scoreboard():
    current_equity = TOTAL_CAPITAL_USD + total_account_pnl
    daily_buffer = MAX_DAILY_DRAWDOWN + min(0.0, daily_realized_pnl)
    total_buffer = MAX_TOTAL_DRAWDOWN + min(0.0, total_account_pnl)

    board = (
        f"🏦 <b>$100,000 ACCOUNT & RISK OVERVIEW:</b>\n"
        f"• <b>Account Equity:</b> ${current_equity:,.0f} (Today P&L: <b>${daily_realized_pnl:+,.0f}</b>)\n"
        f"• <b>Daily Drawdown Buffer:</b> ${daily_buffer:,.0f} / $5,000\n"
        f"• <b>Total Drawdown Buffer:</b> ${total_buffer:,.0f} / $10,000\n\n"
    )
    if not daily_trades_log:
        board += "📋 <b>आज के ट्रेड्स का स्टेटस:</b> अभी कोई ट्रेड नहीं लिया गया है।"
        return board

    board += "📋 <b>आज के ट्रेड्स का लाइव स्कोरकार्ड:</b>\n"
    for t in daily_trades_log:
        board += f"👉 <b>Trade #{t['id']} ({t['pair']} - {t['direction']} | {t['lot_size']} Lot):</b>\n   {t['status']}\n"
    return board.strip()

# =====================================================================
# 2. QUANTITATIVE ORDER-FLOW & DXY INTERMARKET ENGINE
# =====================================================================
def calculate_proprietary_metrics(df):
    """
    प्रोफेशनल क्वांटिटेटिव मेट्रिक्स:
    • Kaufman Efficiency Ratio (ER): ट्रेंड की शुद्धता (Smoothness vs Chop) नापने के लिए।
    • Institutional Displacement Factor: कैंडल की ताकत को पिछले 20 कैंडल्स के औसत से तौलने के लिए।
    • Dynamic Equilibrium & Liquidity Pools: प्रीमियम और डिस्काउंट वैल्यू ज़ोन।
    """
    df = df.copy()
    df['EMA20']  = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50']  = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

    hl = df['High'] - df['Low']
    hc = np.abs(df['High'] - df['Close'].shift())
    lc = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    # Kaufman Efficiency Ratio (10-Period)
    direction_move = np.abs(df['Close'] - df['Close'].shift(10))
    volatility_sum = np.abs(df['Close'] - df['Close'].shift(1)).rolling(10).sum().replace(0, 1e-9)
    df['ER'] = (direction_move / volatility_sum).fillna(0.0)

    # Candle Micro-Structure
    df['Body']       = np.abs(df['Close'] - df['Open'])
    df['Range']      = (df['High'] - df['Low']).replace(0, 1e-9)
    df['Body_Ratio'] = df['Body'] / df['Range']
    df['Upper_Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Close_Pos']  = (df['Close'] - df['Low']) / df['Range']

    # Institutional Displacement Ratio
    avg_body = df['Body'].rolling(20).mean().replace(0, 1e-9)
    df['Disp_Power'] = df['Body'] / avg_body

    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50.0)

    # 32-Candle Dealing Range (8 Hours on 15M)
    df['Deal_High'] = df['High'].rolling(32).max()
    df['Deal_Low']  = df['Low'].rolling(32).min()
    df['Equilibrium'] = (df['Deal_High'] + df['Deal_Low']) / 2.0

    return df

def get_dxy_intermarket_bias():
    """US Dollar Index (DXY) की लाइव दिशा की जांच करता है"""
    try:
        dxy = yf.Ticker(DXY_SYMBOL)
        df_dxy = dxy.history(period="5d", interval="15m")
        if df_dxy.empty or len(df_dxy) < 40:
            return "NEUTRAL"

        df_dxy = calculate_proprietary_metrics(df_dxy)
        c = df_dxy.iloc[-2]
        close_p = float(c['Close'])
        ema20   = float(c['EMA20'])
        ema50   = float(c['EMA50'])
        slope_3 = close_p - float(df_dxy.iloc[-5]['Close'])

        if close_p > ema20 and ema20 > ema50 and slope_3 > 0:
            return "BULLISH_DOLLAR"
        elif close_p < ema20 and ema20 < ema50 and slope_3 < 0:
            return "BEARISH_DOLLAR"
        return "NEUTRAL"
    except Exception:
        return "NEUTRAL"

def check_dxy_permission(dxy_mode, trade_dir, dxy_bias):
    if dxy_mode == "INVERSE":
        if trade_dir == "BUY" and dxy_bias == "BULLISH_DOLLAR":
            return False
        if trade_dir == "SELL" and dxy_bias == "BEARISH_DOLLAR":
            return False
    elif dxy_mode == "DIRECT":
        if trade_dir == "BUY" and dxy_bias == "BEARISH_DOLLAR":
            return False
        if trade_dir == "SELL" and dxy_bias == "BULLISH_DOLLAR":
            return False
    return True

def analyze_htf_1h_institutional_flow(df_1h):
    """1-Hour चार्ट पर मुख्य इंस्टीट्यूशनल ऑर्डर-फ्लो और मेजर लिक्विडिटी लेवल्स"""
    c1 = df_1h.iloc[-2]
    c2 = df_1h.iloc[-3]

    close_1h  = float(c1['Close'])
    ema20_1h  = float(c1['EMA20'])
    ema50_1h  = float(c1['EMA50'])
    ema200_1h = float(c1['EMA200'])

    recent_16 = df_1h.iloc[-18:-2]
    major_res = float(recent_16['High'].max())
    major_sup = float(recent_16['Low'].min())

    bull_points = 0
    bear_points = 0

    if close_1h > ema20_1h:
        bull_points += 1
    else:
        bear_points += 1

    if ema20_1h > ema50_1h:
        bull_points += 1
    else:
        bear_points += 1

    if close_1h > ema200_1h:
        bull_points += 1
    else:
        bear_points += 1

    if float(c1['High']) >= float(c2['High']) and float(c1['Low']) >= float(c2['Low']):
        bull_points += 1
    elif float(c1['High']) <= float(c2['High']) and float(c1['Low']) <= float(c2['Low']):
        bear_points += 1

    if bull_points >= 3:
        bias = "BULLISH"
    elif bear_points >= 3:
        bias = "BEARISH"
    else:
        bias = "NEUTRAL"

    return {
        "bias": bias,
        "major_res": major_res,
        "major_sup": major_sup
    }

# =====================================================================
# 3. UNIQUE PROPRIETARY "VLIA" STRATEGY ENGINE (1H + 15M + 5M)
# =====================================================================
def evaluate_vlia_institutional_strategy(pair_name, pair_info, df_1h, df_15m, df_5m, dxy_bias):
    """
    Vortex Liquidity & Institutional Absorption (VLIA) Engine:
    • Model 1: Liquidity Pool Sweep + 2-Candle Institutional Absorption + 5M Micro Shift
    • Model 2: Unmitigated Institutional Imbalance (FVG + Order Block) + Value Zone Tap
    • Model 3: High-Efficiency (ER >= 0.42) Institutional Vacuum Expansion
    """
    session_ok, _ = get_institutional_session()
    if not session_ok:
        return None, None, None, None

    htf = analyze_htf_1h_institutional_flow(df_1h)
    htf_bias = htf["bias"]
    if htf_bias == "NEUTRAL":
        return None, None, None, None

    c0 = df_15m.iloc[-2]  # आखिरी पूरी तरह बंद हुई 15M कैंडल
    c1 = df_15m.iloc[-3]  # पिछली 15M कैंडल
    c2 = df_15m.iloc[-4]  # तीसरी पिछली 15M कैंडल

    price      = float(c0['Close'])
    open_0     = float(c0['Open'])
    high_0     = float(c0['High'])
    low_0      = float(c0['Low'])
    atr        = float(c0['ATR'])
    er         = float(c0['ER'])
    disp_power = float(c0['Disp_Power'])
    close_pos  = float(c0['Close_Pos'])
    body_ratio = float(c0['Body_Ratio'])
    u_wick     = float(c0['Upper_Wick'])
    l_wick     = float(c0['Lower_Wick'])
    ema20      = float(c0['EMA20'])
    ema50      = float(c0['EMA50'])
    rsi        = float(c0['RSI'])
    eq_level   = float(c0['Equilibrium'])

    if np.isnan(atr) or atr <= 0:
        return None, None, None, None

    # Exhaustion & Dead-Candle Filter
    if float(c0['Range']) > (atr * 2.4) or float(c0['Range']) < (atr * 0.45):
        return None, None, None, None

    # 5-Minute Micro-Structure Alignment Check
    m5_bull_aligned = True
    m5_bear_aligned = True
    if df_5m is not None and len(df_5m) >= 25:
        df_5m_m = calculate_proprietary_metrics(df_5m)
        m5_c = df_5m_m.iloc[-2]
        m5_p = df_5m_m.iloc[-3]
        m5_bull_aligned = (float(m5_c['Close']) > float(m5_c['EMA20'])) and (float(m5_c['Close']) >= float(m5_p['Low']))
        m5_bear_aligned = (float(m5_c['Close']) < float(m5_c['EMA20'])) and (float(m5_c['Close']) <= float(m5_p['High']))

    # पिछले 3 घंटे (12 कैंडल्स) का एक्सटर्नल लिक्विडिटी पूल
    pool_12   = df_15m.iloc[-14:-3]
    pool_high = float(pool_12['High'].max())
    pool_low  = float(pool_12['Low'].min())

    # =========================================================================
    # BULLISH INSTITUTIONAL MODELS (1H Bullish + DXY Permitted + 5M Confirmed)
    # =========================================================================
    if htf_bias == "BULLISH" and m5_bull_aligned and check_dxy_permission(pair_info["dxy_mode"], "BUY", dxy_bias):
        room_to_res = (htf["major_res"] - price) >= (atr * 1.3) or (price > htf["major_res"])

        if room_to_res and 42 <= rsi <= 68:
            # MODEL 1: Sell-Side Liquidity Sweep & Institutional Absorption
            swept_liquidity = (low_0 < pool_low and price > pool_low) or (float(c1['Low']) < pool_low and price > pool_low and price > float(c1['High']))
            in_discount_zone = (price <= eq_level * 1.002) or (low_0 <= ema50)
            strong_absorption = (l_wick >= float(c0['Range']) * 0.38) or (disp_power >= 1.15 and price > float(c1['High']))

            if swept_liquidity and in_discount_zone and strong_absorption and price > open_0 and close_pos >= 0.65:
                sl_price = min(low_0, float(c1['Low'])) - (atr * 0.30)
                return "BUY (LONG) 🟢", "BUY", "VLIA Model 1: Liquidity Sweep & Institutional Absorption", sl_price

            # MODEL 2: Unmitigated Institutional Imbalance (Order Block + FVG Tap)
            for i in range(3, 13):
                b1 = df_15m.iloc[-i-2]
                b2 = df_15m.iloc[-i-1]
                b3 = df_15m.iloc[-i]
                if float(b3['Low']) > float(b1['High']) and float(b2['Close']) > float(b2['Open']) and float(b2['Disp_Power']) >= 1.25:
                    fvg_top = float(b3['Low'])
                    ob_base = float(b1['Low'])
                    inter = df_15m.iloc[-i+1:-2]
                    mitigated = (len(inter) > 0) and (float(inter['Low'].min()) < ob_base)
                    if not mitigated and low_0 <= (fvg_top + atr * 0.08) and low_0 >= (ob_base - atr * 0.15):
                        if price > open_0 and close_pos >= 0.65 and price > ema20 and er >= 0.28:
                            sl_price = min(low_0, ob_base) - (atr * 0.30)
                            return "BUY (LONG) 🟢", "BUY", "VLIA Model 2: Unmitigated Institutional Imbalance Tap", sl_price

            # MODEL 3: High-Efficiency Vacuum Expansion (Clean Trend Continuation)
            retested_dynamic_value = (low_0 <= ema20 * 1.0008) or (float(c1['Low']) <= ema20 * 1.0008)
            displacement_break = (price > open_0) and (price > float(c1['High'])) and (close_pos >= 0.70) and (body_ratio >= 0.60)
            if retested_dynamic_value and displacement_break and ema20 > ema50 and er >= 0.40 and disp_power >= 1.15:
                sl_price = min(low_0, float(c1['Low'])) - (atr * 0.30)
                return "BUY (LONG) 🟢", "BUY", f"VLIA Model 3: High-Efficiency Order-Flow Expansion (ER: {er:.2f})", sl_price

    # =========================================================================
    # BEARISH INSTITUTIONAL MODELS (1H Bearish + DXY Permitted + 5M Confirmed)
    # =========================================================================
    if htf_bias == "BEARISH" and m5_bear_aligned and check_dxy_permission(pair_info["dxy_mode"], "SELL", dxy_bias):
        room_to_sup = (price - htf["major_sup"]) >= (atr * 1.3) or (price < htf["major_sup"])

        if room_to_sup and 32 <= rsi <= 58:
            # MODEL 1: Buy-Side Liquidity Sweep & Institutional Supply Absorption
            swept_liquidity = (high_0 > pool_high and price < pool_high) or (float(c1['High']) > pool_high and price < pool_high and price < float(c1['Low']))
            in_premium_zone = (price >= eq_level * 0.998) or (high_0 >= ema50)
            strong_absorption = (u_wick >= float(c0['Range']) * 0.38) or (disp_power >= 1.15 and price < float(c1['Low']))

            if swept_liquidity and in_premium_zone and strong_absorption and price < open_0 and close_pos <= 0.35:
                sl_price = max(high_0, float(c1['High'])) + (atr * 0.30)
                return "SELL (SHORT) 🔴", "SELL", "VLIA Model 1: Buy-Side Sweep & Institutional Supply Absorption", sl_price

            # MODEL 2: Unmitigated Bearish Imbalance (Supply Order Block + FVG Tap)
            for i in range(3, 13):
                b1 = df_15m.iloc[-i-2]
                b2 = df_15m.iloc[-i-1]
                b3 = df_15m.iloc[-i]
                if float(b3['High']) < float(b1['Low']) and float(b2['Close']) < float(b2['Open']) and float(b2['Disp_Power']) >= 1.25:
                    fvg_bottom = float(b3['High'])
                    ob_top     = float(b1['High'])
                    inter = df_15m.iloc[-i+1:-2]
                    mitigated = (len(inter) > 0) and (float(inter['High'].max()) > ob_top)
                    if not mitigated and high_0 >= (fvg_bottom - atr * 0.08) and high_0 <= (ob_top + atr * 0.15):
                        if price < open_0 and close_pos <= 0.35 and price < ema20 and er >= 0.28:
                            sl_price = max(high_0, ob_top) + (atr * 0.30)
                            return "SELL (SHORT) 🔴", "SELL", "VLIA Model 2: Unmitigated Bearish Supply Imbalance Tap", sl_price

            # MODEL 3: High-Efficiency Bearish Vacuum Expansion
            retested_dynamic_value = (high_0 >= ema20 * 0.9992) or (float(c1['High']) >= ema20 * 0.9992)
            displacement_break = (price < open_0) and (price < float(c1['Low'])) and (close_pos <= 0.30) and (body_ratio >= 0.60)
            if retested_dynamic_value and displacement_break and ema20 < ema50 and er >= 0.40 and disp_power >= 1.15:
                sl_price = max(high_0, float(c1['High'])) + (atr * 0.30)
                return "SELL (SHORT) 🔴", "SELL", f"VLIA Model 3: High-Efficiency Bearish Expansion (ER: {er:.2f})", sl_price

    return None, None, None, None

def send_live_forex_summary():
    market_state = "🟢 ग्लोबल फॉरेक्स मार्केट खुला है (VLIA INSTITUTIONAL MODE)" if is_forex_market_open() else "🔴 वीकेंड: मार्केट बंद है"
    _, session_name = get_institutional_session()
    dxy_bias = get_dxy_intermarket_bias()

    report = (
        f"🏦 <b>INSTITUTIONAL MULTI-TIMEFRAME REPORT</b> 🏦\n\n"
        f"📢 <b>Status:</b> {market_state}\n"
        f"🕒 <b>Active Session:</b> {session_name}\n"
        f"💵 <b>US Dollar Index (DXY):</b> <b>{dxy_bias}</b>\n\n"
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
            htf    = analyze_htf_1h_institutional_flow(df_1h)

            latest = df_15m.iloc[-1]
            price  = float(latest['Close'])
            er     = float(latest['ER'])
            rsi    = float(latest['RSI'])
            p_str  = format_price(price, info["decimals"])

            report += (
                f"🔹 <b>{name}:</b> {p_str}\n"
                f"   • 1H Flow: <b>{htf['bias']}</b> | Efficiency: {er:.2f} | RSI: {rsi:.0f}\n\n"
            )
        except Exception:
            report += f"🔹 <b>{name}:</b> डेटा लोड हो रहा है...\n\n"

    report += f"{get_account_scoreboard()}"
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
    """1-Minute लाइव टिक ट्रैकर (1:1 Breakeven + Full Target + Early Structure Protection)"""
    global daily_realized_pnl, total_account_pnl

    ema20 = float(closed_15m['EMA20'])
    ema50 = float(closed_15m['EMA50'])

    if df_1m is not None and not df_1m.empty:
        price = float(df_1m.iloc[-1]['Close'])
        now_time = df_1m.index[-1]
    else:
        price = float(latest_live_15m['Close'])
        now_time = candle_time

    for trade in daily_trades_log:
        if trade["pair"] == pair_name and trade["is_open"]:
            if candle_time < trade["entry_candle_time"]:
                continue

            entry_str  = format_price(trade["entry"], decimals)
            target_str = format_price(trade["target"], decimals)
            sl_str     = format_price(trade["sl"], decimals)
            price_str  = format_price(price, decimals)

            check_since = trade.get("be_activated_time", trade["entry_exact_time"])

            if df_1m is not None and not df_1m.empty:
                recent_ticks = df_1m[df_1m.index > check_since]
                if not recent_ticks.empty:
                    current_high = float(recent_ticks['High'].max())
                    current_low  = float(recent_ticks['Low'].min())
                else:
                    current_high = price
                    current_low  = price
            else:
                current_high = price
                current_low  = price

            # 1. 1:1 टारगेट पर स्टॉप-लॉस कॉस्ट (Breakeven) पर और 50% प्रॉफिट बुक
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
                    trade["status"]            = f"🔥 1:1 HIT (+${half_prof:.0f} Secured | SL at Cost {entry_str})"
                    send_telegram_message(
                        f"🛡️ <b>INSTITUTIONAL UPDATE (Trade #{trade['id']}) : MOVE SL TO BREAKEVEN!</b> 🛡️\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n"
                        f"✅ <b>1:1 टारगेट हिट हो चुका है! (Current Price: {price_str})</b>\n\n"
                        f"📢 <b>एक्शन:</b> अपना 50% लॉट बुक कर लें (<b>+${half_prof:.0f} सुरक्षित प्रॉफिट</b>) और बचे हुए ट्रेड का SL <b>Entry Price ({entry_str})</b> पर लगा दें।"
                    )
                    continue

            # 2. FULL TARGET या STOP-LOSS चेक करें
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
                net_win = trade["expected_profit"]
                daily_realized_pnl += net_win
                total_account_pnl += net_win
                trade["status"] = f"✅ FULL TARGET HIT (+${net_win:.0f} प्रॉफिट)"
                send_telegram_message(
                    f"🎯 <b>INSTITUTIONAL TRADE #{trade['id']} : FULL TARGET (1:2.4) HIT!</b> 🎯\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['mt5_name']} | {trade['lot_size']} Lot)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target Hit:</b> {target_str}\n\n"
                    f"💰 <b>Final Profit:</b> <b>✅ +${net_win:.0f}</b>\n\n"
                    f"────────────────────\n\n{get_account_scoreboard()}"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                half_prof = trade["max_risk"] * 0.5
                daily_realized_pnl += half_prof
                total_account_pnl += half_prof
                trade["status"] = f"🛡️ BREAKEVEN EXIT (+${half_prof:.0f} Profit)"
                send_telegram_message(
                    f"🛡️ <b>INSTITUTIONAL TRADE #{trade['id']} : BREAKEVEN SL HIT!</b> 🛡️\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n"
                    f"📢 <b>रिजल्ट:</b> 1:1 के बाद मार्केट वापस कॉस्ट ({entry_str}) पर आया। 50% पार्टियल बुकिंग से <b>+${half_prof:.0f} प्रॉफिट</b> सुरक्षित रहा और बचे हुए हिस्से पर $0 नुकसान हुआ।\n\n"
                    f"────────────────────\n\n{get_account_scoreboard()}"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_account_pnl -= loss_amt
                trade["status"] = f"🛑 SL HIT (-${loss_amt:.0f})"
                send_telegram_message(
                    f"🛑 <b>INSTITUTIONAL TRADE #{trade['id']} : STOP-LOSS HIT!</b> 🛑\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>SL Hit:</b> {sl_str}\n"
                    f"📊 <b>Risk Outcome:</b> <b>-${loss_amt:.0f} (0.40% Standard Risk)</b>\n\n"
                    f"────────────────────\n\n{get_account_scoreboard()}"
                )
                continue

            # 3. 15M Structure Invalidation Exit
            hold_minutes = (candle_time - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= 45 and not trade["trailed_to_cost"]:
                closed_price = float(closed_15m['Close'])
                trend_shifted = False
                if trade["direction"] == "BUY" and closed_price < ema20 and closed_price < ema50 and closed_price < (trade["entry"] - trade["sl_dist"] * 0.60):
                    trend_shifted = True
                elif trade["direction"] == "SELL" and closed_price > ema20 and closed_price > ema50 and closed_price > (trade["entry"] + trade["sl_dist"] * 0.60):
                    trend_shifted = True

                if trend_shifted:
                    trade["is_open"] = False
                    early_cut = round(trade["max_risk"] * 0.60, 0)
                    daily_realized_pnl -= early_cut
                    total_account_pnl -= early_cut
                    trade["status"] = f"🔄 STRUCTURE SHIFT EXIT (-${early_cut:.0f})"
                    send_telegram_message(
                        f"🔄 <b>TRADE MANAGEMENT (Trade #{trade['id']}) : STRUCTURE SHIFT EXIT!</b> 🔄\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n"
                        f"📢 <b>अपडेट:</b> 15-मिनट चार्ट पर ऑर्डर-फ्लो विपरीत दिशा में क्लोज हुआ है। पूरा स्टॉप-लॉस देने के बजाय अभी एग्जिट कर लें।\n\n"
                        f"────────────────────\n\n{get_account_scoreboard()}"
                    )
                    continue

def scan_forex_market():
    global trades_today, current_day, last_candle_traded, daily_trades_log, daily_realized_pnl

    if not is_forex_market_open():
        return

    today = get_ist_now().date()
    if today != current_day:
        current_day = today
        trades_today = 0
        daily_trades_log = []
        daily_realized_pnl = 0.0
        last_candle_traded = {name: None for name in FOREX_PAIRS}

    if daily_realized_pnl <= -DAILY_PROTECTION_CAP:
        return

    dxy_bias = get_dxy_intermarket_bias()

    for name, info in FOREX_PAIRS.items():
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
            closed_time     = df_15m.index[-2]

            price = float(df_1m.iloc[-1]['Close']) if (df_1m is not None and not df_1m.empty) else float(latest_live_15m['Close'])
            exact_now_time = df_1m.index[-1] if (df_1m is not None and not df_1m.empty) else closed_time
            atr   = float(closed_15m['ATR'])
            dec   = info["decimals"]

            manage_active_forex_trades(name, closed_time, df_1m, latest_live_15m, closed_15m, dec)

            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            if trades_today >= MAX_TRADES_PER_DAY:
                continue

            if last_candle_traded[name] == closed_time:
                continue

            signal, direction, strategy_used, custom_sl = evaluate_vlia_institutional_strategy(
                name, info, df_1h, df_15m, df_5m, dxy_bias
            )

            if signal and custom_sl is not None:
                raw_sl_dist = abs(price - custom_sl)
                min_sl_dist = atr * 1.05
                max_sl_dist = atr * 1.95
                sl_dist = round(max(min_sl_dist, min(raw_sl_dist, max_sl_dist)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                trades_today += 1

                rr_ratio = 2.4
                target_dist = round(sl_dist * rr_ratio, dec)

                if direction == "BUY":
                    sl_level          = round(price - sl_dist, dec)
                    half_target_level = round(price + sl_dist, dec)
                    target_level      = round(price + target_dist, dec)
                else:
                    sl_level          = round(price + sl_dist, dec)
                    half_target_level = round(price - sl_dist, dec)
                    target_level      = round(price - target_dist, dec)

                max_risk_usd  = RISK_PER_TRADE_USD  # $400 (0.40% of $100,000)
                expected_prof = round(max_risk_usd * rr_ratio, 0)  # +$960 Profit
                exact_lot     = calculate_exact_mt5_lot_size(name, price, sl_dist, max_risk_usd)

                trade_id = trades_today
                daily_trades_log.append({
                    "id": trade_id,
                    "pair": name,
                    "mt5_name": info["mt5_name"],
                    "action": signal,
                    "direction": direction,
                    "strategy": strategy_used,
                    "lot_size": exact_lot,
                    "entry": price,
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
                    "status": "⏳ RUNNING (चल रहा है)"
                })

                entry_s  = format_price(price, dec)
                sl_s     = format_price(sl_level, dec)
                half_s   = format_price(half_target_level, dec)
                target_s = format_price(target_level, dec)

                sl_pips_str     = calculate_pips_or_points(name, sl_dist, dec)
                target_pips_str = calculate_pips_or_points(name, target_dist, dec)
                htf_info        = analyze_htf_1h_institutional_flow(df_1h)

                gold_note = ""
                if "GOLD" in name:
                    gold_note = (
                        f"📌 <b>XAUUSD Spot Execution Note:</b> MT5 के चालू Spot Price पर एंट्री लें "
                        f"और वहाँ से <b>{sl_pips_str}</b> का SL तथा <b>{target_pips_str}</b> का Target सेट करें।\n\n"
                    )

                msg = (
                    f"🏦 <b>INSTITUTIONAL ORDER-FLOW SIGNAL ({trades_today}/{MAX_TRADES_PER_DAY})</b> 🏦\n\n"
                    f"🌍 <b>Pair:</b> <b>{name}</b> (<code>{info['mt5_name']}</code>)\n"
                    f"📊 <b>Confluence:</b> 1H ({htf_info['bias']}) + 15M Confirmed + 5M Aligned | DXY: {dxy_bias}\n"
                    f"🧠 <b>Setup Model:</b> {strategy_used}\n\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n\n"
                    f"📍 <b>Entry Price:</b> {entry_s}\n"
                    f"🛑 <b>Protected SL:</b> {sl_s}  <i>({sl_pips_str} का गैप)</i>\n"
                    f"🛡️ <b>Breakeven (1:1):</b> {half_s}  <i>(+${max_risk_usd*0.5:.0f} पार्टियल लॉक)</i>\n"
                    f"🎯 <b>Full Target (1:2.4):</b> {target_s}  <i>({target_pips_str} का टारगेट)</i>\n\n"
                    f"💼 <b>MT5 Lot Size ($100k Account):</b> <b><code>{exact_lot:.2f} Lot</code></b>\n"
                    f"⚖️ <b>Risk Allocation:</b> <b>${max_risk_usd:.0f} (0.40%)</b>  ➡️  <b>Target Profit: +${expected_prof:.0f}</b>\n\n"
                    f"{gold_note}"
                    f"────────────────────\n\n"
                    f"{get_account_scoreboard()}"
                )
                send_telegram_message(msg)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    mkt_msg = "🟢 फॉरेक्स मार्केट खुला है (VLIA Institutional Engine चालू है)।" if is_forex_market_open() else "🔴 अभी वीकेंड की वजह से मार्केट बंद है।"

    send_telegram_message(
        "🏦 <b>Proprietary Institutional Forex Bot ($100,000 Edition) Active!</b> 🏦\n\n"
        f"📢 <b>Status:</b> {mkt_msg}\n\n"
        "⚙️ <b>Account & Risk Parameters:</b>\n"
        "• <b>Account Capital:</b> $100,000\n"
        "• <b>Standard Risk Per Trade:</b> $400 (0.40%) | Target: +$960 (1:2.4)\n"
        "• <b>Drawdown Rules Respected:</b> $5,000 Daily | $10,000 Max\n\n"
        "🧠 <b>VLIA 4-Layer Institutional Architecture:</b>\n"
        "1️⃣ Live DXY Intermarket Correlation Filter\n"
        "2️⃣ Kaufman Efficiency Ratio (Anti-Chop Filter)\n"
        "3️⃣ 1H Order-Flow + 15M Liquidity Sweep/Imbalance + 5M Micro Alignment\n"
        "4️⃣ Automated MT5 Lot Size & 1-Minute Precision Tracking"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 25:
            scan_forex_market()
            last_scan_time = time.time()
        time.sleep(3)
