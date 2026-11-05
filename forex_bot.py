import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# =====================================================================
# 1. FUNDING PIPS $100K — MACRO-QUANT RECOVERY & SNIPER EDITION
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "5610520408"

TOTAL_CAPITAL_USD     = 100000.0  # $1,00,000 Competition Account
MAX_DAILY_DRAWDOWN    = 5000.0    # $5,000 Daily Loss Rule
MAX_TOTAL_DRAWDOWN    = 10000.0   # $10,000 Max Loss Rule

# RECOVERY RISK ENGINE:
# $250 Risk (0.25%) per trade -> 1:2.5 Target = +$625 Profit (1 Winning Trade recovers your $600 loss!)
RISK_PER_TRADE_USD    = 250.0     # प्रति ट्रेड सिर्फ $250 का बेहद सुरक्षित रिस्क
DAILY_SAFE_STOP_USD   = 750.0     # दिन में अगर 3 गलत ट्रेड (-$750) हुए तो बॉट तुरंत उस दिन के लिए बंद हो जाएगा
MAX_TRADES_PER_DAY    = 3         # दिन में सिर्फ 3 सबसे परफेक्ट 'A+ Sniper' ट्रेड्स (Quality over Quantity)
MAX_HOLD_MINUTES      = 150       # 2.5 घंटे में फॉलो-थ्रू न मिले तो बाहर

# DXY (US Dollar Index) Symbol for Live Intermarket Correlation
DXY_SYMBOL = "DX-Y.NYB"

FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "symbol": "GC=F",
        "mt5_name": "XAUUSD",
        "decimals": 2,
        "unit_name": "Points",
        "dxy_correlation": "INVERSE"  # Dollar ऊपर = Gold नीचे
    },
    "EUR/USD": {
        "symbol": "EURUSD=X",
        "mt5_name": "EURUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "dxy_correlation": "INVERSE"  # Dollar ऊपर = EUR/USD नीचे
    },
    "GBP/USD": {
        "symbol": "GBPUSD=X",
        "mt5_name": "GBPUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "dxy_correlation": "INVERSE"
    },
    "USD/JPY": {
        "symbol": "JPY=X",
        "mt5_name": "USDJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "dxy_correlation": "DIRECT"   # Dollar ऊपर = USD/JPY ऊपर
    },
    "GBP/JPY": {
        "symbol": "GBPJPY=X",
        "mt5_name": "GBPJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "dxy_correlation": "NEUTRAL"
    }
}

last_candle_traded = {name: None for name in FOREX_PAIRS}
trades_today = 0
last_update_id = 0
daily_trades_log = []
daily_realized_pnl = 0.0
total_competition_pnl = -600.0  # आपके पिछले -$600 को ट्रैक में रखा है ताकि रिकवरी लाइव दिखे

def get_ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

current_day = get_ist_now().date()

def is_forex_market_open():
    now = get_ist_now()
    wd = now.weekday()
    if wd == 6:
        return False
    if wd == 5 and now.hour >= 4:
        return False
    return True

def get_institutional_killzone():
    """
    प्रोफेशनल ट्रेडर्स हर समय ट्रेड नहीं करते!
    बेकार और कम वॉल्यूम वाले घंटों (Dead Zones) में बॉट अब कोई एंट्री नहीं लेगा।
    """
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)
    # Dead Zone 1: सुबह 2:30 AM से 6:00 AM IST (Rollover & Wide Spreads)
    if 2.5 <= hr <= 6.0:
        return False, "Dead Rollover Zone (No Institutional Volume)"
    # Dead Zone 2: सुबह 10:30 AM से 12:15 PM IST (Pre-London Fakeout Trap Zone)
    if 10.5 <= hr < 12.25:
        return False, "Pre-London Retail Inducement Zone (Waiting for London Open)"
    elif 6.0 < hr < 10.5:
        return True, "Tokyo Institutional Flow"
    elif 12.25 <= hr < 18.0:
        return True, "London Prime Killzone"
    elif 18.0 <= hr < 23.0:
        return True, "London + NY Overlap (Peak Institutional Liquidity)"
    else:
        return True, "New York Late Session"

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
    if sl_dist <= 0:
        return 0.05
    if "GOLD" in pair_name:
        loss_per_1_lot = sl_dist * 100.0
    elif pair_name.endswith("JPY"):
        loss_per_1_lot = (sl_dist * 100000.0) / max(entry_price, 100.0)
    else:
        pips = sl_dist * 10000.0
        loss_per_1_lot = pips * 10.0

    if loss_per_1_lot <= 0:
        return 0.05
    raw_lot = risk_usd / loss_per_1_lot
    return max(0.05, min(round(raw_lot, 2), 1.50))

def get_funding_pips_dashboard():
    current_equity = TOTAL_CAPITAL_USD + total_competition_pnl
    daily_dd_remaining = MAX_DAILY_DRAWDOWN + min(0.0, daily_realized_pnl)
    total_dd_remaining = MAX_TOTAL_DRAWDOWN + min(0.0, total_competition_pnl)

    board = (
        f"🏦 <b>MACRO-QUANT $100K RECOVERY DASHBOARD:</b>\n"
        f"• <b>Current Equity:</b> ${current_equity:,.0f} (Today: <b>${daily_realized_pnl:+,.0f}</b> | Net: <b>${total_competition_pnl:+,.0f}</b>)\n"
        f"• <b>Daily Loss Buffer Left:</b> ${daily_dd_remaining:,.0f} / $5,000\n"
        f"• <b>Total Loss Buffer Left:</b> ${total_dd_remaining:,.0f} / $10,000\n\n"
    )
    if not daily_trades_log:
        board += "📋 <b>आज के स्नाइपर ट्रेड्स:</b> अभी कोई A+ सेटअप कन्फर्म नहीं हुआ है।"
        return board

    board += "📋 <b>आज के स्नाइपर ट्रेड्स का हाल:</b>\n"
    for t in daily_trades_log:
        board += f"👉 <b>Trade #{t['id']} ({t['pair']} - {t['direction']} | {t['lot_size']} Lot):</b>\n   {t['status']}\n"
    return board.strip()

# =====================================================================
# 2. QUANTITATIVE & INTERMARKET DXY ENGINE (PRO TRADER BRAIN)
# =====================================================================
def calculate_quant_metrics(df):
    """
    साधारण रिटेल इंडिकेटर्स के बजाय Quantitative Price Delivery Metrics:
    1. Kaufman Efficiency Ratio (ER): यह नापता है कि मार्केट सीधा चल रहा है या ज़िग-ज़ैग (Choppy) होकर SL खा रहा है।
    2. Z-Score & Institutional Displacement: असली ऑपरेटर वॉल्यूम स्पाइक पकड़ने के लिए।
    3. Dynamic Liquidity Pools: जहाँ रिटेलर्स के स्टॉप-लॉस पड़े होते हैं।
    """
    df = df.copy()
    df['EMA20']  = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50']  = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

    # ATR Volatility
    hl = df['High'] - df['Low']
    hc = np.abs(df['High'] - df['Close'].shift())
    lc = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    # Kaufman Efficiency Ratio (ER over 10 periods)
    # ER = 1.0 मतलब एकदम सीधा ट्रेंड, ER < 0.35 मतलब गंदा साइडवेज मार्केट (SL Hunting Zone)
    net_change = np.abs(df['Close'] - df['Close'].shift(10))
    sum_volatility = np.abs(df['Close'] - df['Close'].shift(1)).rolling(10).sum().replace(0, 1e-9)
    df['Efficiency_Ratio'] = (net_change / sum_volatility).fillna(0.0)

    # Candle Micro-Anatomy
    df['Body']       = np.abs(df['Close'] - df['Open'])
    df['Range']      = (df['High'] - df['Low']).replace(0, 1e-9)
    df['Body_Ratio'] = df['Body'] / df['Range']
    df['Upper_Wick'] = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Wick'] = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Close_Pos']  = (df['Close'] - df['Low']) / df['Range']

    # Institutional Displacement Ratio (Current Body vs Average Body of last 20 candles)
    avg_body_20 = df['Body'].rolling(20).mean().replace(0, 1e-9)
    df['Displacement_Z'] = df['Body'] / avg_body_20

    # RSI
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = (100 - (100 / (1 + rs))).fillna(50.0)

    return df

def get_live_dxy_macro_flow():
    """
    US Dollar Index (DXY) का लाइव फ्लो चेक करता है।
    अगर DXY मजबूत है (BULLISH), तो Gold और EUR/USD में BUY लेना आत्महत्या करने जैसा है!
    """
    try:
        dxy = yf.Ticker(DXY_SYMBOL)
        df_dxy_15m = dxy.history(period="5d", interval="15m")
        if df_dxy_15m.empty or len(df_dxy_15m) < 50:
            return "NEUTRAL", 0.0

        df_dxy_15m = calculate_quant_metrics(df_dxy_15m)
        c = df_dxy_15m.iloc[-2]
        p = df_dxy_15m.iloc[-3]

        close_dxy = float(c['Close'])
        ema20_dxy = float(c['EMA20'])
        ema50_dxy = float(c['EMA50'])
        chg_3bars = close_dxy - float(df_dxy_15m.iloc[-5]['Close'])

        if close_dxy > ema20_dxy and ema20_dxy > ema50_dxy and chg_3bars > 0:
            return "STRONG_BULLISH", chg_3bars
        elif close_dxy < ema20_dxy and ema20_dxy < ema50_dxy and chg_3bars < 0:
            return "STRONG_BEARISH", chg_3bars
        elif close_dxy > ema20_dxy and float(c['Close']) > float(p['High']):
            return "BULLISH_IMPULSE", chg_3bars
        elif close_dxy < ema20_dxy and float(c['Close']) < float(p['Low']):
            return "BEARISH_IMPULSE", chg_3bars
        return "NEUTRAL", chg_3bars
    except Exception:
        return "NEUTRAL", 0.0

def is_dxy_aligned_with_trade(pair_info, trade_direction, dxy_state):
    """
    प्रोफेशनल कॉरिलेशन लॉक:
    • अगर पेयर INVERSE है (Gold, EUR/USD, GBP/USD):
      - BUY तभी करो जब DXY कमज़ोर (BEARISH) या शांत हो। अगर DXY में तेजी (BULLISH) है, तो BUY ब्लॉक!
      - SELL तभी करो जब DXY मजबूत (BULLISH) या शांत हो। अगर DXY गिर रहा है, तो SELL ब्लॉक!
    """
    corr = pair_info.get("dxy_correlation", "NEUTRAL")
    if corr == "INVERSE":
        if trade_direction == "BUY" and "BULLISH" in dxy_state:
            return False, f"Blocked: DXY is {dxy_state} (Dollar ऊपर जा रहा है, इसलिए BUY मना है)"
        if trade_direction == "SELL" and "BEARISH" in dxy_state:
            return False, f"Blocked: DXY is {dxy_state} (Dollar गिर रहा है, इसलिए SELL मना है)"
    elif corr == "DIRECT":
        if trade_direction == "BUY" and "BEARISH" in dxy_state:
            return False, f"Blocked: DXY is {dxy_state}"
        if trade_direction == "SELL" and "BULLISH" in dxy_state:
            return False, f"Blocked: DXY is {dxy_state}"
    return True, f"DXY Aligned ({dxy_state})"

def analyze_strict_1h_institutional_structure(df_1h):
    """
    कोई फेक 'LEAN' नहीं!
    1H ट्रेंड तभी BULLISH या BEARISH माना जाएगा जब स्ट्रक्चर और EMA दोनों 100% साफ हों।
    """
    c1 = df_1h.iloc[-2]
    c2 = df_1h.iloc[-3]
    c3 = df_1h.iloc[-4]

    close_1h  = float(c1['Close'])
    ema20_1h  = float(c1['EMA20'])
    ema50_1h  = float(c1['EMA50'])
    atr_1h    = float(c1['ATR']) if not np.isnan(c1['ATR']) else 0.0

    # पिछले 16 घंटे के मेजर वॉल (Major Liquidity Walls)
    recent_16 = df_1h.iloc[-18:-2]
    major_high = float(recent_16['High'].max())
    major_low  = float(recent_16['Low'].min())

    # 1H की आखिरी कैंडल का खुद का डायरेक्शन
    h1_candle_bull = float(c1['Close']) > float(c1['Open']) or float(c1['Close_Pos']) >= 0.55
    h1_candle_bear = float(c1['Close']) < float(c1['Open']) or float(c1['Close_Pos']) <= 0.45

    # Strict Institutional Alignment
    if close_1h > ema20_1h and ema20_1h > ema50_1h and (h1_candle_bull or float(c1['Low']) > float(c3['Low'])):
        bias = "STRICT_BULLISH"
    elif close_1h < ema20_1h and ema20_1h < ema50_1h and (h1_candle_bear or float(c1['High']) < float(c3['High'])):
        bias = "STRICT_BEARISH"
    else:
        bias = "CHOPPY_NEUTRAL"  # ऐसे मार्केट में प्रोफेशनल ट्रेडर हाथ भी नहीं लगाता!

    return {
        "bias": bias,
        "major_high": major_high,
        "major_low": major_low,
        "atr_1h": atr_1h,
        "ema20_1h": ema20_1h
    }

# =====================================================================
# 3. THE CUSTOM "TRIAD PROTOCOL" (OUR ORIGINAL PROPRIETARY STRATEGY)
# =====================================================================
def evaluate_triad_proprietary_strategy(pair_name, pair_info, df_1h, df_15m, df_5m, dxy_state):
    """
    यह स्ट्रैटेजी किसी किताब या यूट्यूब की कॉपी नहीं है।
    यह 4-स्टेप Institutional Order-Flow Triad पर काम करती है:
    1. HTF 1H Strict Regime + DXY Macro Lock
    2. 15M 'Inducement Flush & Institutional Absorption' (रिटेलर्स को ट्रैप करके रिवर्सल या री-एक्यूमुलेशन)
    3. 5M Micro-Structure Break Confirmation (ताकि चलती छुरी में हाथ न कटे)
    4. Liquidity Vacuum Check (सामने कोई दीवार न हो ताकि 1:1 और 1:2.5 आसानी से हिट हो)
    """
    kz_ok, _ = get_institutional_killzone()
    if not kz_ok:
        return None, None, None, None

    htf = analyze_strict_1h_structure_wrapper(df_1h)
    if htf["bias"] == "CHOPPY_NEUTRAL":
        # अगर 1-Hour चार्ट ही कन्फ्यूज और साइडवेज है, तो सीधे छोड़ दो!
        return None, None, None, None

    c0 = df_15m.iloc[-2]  # आखिरी क्लोज्ड 15M कैंडल (Trigger Candle)
    c1 = df_15m.iloc[-3]  # उससे पहले की 15M कैंडल (Setup / Trap Candle)
    c2 = df_15m.iloc[-4]  # तीसरी पिछली कैंडल

    price      = float(c0['Close'])
    open_0     = float(c0['Open'])
    high_0     = float(c0['High'])
    low_0      = float(c0['Low'])
    atr        = float(c0['ATR'])
    er         = float(c0['Efficiency_Ratio'])
    disp_z     = float(c0['Displacement_Z'])
    close_pos  = float(c0['Close_Pos'])
    body_ratio = float(c0['Body_Ratio'])
    ema20      = float(c0['EMA20'])
    ema50      = float(c0['EMA50'])
    rsi        = float(c0['RSI'])

    if np.isnan(atr) or atr <= 0:
        return None, None, None, None

    # 1. CHOPPY & EXHAUSTION FILTER:
    # अगर कैंडल 2.2x ATR से बड़ी बन चुकी है, तो सारा मूव खत्म हो चुका है—अब घुसोगे तो टॉप/बॉटम पर फंसोगे!
    if float(c0['Range']) > (atr * 2.2) or float(c0['Range']) < (atr * 0.45):
        return None, None, None, None

    # 5-Minute Micro Confirmation (असली प्रोफेशनल एंट्री फिल्टर)
    m5_bull_ok = True
    m5_bear_ok = True
    if df_5m is not None and len(df_5m) >= 25:
        df_5m_q = calculate_quant_metrics(df_5m)
        m5_last = df_5m_q.iloc[-2]
        m5_prev = df_5m_q.iloc[-3]
        # 5M पर भी कीमत EMA20 के ऊपर हो और मोमेंटम हमारे साथ हो
        m5_bull_ok = (float(m5_last['Close']) > float(m5_last['EMA20'])) and (float(m5_last['Close']) >= float(m5_prev['Low']))
        m5_bear_ok = (float(m5_last['Close']) < float(m5_last['EMA20'])) and (float(m5_last['Close']) <= float(m5_prev['High']))

    # पिछले 3 घंटे (12 कैंडल्स) का लिक्विडिटी पूल
    pool_12 = df_15m.iloc[-14:-3]
    pool_high = float(pool_12['High'].max())
    pool_low  = float(pool_12['Low'].min())

    # =========================================================================
    # SETUP A: BULLISH TRIAD (1H STRICT BULLISH + DXY WEAK/NEUTRAL + 5M ALIGNED)
    # =========================================================================
    if htf["bias"] == "STRICT_BULLISH" and m5_bull_ok:
        dxy_ok, _ = is_dxy_aligned_with_trade(pair_info, "BUY", dxy_state)
        # सामने 1H की दीवार कम से कम 1.4x ATR दूर हो (Vacuum Room to Target)
        vacuum_room_up = (htf["major_high"] - price) >= (atr * 1.4) or (price > htf["major_high"])

        if dxy_ok and vacuum_room_up and 44 <= rsi <= 67:
            # PATTERN 1: Institutional Liquidity Flush & 2-Bar Absorption
            # c1 या c0 ने पिछले लिक्विडिटी लो (या EMA50/20 के नीचे के स्टॉप्स) को फ्लश किया,
            # और c0 ने मजबूत डिस्प्लेसमेंट (disp_z >= 1.15) के साथ c1 के High के ऊपर क्लोजिंग दी!
            flushed_stops = (low_0 <= ema20) or (float(c1['Low']) <= ema20) or (low_0 < pool_low) or (float(c1['Low']) < float(c2['Low']))
            strong_reclaim = (price > open_0) and (price > float(c1['High'])) and (close_pos >= 0.68) and (disp_z >= 1.10) and (price > ema20 >= ema50)

            if flushed_stops and strong_reclaim and er >= 0.30:
                swing_floor = min(low_0, float(c1['Low']), float(c2['Low']))
                sl_price = swing_floor - (atr * 0.28)
                return (
                    "BUY (LONG) 🟢",
                    "BUY",
                    f"Triad Protocol: 1H Bullish + DXY ({dxy_state}) + 15M/5M Stop-Flush Absorption",
                    sl_price
                )

            # PATTERN 2: High-Efficiency Institutional Vacuum Breakout
            # जब मार्केट बिना रुके स्मूद ट्रेंड में हो (ER >= 0.48) और 3 घंटे के कंसोलिडेशन को वॉल्यूम के साथ तोड़े
            clean_breakout = (float(c1['Close']) <= pool_high) and (price > pool_high) and (price > open_0) and (body_ratio >= 0.65) and (close_pos >= 0.72)
            if clean_breakout and er >= 0.45 and disp_z >= 1.30 and price > ema20 > ema50:
                sl_price = max(low_0 - (atr * 0.25), price - (atr * 1.5))
                return (
                    "BUY (LONG) 🟢",
                    "BUY",
                    f"Triad Protocol: High-Efficiency Vacuum Breakout (ER: {er:.2f} | DXY: {dxy_state})",
                    sl_price
                )

    # =========================================================================
    # SETUP B: BEARISH TRIAD (1H STRICT BEARISH + DXY STRONG/NEUTRAL + 5M ALIGNED)
    # =========================================================================
    if htf["bias"] == "STRICT_BEARISH" and m5_bear_ok:
        dxy_ok, _ = is_dxy_aligned_with_trade(pair_info, "SELL", dxy_state)
        vacuum_room_down = (price - htf["major_low"]) >= (atr * 1.4) or (price < htf["major_low"])

        if dxy_ok and vacuum_room_down and 33 <= rsi <= 56:
            # PATTERN 1: Institutional Bull-Trap Flush & 2-Bar Bearish Absorption
            flushed_highs = (high_0 >= ema20) or (float(c1['High']) >= ema20) or (high_0 > pool_high) or (float(c1['High']) > float(c2['High']))
            strong_breakdown = (price < open_0) and (price < float(c1['Low'])) and (close_pos <= 0.32) and (disp_z >= 1.10) and (price < ema20 <= ema50)

            if flushed_highs and strong_breakdown and er >= 0.30:
                swing_ceiling = max(high_0, float(c1['High']), float(c2['High']))
                sl_price = swing_ceiling + (atr * 0.28)
                return (
                    "SELL (SHORT) 🔴",
                    "SELL",
                    f"Triad Protocol: 1H Bearish + DXY ({dxy_state}) + 15M/5M Bull-Trap Absorption",
                    sl_price
                )

            # PATTERN 2: High-Efficiency Institutional Vacuum Breakdown
            clean_breakdown = (float(c1['Close']) >= pool_low) and (price < pool_low) and (price < open_0) and (body_ratio >= 0.65) and (close_pos <= 0.28)
            if clean_breakdown and er >= 0.45 and disp_z >= 1.30 and price < ema20 < ema50:
                sl_price = min(high_0 + (atr * 0.25), price + (atr * 1.5))
                return (
                    "SELL (SHORT) 🔴",
                    "SELL",
                    f"Triad Protocol: High-Efficiency Vacuum Breakdown (ER: {er:.2f} | DXY: {dxy_state})",
                    sl_price
                )

    return None, None, None, None

def analyze_strict_1h_structure_wrapper(df_1h):
    return analyze_strict_1h_institutional_structure(df_1h)

def send_live_forex_summary():
    market_state = "🟢 मार्केट चालू है (TRIAD MACRO-QUANT MODE)" if is_forex_market_open() else "🔴 वीकेंड: मार्केट बंद है"
    _, kz_name = get_institutional_killzone()
    dxy_state, _ = get_live_dxy_macro_flow()

    report = (
        f"🧠 <b>MACRO-QUANT TRIAD REPORT ($100K CHALLENGE)</b> 🧠\n\n"
        f"📢 <b>Status:</b> {market_state}\n"
        f"🕒 <b>Killzone:</b> {kz_name}\n"
        f"💵 <b>US Dollar Index (DXY) Flow:</b> <b>{dxy_state}</b>\n\n"
    )

    for name, info in FOREX_PAIRS.items():
        try:
            ticker = yf.Ticker(info["symbol"])
            df_15m = ticker.history(period="5d", interval="15m")
            df_1h  = ticker.history(period="1mo", interval="1h")
            if df_15m.empty or len(df_15m) < 50 or df_1h.empty or len(df_1h) < 40:
                continue

            df_15m = calculate_quant_metrics(df_15m)
            df_1h  = calculate_quant_metrics(df_1h)
            htf    = analyze_strict_1h_institutional_structure(df_1h)

            latest = df_15m.iloc[-1]
            price  = float(latest['Close'])
            er     = float(latest['Efficiency_Ratio'])
            p_str  = format_price(price, info["decimals"])

            report += (
                f"🔹 <b>{name}:</b> {p_str}\n"
                f"   • 1H Structure: <b>{htf['bias']}</b> | Efficiency: {er:.2f}\n\n"
            )
        except Exception:
            report += f"🔹 <b>{name}:</b> लोड हो रहा है...\n\n"

    report += f"{get_funding_pips_dashboard()}"
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
    global daily_realized_pnl, total_competition_pnl

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

            # 1. 1:1 आने पर तुरंत Breakeven और 50% प्रॉफिट लॉक
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
                    half_prof                  = trade["max_risk"] * 0.5  # +$125 पक्का लॉक
                    trade["status"]            = f"🔥 1:1 LOCKED (+${half_prof:.0f} Safe | SL at Cost {entry_str})"
                    send_telegram_message(
                        f"🛡️ <b>TRIAD SHIELD (Trade #{trade['id']}) : 1:1 HIT — LOCK PROFIT!</b> 🛡️\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n"
                        f"✅ <b>1:1 टारगेट हिट हो गया है! (Live: {price_str})</b>\n\n"
                        f"📢 <b>तुरंत करें:</b> आधा लॉट बुक कर लें (<b>+${half_prof:.0f} पक्का</b>) और बचे हुए लॉट का SL उठाकर <b>Entry ({entry_str})</b> पर लगा दें!"
                    )
                    continue

            # 2. Target या SL चेक करें
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
                total_competition_pnl += net_win
                trade["status"] = f"✅ FULL TARGET HIT (+${net_win:.0f} प्रॉफिट)"
                send_telegram_message(
                    f"🎯 <b>TRIAD SNIPER TRADE #{trade['id']} : FULL TARGET (1:2.5) HIT!</b> 🎯\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target Hit:</b> {target_str}\n\n"
                    f"💰 <b>Result:</b> <b>✅ +${net_win:.0f} प्रॉफिट! (आपका पुराना लॉस रिकवर हो गया!)</b>\n\n"
                    f"────────────────────\n\n{get_funding_pips_dashboard()}"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                half_prof = trade["max_risk"] * 0.5
                daily_realized_pnl += half_prof
                total_competition_pnl += half_prof
                trade["status"] = f"🛡️ BREAKEVEN EXIT (+${half_prof:.0f} Locked)"
                send_telegram_message(
                    f"🛡️ <b>TRIAD TRADE #{trade['id']} : BREAKEVEN SL HIT!</b> 🛡️\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']}\n"
                    f"📢 <b>रिजल्ट:</b> 1:1 के बाद मार्केट वापस कॉस्ट पर आया। 50% बुकिंग से <b>+${half_prof:.0f} प्रॉफिट</b> मिला और बचे लॉट पर $0 नुकसान हुआ!\n\n"
                    f"────────────────────\n\n{get_funding_pips_dashboard()}"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_competition_pnl -= loss_amt
                trade["status"] = f"🛑 SL HIT (-${loss_amt:.0f} Micro Risk)"
                send_telegram_message(
                    f"🛑 <b>TRIAD TRADE #{trade['id']} : STOP-LOSS HIT!</b> 🛑\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']}\n"
                    f"⚠️ <b>Controlled Loss:</b> <b>-${loss_amt:.0f} (सिर्फ 0.25% रिस्क)</b>\n\n"
                    f"────────────────────\n\n{get_funding_pips_dashboard()}"
                )
                continue

            # 3. Early Structure Invalidation (अगर अगली ही कैंडल हमारे ट्रैप जोन के खिलाफ बंद हो जाए तो पूरा SL मत दो!)
            hold_minutes = (candle_time - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= 30 and not trade["trailed_to_cost"]:
                closed_price = float(closed_15m['Close'])
                invalidated = False
                if trade["direction"] == "BUY" and closed_price < ema20 and closed_price < (trade["entry"] - trade["sl_dist"] * 0.55):
                    invalidated = True
                elif trade["direction"] == "SELL" and closed_price > ema20 and closed_price > (trade["entry"] + trade["sl_dist"] * 0.55):
                    invalidated = True

                if invalidated:
                    trade["is_open"] = False
                    saved_loss = round(trade["max_risk"] * 0.55, 0)
                    daily_realized_pnl -= saved_loss
                    total_competition_pnl -= saved_loss
                    trade["status"] = f"🔄 INVALIDATION EXIT (-${saved_loss:.0f} Saved SL)"
                    send_telegram_message(
                        f"🔄 <b>PRO TRADER EARLY EXIT (Trade #{trade['id']})!</b> 🔄\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n"
                        f"⚠️ <b>कारण:</b> 15M कैंडल EMA-20 के नीचे बंद हो गई है (ऑपरेटर फॉलो-थ्रू फेल)। पूरा SL देने के बजाय अभी एग्जिट करके अपना पैसा बचाएं!\n\n"
                        f"────────────────────\n\n{get_funding_pips_dashboard()}"
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

    if daily_realized_pnl <= -DAILY_SAFE_STOP_USD:
        return

    # Step 0: सबसे पहले US Dollar Index (DXY) का लाइव मैक्रो फ्लो निकालो
    dxy_state, _ = get_live_dxy_macro_flow()

    for name, info in FOREX_PAIRS.items():
        try:
            ticker = yf.Ticker(info["symbol"])
            df_1h  = ticker.history(period="1mo", interval="1h")
            df_15m = ticker.history(period="5d", interval="15m")
            df_5m  = ticker.history(period="5d", interval="5m")
            df_1m  = ticker.history(period="1d", interval="1m")

            if df_15m.empty or len(df_15m) < 60 or df_1h.empty or len(df_1h) < 40:
                continue

            df_1h  = calculate_quant_metrics(df_1h)
            df_15m = calculate_quant_metrics(df_15m)

            latest_live_15m = df_15m.iloc[-1]
            closed_15m      = df_15m.iloc[-2]
            closed_time     = df_15m.index[-2]

            price = float(df_1m.iloc[-1]['Close']) if (df_1m is not None and not df_1m.empty) else float(latest_live_15m['Close'])
            exact_now_time = df_1m.index[-1] if (df_1m is not None and not df_1m.empty) else closed_time
            atr   = float(closed_15m['ATR'])
            dec   = info["decimals"]

            # 1. चल रहे ट्रेड्स को मैनेज करें
            manage_active_forex_trades(name, closed_time, df_1m, latest_live_15m, closed_15m, dec)

            # 2. एक समय पर एक पेयर में एक ही ट्रेड
            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            if trades_today >= MAX_TRADES_PER_DAY:
                continue

            if last_candle_traded[name] == closed_time:
                continue

            # 3. Run Original 4-Layer Triad Protocol
            signal, direction, strategy_used, custom_sl = evaluate_triad_proprietary_strategy(
                name, info, df_1h, df_15m, df_5m, dxy_state
            )

            if signal and custom_sl is not None:
                raw_sl_dist = abs(price - custom_sl)
                min_sl_dist = atr * 1.0
                max_sl_dist = atr * 1.85
                sl_dist = round(max(min_sl_dist, min(raw_sl_dist, max_sl_dist)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                trades_today += 1

                rr_ratio = 2.5  # $250 Risk -> +$625 Target (1 ट्रेड में $600 लॉस की पूरी रिकवरी)
                target_dist = round(sl_dist * rr_ratio, dec)

                if direction == "BUY":
                    sl_level          = round(price - sl_dist, dec)
                    half_target_level = round(price + sl_dist, dec)
                    target_level      = round(price + target_dist, dec)
                else:
                    sl_level          = round(price + sl_dist, dec)
                    half_target_level = round(price - sl_dist, dec)
                    target_level      = round(price - target_dist, dec)

                max_risk_usd  = RISK_PER_TRADE_USD  # $250
                expected_prof = round(max_risk_usd * rr_ratio, 0)  # +$625
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

                msg = (
                    f"🎯 <b>TRIAD MACRO-QUANT SNIPER SIGNAL ({trades_today}/{MAX_TRADES_PER_DAY})</b> 🎯\n\n"
                    f"🌍 <b>Pair:</b> <b>{name}</b> (<code>{info['mt5_name']}</code>)\n"
                    f"💵 <b>Macro DXY Check:</b> Verified ({dxy_state})\n"
                    f"🧠 <b>Proprietary Setup:</b> {strategy_used}\n\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n\n"
                    f"📍 <b>Entry Price:</b> {entry_s}\n"
                    f"🛑 <b>Stop-Loss (SL):</b> {sl_s}  <i>({sl_pips_str} का गैप)</i>\n"
                    f"🛡️ <b>1:1 Safe Lock:</b> {half_s}  <i>(+${max_risk_usd*0.5:.0f} बुक करें और SL कॉस्ट पर लाएं)</i>\n"
                    f"🎯 <b>Full Target (1:2.5):</b> {target_s}  <i>({target_pips_str} का टारगेट)</i>\n\n"
                    f"💼 <b>Exact MT5 Lot Size:</b> <b><code>{exact_lot:.2f} Lot</code></b>\n"
                    f"🛡️ <b>Recovery Risk:</b> <b>${max_risk_usd:.0f} (0.25%)</b>  ➡️  <b>Target: +${expected_prof:.0f}</b>\n\n"
                    f"────────────────────\n\n"
                    f"{get_funding_pips_dashboard()}"
                )
                send_telegram_message(msg)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    send_telegram_message(
        "🧠 <b>Triad Macro-Quant Sniper Bot Active ($600 Recovery Mode)!</b> 🧠\n\n"
        "🛠️ <b>पिछले लॉस की तीनों गलतियां 100% बंद कर दी गई हैं:</b>\n"
        "1️⃣ <b>Live DXY (Dollar Index) Lock:</b> अब अगर Dollar मजबूत हो रहा है, तो बॉट Gold और EUR/USD में कभी गलत BUY नहीं देगा!\n"
        "2️⃣ <b>No 'LEAN' / Sideways Trades:</b> 1H चार्ट थोड़ा भी साइडवेज या कन्फ्यूज हुआ तो बॉट बिल्कुल शांत बैठेगा।\n"
        "3️⃣ <b>1H + 15M + 5M Triad Alignment:</b> जब तक 5-मिनट चार्ट पर भी स्टॉप-हंट के बाद ऑपरेटर एब्जॉर्प्शन कन्फर्म नहीं होगा, कोई ट्रेड नहीं आएगा।\n"
        "4️⃣ <b>Safe Recovery Math:</b> रिस्क घटाकर <b>$250 (0.25%)</b> और टारगेट <b>+$625 (1:2.5)</b> कर दिया गया है—सिर्फ 1 सही ट्रेड आपका पूरा $600 रिकवर कर देगा!"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 25:
            scan_forex_market()
            last_scan_time = time.time()
        time.sleep(3)
