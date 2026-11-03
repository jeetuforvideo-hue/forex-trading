import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# =====================================================================
# 1. आपकी पर्सनल और सिक्योर सेटिंग्स (HTF 1H + 15M OPERATOR EDITION)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9153301687:AAEO3EFmMW6BhenVqk01NjwbJLHvyvN479o")
CHAT_ID   = "5610520408"  # सिर्फ आपकी टेलीग्राम आईडी पर काम करेगा

TOTAL_CAPITAL_USD  = 1000   # $1,000 (लगभग ₹85,000) के हिसाब से स्टैंडर्ड रिस्क कैलकुलेशन
RISK_PER_TRADE_PCT = 1.0    # प्रति ट्रेड 1% रिस्क ($10)
MAX_TRADES_PER_DAY = 3      # 24 घंटे में सिर्फ 3 सबसे बेस्ट बड़े टाइमफ्रेम के ऑपरेटर ट्रेड्स
ADX_THRESHOLD      = 20     # मजबूत ट्रेंड और मोमेंटम फिल्टर
MAX_HOLD_MINUTES   = 180    # 15M/1H बड़े टाइमफ्रेम के हिसाब से 3 घंटे (180 मिनट) का होल्ड टाइम

# फॉरेक्स के 5 सबसे बेहतरीन और लिक्विड पेयर्स
FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "symbol": "GC=F",
        "mt5_name": "XAUUSD",
        "decimals": 2,
        "unit_name": "Points"
    },
    "EUR/USD": {
        "symbol": "EURUSD=X",
        "mt5_name": "EURUSD",
        "decimals": 5,
        "unit_name": "Pips"
    },
    "GBP/USD": {
        "symbol": "GBPUSD=X",
        "mt5_name": "GBPUSD",
        "decimals": 5,
        "unit_name": "Pips"
    },
    "USD/JPY": {
        "symbol": "JPY=X",
        "mt5_name": "USDJPY",
        "decimals": 3,
        "unit_name": "Pips"
    },
    "GBP/JPY": {
        "symbol": "GBPJPY=X",
        "mt5_name": "GBPJPY",
        "decimals": 3,
        "unit_name": "Pips"
    }
}

last_candle_traded = {name: None for name in FOREX_PAIRS}
trades_today = 0
last_update_id = 0
daily_trades_log = []

def get_ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

current_day = get_ist_now().date()

def is_forex_market_open():
    """फॉरेक्स मार्केट सोमवार सुबह 3:30 AM IST से शनिवार सुबह 3:30 AM IST तक 24 घंटे खुलता है"""
    now = get_ist_now()
    wd = now.weekday() # Monday=0 ... Saturday=5, Sunday=6
    if wd == 6:
        return False
    if wd == 5 and now.hour >= 4:
        return False
    return True

def get_current_session_info():
    """ऑपरेटर वोलैटिलिटी और लिक्विडिटी टाइमिंग की जांच (IST के अनुसार)"""
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)
    # सुबह 3:30 से 5:30 के बीच रोलओवर स्प्रेड हाई होता है, इसलिए उसे अवॉइड करें
    if 3.5 <= hr <= 5.5:
        return False, "Low Liquidity Rollover Zone (High Spread)"
    elif 5.5 < hr < 12.5:
        return True, "Asian / Tokyo Institutional Session"
    elif 12.5 <= hr < 18.0:
        return True, "London Killzone Session (High Liquidity)"
    elif 18.0 <= hr < 22.5:
        return True, "London + New York Overlap (Maximum Operator Volume)"
    else:
        return True, "New York Late Institutional Session"

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
    """Exness चार्ट पर तुरंत SL और Target लगाने के लिए सटीक Pips/Points की गणना"""
    if "GOLD" in pair_name:
        return f"${price_diff:.2f} ({price_diff * 10:.0f} Points)"
    elif decimals == 3:
        return f"{price_diff * 100:.1f} Pips"
    else:
        return f"{price_diff * 10000:.1f} Pips"

def get_trades_scoreboard():
    if not daily_trades_log:
        return "📋 <b>आज (24h) के बड़े टाइमफ्रेम (1H+15M) ट्रेड्स का हाल:</b>\n\n• अभी कोई हाई-एक्यूरेसी ऑपरेटर ट्रेड नहीं लिया गया है।"
    board = "📋 <b>आज के तीनों ऑपरेटर ट्रेड्स का रिजल्ट:</b>\n\n"
    for t in daily_trades_log:
        board += f"👉 <b>Trade #{t['id']} ({t['pair']} - {t['direction']}):</b>\n   {t['status']}\n\n"
    return board.strip()

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
                    if chat_id:
                        send_telegram_message(
                            "⛔ <b>Access Denied:</b> यह एक प्राइवेट पर्सनल फॉरेक्स ट्रेडिंग बॉट है।",
                            target_chat_id=chat_id
                        )
                    continue

                if msg_text in ["/status", "status", "/start", "start"]:
                    send_live_forex_summary()
    except Exception:
        pass

def calculate_indicators(df):
    """बड़े टाइमफ्रेम (15M और 1H) के लिए माइक्रो-स्ट्रक्चर, कैंडल एनाटॉमी और ऑपरेटर मेट्रिक्स"""
    df = df.copy()
    df['EMA20']  = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50']  = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

    # RSI Calculation (Wilder's Smoothing approximation)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(50.0)

    # True Range & ATR (Volatility Engine)
    hl = df['High'] - df['Low']
    hc = np.abs(df['High'] - df['Close'].shift())
    lc = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    # ADX (Trend Strength Filter)
    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_smooth = tr.rolling(14).mean().replace(0, np.nan)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).mean() / tr_smooth)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).mean() / tr_smooth)
    dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    df['ADX'] = dx.rolling(14).mean().fillna(15.0)

    # सूक्ष्म कैंडल एनालिसिस (Micro Candle Anatomy)
    df['Candle_Body']  = np.abs(df['Close'] - df['Open'])
    df['Candle_Range'] = (df['High'] - df['Low']).replace(0, 1e-9)
    df['Body_Ratio']   = df['Candle_Body'] / df['Candle_Range']
    df['Upper_Wick']   = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Wick']   = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Close_Pos']    = (df['Close'] - df['Low']) / df['Candle_Range'] # 0.0=Bottom, 1.0=Top
    df['Vol_MA20']     = df['Volume'].rolling(20).mean() if 'Volume' in df.columns else 0.0

    # पिछले 40 कैंडल्स (15M पर 10 घंटे) का Premium / Discount Equilibrium (50% Level)
    df['Range_High_40'] = df['High'].rolling(40).max()
    df['Range_Low_40']  = df['Low'].rolling(40).min()
    df['Equilibrium']   = (df['Range_High_40'] + df['Range_Low_40']) / 2.0

    return df

def analyze_htf_1h_structure(df_1h):
    """
    बड़े टाइमफ्रेम (1-Hour Chart) का डीप एनालिसिस:
    • 1H का मुख्य इंस्टीट्यूशनल फ्लो (Bullish / Bearish)
    • 1H के मेजर रेजिस्टेंस और सपोर्ट (ताकि हम ठीक दीवार के सामने ट्रेड न लें)
    """
    c_1h = df_1h.iloc[-2] # आखिरी पूरी तरह क्लोज हुई 1-घंटे की कैंडल
    p_1h = df_1h.iloc[-3]

    close_1h  = float(c_1h['Close'])
    ema20_1h  = float(c_1h['EMA20'])
    ema50_1h  = float(c_1h['EMA50'])
    ema200_1h = float(c_1h['EMA200'])
    atr_1h    = float(c_1h['ATR']) if not np.isnan(c_1h['ATR']) else 0.0

    # पिछले 12 घंटे (12 कैंडल्स) का 1H स्ट्रक्चर हाई और लो
    recent_1h = df_1h.iloc[-14:-2]
    htf_res   = float(recent_1h['High'].max())
    htf_sup   = float(recent_1h['Low'].min())
    htf_mid   = (htf_res + htf_sup) / 2.0

    # 1H ट्रेंड स्कोरिंग (बारीक जांच)
    bull_score = 0
    bear_score = 0

    if close_1h > ema50_1h:
        bull_score += 1
    else:
        bear_score += 1

    if ema20_1h > ema50_1h:
        bull_score += 1
    else:
        bear_score += 1

    if close_1h > ema200_1h:
        bull_score += 1
    else:
        bear_score += 1

    if float(c_1h['High']) >= float(p_1h['High']) and float(c_1h['Low']) >= float(p_1h['Low']):
        bull_score += 1
    elif float(c_1h['High']) <= float(p_1h['High']) and float(c_1h['Low']) <= float(p_1h['Low']):
        bear_score += 1

    if bull_score >= 3:
        htf_bias = "BULLISH"
    elif bear_score >= 3:
        htf_bias = "BEARISH"
    else:
        htf_bias = "NEUTRAL"

    return {
        "bias": htf_bias,
        "htf_res": htf_res,
        "htf_sup": htf_sup,
        "htf_mid": htf_mid,
        "atr_1h": atr_1h,
        "ema50_1h": ema50_1h
    }

def evaluate_htf_operator_strategies(df_15m, df_1h):
    """
    4 बिल्कुल नई, खुद की बनाई हुई Multi-Timeframe (1H + 15M) ऑपरेटर स्ट्रैटेजी:
    नोट: यहाँ df_15m.iloc[-2] (पूरी तरह बंद हो चुकी 15-मिनट की कैंडल) का उपयोग किया गया है,
    ताकि अधूरी कैंडल के फेकआउट से कभी लॉस न हो!
    """
    session_ok, _ = get_current_session_info()
    if not session_ok:
        return None, None, None, None

    htf = analyze_htf_1h_structure(df_1h)
    htf_bias = htf["bias"]

    # Confirmed Closed Candles on 15-Minute Chart (-2 is last closed, -3 is prior closed)
    curr = df_15m.iloc[-2]
    prev = df_15m.iloc[-3]
    prev2 = df_15m.iloc[-4]

    price      = float(curr['Close'])
    open_p     = float(curr['Open'])
    high_p     = float(curr['High'])
    low_p      = float(curr['Low'])
    body       = float(curr['Candle_Body'])
    c_range    = float(curr['Candle_Range'])
    body_ratio = float(curr['Body_Ratio'])
    u_wick     = float(curr['Upper_Wick'])
    l_wick     = float(curr['Lower_Wick'])
    close_pos  = float(curr['Close_Pos'])

    atr        = float(curr['ATR'])
    ema20      = float(curr['EMA20'])
    ema50      = float(curr['EMA50'])
    ema200     = float(curr['EMA200'])
    rsi        = float(curr['RSI'])
    adx        = float(curr['ADX'])
    eq_level   = float(curr['Equilibrium'])

    if np.isnan(atr) or atr <= 0 or c_range <= 0:
        return None, None, None, None

    # बारीक नियम 0: Overextended Exhaustion Check (अगर कैंडल पहले ही 2.8x ATR से बड़ी बन गई है, तो चेज न करें)
    if c_range > (atr * 2.8):
        return None, None, None, None

    # पिछले 12 कैंडल्स (3 घंटे का 15M स्ट्रक्चर) और 24 कैंडल्स (6 घंटे का सेशन बॉक्स)
    struct_12     = df_15m.iloc[-14:-2]
    swing_high_12 = float(struct_12['High'].max())
    swing_low_12  = float(struct_12['Low'].min())

    box_24        = df_15m.iloc[-26:-3]
    box_high_24   = float(box_24['High'].max())
    box_low_24    = float(box_24['Low'].min())
    box_width_24  = box_high_24 - box_low_24

    # =========================================================================
    # STRATEGY 1: HTF (1H) ORDER FLOW + 15M JUDAS SWING LIQUIDITY SWEEP
    # सूक्ष्म जांच:
    # 1. 1H का ट्रेंड खिलाफ नहीं होना चाहिए + कीमत Discount (BUY) या Premium (SELL) ज़ोन में हो
    # 2. पिछले 3 घंटे का स्विंग लो/हाई स्वीप हुआ हो (रिटेलर्स का SL कटा हो)
    # 3. विक रिजेक्शन कम से कम 42% हो और कैंडल अपनी रेंज के टॉप 35% (BUY) या बॉटम 35% (SELL) में बंद हो
    # =========================================================================
    # Bullish Judas Swing Sweep
    swept_low = (low_p < swing_low_12 and price > swing_low_12) or (float(prev['Low']) < swing_low_12 and price > swing_low_12 and price > float(prev['High']))
    if swept_low and htf_bias != "BEARISH":
        in_discount = (price <= eq_level * 1.002) or (rsi < 52)
        strong_rejection = (l_wick >= c_range * 0.42) or (body_ratio >= 0.55 and price > float(prev['High']))
        if price > open_p and close_pos >= 0.65 and c_range >= (atr * 0.75) and in_discount and strong_rejection:
            lowest_point = min(low_p, float(prev['Low']))
            sl_level = lowest_point - (atr * 0.35)
            return "BUY (LONG) 🟢", "BUY", "1H+15M Judas Swing Liquidity Sweep (Discount Zone)", sl_level

    # Bearish Judas Swing Sweep
    swept_high = (high_p > swing_high_12 and price < swing_high_12) or (float(prev['High']) > swing_high_12 and price < swing_high_12 and price < float(prev['Low']))
    if swept_high and htf_bias != "BULLISH":
        in_premium = (price >= eq_level * 0.998) or (rsi > 48)
        strong_rejection = (u_wick >= c_range * 0.42) or (body_ratio >= 0.55 and price < float(prev['Low']))
        if price < open_p and close_pos <= 0.35 and c_range >= (atr * 0.75) and in_premium and strong_rejection:
            highest_point = max(high_p, float(prev['High']))
            sl_level = highest_point + (atr * 0.35)
            return "SELL (SHORT) 🔴", "SELL", "1H+15M Judas Swing Bull-Trap Sweep (Premium Zone)", sl_level

    # =========================================================================
    # STRATEGY 2: UNMITIGATED ORDER BLOCK + FVG + OTE (61.8% - 79%) REJECTION
    # सूक्ष्म जांच:
    # 1. पिछले 4 से 12 कैंडल्स (1 से 3 घंटे) के बीच एक विशाल ऑपरेटर कैंडल ने FVG बनाया हो
    # 2. वह ज़ोन बीच की किसी भी कैंडल द्वारा पहले तोड़ा न गया हो (100% Unmitigated Check)
    # 3. वर्तमान 15M क्लोज्ड कैंडल उस ज़ोन को टेस्ट करके मजबूत बॉडी के साथ वापस उछली हो
    # =========================================================================
    for i in range(4, 13):
        c1 = df_15m.iloc[-i-2] # Base / Order Block Candle
        c2 = df_15m.iloc[-i-1] # Institutional Impulse Candle
        c3 = df_15m.iloc[-i]   # Confirmation Candle

        # Bullish Unmitigated OB + FVG
        if htf_bias == "BULLISH" and float(c3['Low']) > float(c1['High']) and float(c2['Close']) > float(c2['Open']) and float(c2['Candle_Body']) >= (atr * 1.15):
            zone_top    = float(c3['Low'])
            zone_bottom = float(c1['Low']) # Order block base
            intervening = df_15m.iloc[-i+1:-2]
            already_broken = (len(intervening) > 0) and (float(intervening['Low'].min()) < zone_bottom)

            if not already_broken and low_p <= zone_top and low_p >= (zone_bottom - atr * 0.15):
                if price > open_p and close_pos >= 0.65 and body_ratio >= 0.48 and price > ema50:
                    sl_ob = min(low_p, zone_bottom) - (atr * 0.35)
                    return "BUY (LONG) 🟢", "BUY", "15M Unmitigated Order Block + FVG Mitigation (1H Aligned)", sl_ob

        # Bearish Unmitigated OB + FVG
        if htf_bias == "BEARISH" and float(c3['High']) < float(c1['Low']) and float(c2['Close']) < float(c2['Open']) and float(c2['Candle_Body']) >= (atr * 1.15):
            zone_bottom = float(c3['High'])
            zone_top    = float(c1['High']) # Order block top
            intervening = df_15m.iloc[-i+1:-2]
            already_broken = (len(intervening) > 0) and (float(intervening['High'].max()) > zone_top)

            if not already_broken and high_p >= zone_bottom and high_p <= (zone_top + atr * 0.15):
                if price < open_p and close_pos <= 0.35 and body_ratio >= 0.48 and price < ema50:
                    sl_ob = max(high_p, zone_top) + (atr * 0.35)
                    return "SELL (SHORT) 🔴", "SELL", "15M Unmitigated Bearish Order Block + FVG Supply (1H Aligned)", sl_ob

    # =========================================================================
    # STRATEGY 3: SESSION AMD (ACCUMULATION, MANIPULATION, DISTRIBUTION) TRAP
    # सूक्ष्म जांच:
    # 1. पिछले 6 घंटे (24 कैंडल्स) मार्केट एक टाइट एक्यूमुलेशन बॉक्स में रहा हो (Width < 4.8x ATR)
    # 2. पिछली कैंडल ने बॉक्स तोड़ा (Manipulation / Retail Breakout Trap)
    # 3. वर्तमान क्लोज्ड कैंडल ने तेजी से वापस बॉक्स के अंदर क्लोजिंग दी और पिछली कैंडल को Engulf कर लिया
    # =========================================================================
    if box_width_24 <= (atr * 4.8):
        prev_low   = float(prev['Low'])
        prev_high  = float(prev['High'])
        prev_close = float(prev['Close'])

        # Bullish AMD Manipulation Reversal (Spring)
        if (prev_low < box_low_24 or prev_close < box_low_24) and price > box_low_24:
            if price > open_p and price > float(prev['High']) and body >= (atr * 0.75) and close_pos >= 0.70 and htf_bias != "BEARISH":
                sl_amd = min(low_p, prev_low) - (atr * 0.35)
                return "BUY (LONG) 🟢", "BUY", "15M Session AMD Manipulation Trap (Wyckoff Spring)", sl_amd

        # Bearish AMD Manipulation Reversal (Upthrust)
        if (prev_high > box_high_24 or prev_close > box_high_24) and price < box_high_24:
            if price < open_p and price < float(prev['Low']) and body >= (atr * 0.75) and close_pos <= 0.30 and htf_bias != "BULLISH":
                sl_amd = max(high_p, prev_high) + (atr * 0.35)
                return "SELL (SHORT) 🔴", "SELL", "15M Session AMD Manipulation Trap (Wyckoff Upthrust)", sl_amd

    # =========================================================================
    # STRATEGY 4: MULTI-TIMEFRAME INSTITUTIONAL DISPLACEMENT & CHoCH / BOS
    # सूक्ष्म जांच:
    # 1. 1H और 15M दोनों का ट्रेंड पूरी तरह एक दिशा में हो और ADX >= 20 हो
    # 2. कैंडल बॉडी 1.35x ATR से बड़ी हो और बिना लंबी उलटी विक के (Body Ratio >= 68%) स्ट्रक्चर तोड़े
    # 3. सामने 1-Hour की कोई मेजर रुकावट (Obstacle Wall) न हो (कम से कम 1.2x ATR का खुला रास्ता हो)
    # =========================================================================
    vol_now   = float(curr['Volume']) if 'Volume' in curr and not np.isnan(curr['Volume']) else 0
    vol_avg   = float(curr['Vol_MA20']) if 'Vol_MA20' in curr and not np.isnan(curr['Vol_MA20']) else 0
    vol_valid = (vol_avg == 0) or (vol_now >= vol_avg * 1.20)

    if body >= (atr * 1.35) and body_ratio >= 0.68 and adx >= ADX_THRESHOLD and vol_valid:
        # Bullish MTF Institutional Break of Structure
        room_to_htf_res = (htf["htf_res"] - price) >= (atr * 1.2) or (price > htf["htf_res"])
        higher_lows = float(curr['Low']) > float(prev['Low']) >= float(prev2['Low'])
        if htf_bias == "BULLISH" and price > open_p and float(prev['Close']) <= swing_high_12 and price > swing_high_12:
            if price > ema20 and ema20 > ema50 and 52 <= rsi <= 73 and room_to_htf_res and higher_lows:
                sl_bos = max(low_p - (atr * 0.30), price - (atr * 1.8))
                return "BUY (LONG) 🟢", "BUY", "1H+15M Institutional Displacement & Structure Break (BOS)", sl_bos

        # Bearish MTF Institutional Break of Structure
        room_to_htf_sup = (price - htf["htf_sup"]) >= (atr * 1.2) or (price < htf["htf_sup"])
        lower_highs = float(curr['High']) < float(prev['High']) <= float(prev2['High'])
        if htf_bias == "BEARISH" and price < open_p and float(prev['Close']) >= swing_low_12 and price < swing_low_12:
            if price < ema20 and ema20 < ema50 and 27 <= rsi <= 48 and room_to_htf_sup and lower_highs:
                sl_bos = min(high_p + (atr * 0.30), price + (atr * 1.8))
                return "SELL (SHORT) 🔴", "SELL", "1H+15M Institutional Displacement & Structure Breakdown (BOS)", sl_bos

    return None, None, None, None

def send_live_forex_summary():
    market_state = "🟢 ग्लोबल फॉरेक्स मार्केट खुला है (1H + 15M OPERATOR MODE)" if is_forex_market_open() else "🔴 वीकेंड: फॉरेक्स मार्केट अभी बंद है"
    _, session_name = get_current_session_info()
    report = (
        f"🏦 <b>FOREX MULTI-TIMEFRAME (1H + 15M) REPORT</b> 🏦\n\n"
        f"📢 <b>Status:</b> {market_state}\n"
        f"🕒 <b>Active Session:</b> {session_name}\n\n"
    )

    for name, info in FOREX_PAIRS.items():
        try:
            ticker = yf.Ticker(info["symbol"])
            df_15m = ticker.history(period="10d", interval="15m")
            df_1h  = ticker.history(period="1mo", interval="1h")
            if df_15m.empty or len(df_15m) < 60 or df_1h.empty or len(df_1h) < 50:
                continue

            df_15m = calculate_indicators(df_15m)
            df_1h  = calculate_indicators(df_1h)
            htf    = analyze_htf_1h_structure(df_1h)

            latest = df_15m.iloc[-1]
            price  = float(latest['Close'])
            rsi    = float(latest['RSI'])
            eq     = float(latest['Equilibrium'])
            zone   = "Discount (Buy Zone)" if price < eq else "Premium (Sell Zone)"
            p_str  = format_price(price, info["decimals"])

            report += (
                f"🔹 <b>{name}:</b> {p_str}\n"
                f"   • 1H Trend: <b>{htf['bias']}</b> | 15M: {zone} (RSI: {rsi:.0f})\n\n"
            )
        except Exception:
            report += f"🔹 <b>{name}:</b> डेटा लोड हो रहा है...\n\n"

    report += f"🛡️ <b>आज के कुल ट्रेड:</b> {trades_today} / {MAX_TRADES_PER_DAY}\n\n"
    report += get_trades_scoreboard()
    send_telegram_message(report)

def manage_active_forex_trades(pair_name, candle_time, latest_live, closed_15m, decimals):
    """लाइव टिक पर SL/Target चेक करेगा और क्लोज्ड 15M कैंडल पर स्ट्रक्चर रिवर्सल चेक करेगा"""
    price        = float(latest_live['Close'])
    current_high = float(latest_live['High'])
    current_low  = float(latest_live['Low'])
    ema20        = float(closed_15m['EMA20'])
    ema50        = float(closed_15m['EMA50'])
    adx          = float(closed_15m['ADX']) if not np.isnan(closed_15m['ADX']) else 15.0

    for trade in daily_trades_log:
        if trade["pair"] == pair_name and trade["is_open"]:
            if candle_time <= trade["entry_candle_time"]:
                continue

            entry_str  = format_price(trade["entry"], decimals)
            target_str = format_price(trade["target"], decimals)
            sl_str     = format_price(trade["sl"], decimals)
            price_str  = format_price(price, decimals)

            # 1. TARGET या STOP-LOSS चेक करें
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
                trade["status"] = f"✅ TARGET HIT (+${trade['expected_profit']:.0f} प्रॉफिट)"
                send_telegram_message(
                    f"🎯 <b>OPERATOR TRADE #{trade['id']} : FULL TARGET HIT!</b> 🎯\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['mt5_name']})\n\n"
                    f"⚡ <b>Direction:</b> {trade['action']}\n\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target Hit:</b> {target_str}\n\n"
                    f"💰 <b>Result:</b> <b>✅ +${trade['expected_profit']:.0f} (लगभग +₹{trade['expected_profit']*85:.0f}) प्रॉफिट</b>\n\n"
                    f"📢 <b>ऑपरेटर सलाह:</b> शानदार! बड़े टाइमफ्रेम (1H+15M) का टारगेट पूरा हुआ, अपना प्रॉफिट बुक कर लें।\n\n"
                    f"────────────────────\n\n{get_trades_scoreboard()}"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                trade["status"] = "🛡️ BREAKEVEN EXIT ($0 No Loss)"
                send_telegram_message(
                    f"🛡️ <b>OPERATOR TRADE #{trade['id']} : TRAILING SL HIT (NO LOSS)!</b> 🛡️\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                    f"📢 <b>अपडेट:</b> मार्केट वापस घूम गया, लेकिन स्टॉप-लॉस कॉस्ट (Breakeven) पर होने की वजह से <b>आपका $0 का नुकसान हुआ!</b>\n\n"
                    f"────────────────────\n\n{get_trades_scoreboard()}"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                trade["status"] = f"🛑 STOP-LOSS HIT (-${trade['max_risk']:.0f} लॉस)"
                send_telegram_message(
                    f"🛑 <b>OPERATOR TRADE #{trade['id']} : STOP-LOSS HIT!</b> 🛑\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>SL Hit:</b> {sl_str}\n\n"
                    f"⚠️ <b>Result:</b> <b>🛑 -${trade['max_risk']:.0f} लॉस</b>\n\n"
                    f"📢 <b>सलाह:</b> स्टॉप-लॉस हिट हो गया है, अगले 1H+15M स्मार्ट मनी सेटअप का इंतजार करें।\n\n"
                    f"────────────────────\n\n{get_trades_scoreboard()}"
                )
                continue

            # 2. प्रो नियम: 1:1 आने पर SL को Breakeven (Cost) पर लाना
            if not trade["trailed_to_cost"]:
                half_reached = False
                if trade["direction"] == "BUY" and current_high >= trade["half_target"]:
                    half_reached = True
                elif trade["direction"] == "SELL" and current_low <= trade["half_target"]:
                    half_reached = True

                if half_reached:
                    trade["trailed_to_cost"] = True
                    trade["sl"] = trade["entry"]
                    trade["status"] = "🔥 RUNNING (SL at Breakeven - Risk Free)"
                    send_telegram_message(
                        f"🛡️ <b>SMART MONEY UPDATE (Trade #{trade['id']}) : MOVE SL TO BREAKEVEN!</b> 🛡️\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"✅ <b>1:1 टारगेट पूरा हो चुका है! (Current Price: {price_str})</b>\n\n"
                        f"📢 <b>एक्शन:</b> अब अपने Exness / MT5 में स्टॉप-लॉस को उठाकर <b>Entry Price ({entry_str})</b> पर लगा लें और चाहें तो 50% लॉट बुक कर लें। अब यह ट्रेड 100% रिस्क-फ्री है!"
                    )

            # 3. प्रो नियम: बड़े टाइमफ्रेम (180 मिनट / 3 घंटे) तक मार्केट साइडवेज अटक जाए तो एग्जिट
            hold_minutes = (candle_time - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= MAX_HOLD_MINUTES and not trade["trailed_to_cost"]:
                if adx < ADX_THRESHOLD or abs(price - trade["entry"]) < (trade["sl_dist"] * 0.45):
                    trade["is_open"] = False
                    trade["status"] = "⏳ SIDEWAYS CONSOLIDATION EXIT"
                    send_telegram_message(
                        f"⏳ <b>OPERATOR ALERT (Trade #{trade['id']}) : SIDEWAYS MARKET EXIT!</b> ⏳\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>कारण:</b> 3 घंटे (12 कैंडल्स) हो चुके हैं और 15M चार्ट पर ऑपरेटर फॉलो-थ्रू नहीं आया है।\n\n"
                        f"📢 <b>सलाह:</b> स्टॉप-लॉस का इंतजार न करें, <b>अभी तुरंत कॉस्ट या छोटे प्रॉफिट/लॉस पर ट्रेड क्लोज कर दें!</b>\n\n"
                        f"────────────────────\n\n{get_trades_scoreboard()}"
                    )
                    continue

            # 4. प्रो नियम: अगर 15M की क्लोज्ड कैंडल दोनों EMA (20 और 50) के खिलाफ बंद हो जाए
            if hold_minutes >= 45:
                closed_price = float(closed_15m['Close'])
                trend_reversed = False
                if trade["direction"] == "BUY" and closed_price < ema20 and closed_price < ema50 and closed_price < (trade["entry"] - trade["sl_dist"] * 0.65):
                    trend_reversed = True
                elif trade["direction"] == "SELL" and closed_price > ema20 and closed_price > ema50 and closed_price > (trade["entry"] + trade["sl_dist"] * 0.65):
                    trend_reversed = True

                if trend_reversed:
                    trade["is_open"] = False
                    trade["status"] = "🔄 15M STRUCTURE SHIFT EXIT (जल्दी बचाव)"
                    send_telegram_message(
                        f"🔄 <b>OPERATOR ALERT (Trade #{trade['id']}) : 15M STRUCTURE SHIFT EXIT!</b> 🔄\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>कारण:</b> 15-मिनट चार्ट पर ऑपरेटर स्ट्रक्चर आपके ट्रेड के खिलाफ क्लोज हो गया है।\n\n"
                        f"📢 <b>सलाह:</b> पूरा स्टॉप-लॉस देने से बेहतर है कि अभी तुरंत ट्रेड से बाहर निकल जाएं!\n\n"
                        f"────────────────────\n\n{get_trades_scoreboard()}"
                    )
                    continue

def scan_forex_market():
    global trades_today, current_day, last_candle_traded, daily_trades_log

    if not is_forex_market_open():
        return

    today = get_ist_now().date()
    if today != current_day:
        current_day = today
        trades_today = 0
        daily_trades_log = []
        last_candle_traded = {name: None for name in FOREX_PAIRS}

    for name, info in FOREX_PAIRS.items():
        try:
            ticker = yf.Ticker(info["symbol"])
            # बड़े टाइमफ्रेम का डेटा: 15-Minute (एंट्री और स्ट्रक्चर) + 1-Hour (मेजर ऑपरेटर डायरेक्शन)
            df_15m = ticker.history(period="10d", interval="15m")
            df_1h  = ticker.history(period="1mo", interval="1h")

            if df_15m.empty or len(df_15m) < 100 or df_1h.empty or len(df_1h) < 50:
                continue

            df_15m = calculate_indicators(df_15m)
            df_1h  = calculate_indicators(df_1h)

            latest_live    = df_15m.iloc[-1] # चालू कैंडल (सिर्फ लाइव प्राइस और एक्टिव ट्रेड SL/Target ट्रैक करने के लिए)
            closed_15m     = df_15m.iloc[-2] # आखिरी पूरी तरह बंद हो चुकी 15M कैंडल (सिग्नल कंफर्मेशन के लिए)
            closed_time    = df_15m.index[-2]

            price = float(latest_live['Close'])
            atr   = float(closed_15m['ATR'])
            dec   = info["decimals"]

            # 1. पहले चल रहे ट्रेड्स को मैनेज करें
            manage_active_forex_trades(name, closed_time, latest_live, closed_15m, dec)

            # 2. अगर इस पेयर में पहले से ट्रेड खुला है तो नया न लें
            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            # 3. 24 घंटे के 3 बेस्ट ट्रेड पूरे हो गए हों तो रुक जाएं
            if trades_today >= MAX_TRADES_PER_DAY:
                continue

            # 4. एक ही 15M क्लोज्ड कैंडल पर दोबारा सिग्नल न दें
            if last_candle_traded[name] == closed_time:
                continue

            signal, direction, strategy_used, custom_sl = evaluate_htf_operator_strategies(df_15m, df_1h)

            if signal and custom_sl is not None:
                # 15M टाइमफ्रेम के हिसाब से सुरक्षित स्टॉप-लॉस (1.1x ATR से 2.2x ATR के बीच)
                raw_sl_dist = abs(price - custom_sl)
                min_sl_dist = atr * 1.1
                max_sl_dist = atr * 2.2
                sl_dist = round(max(min_sl_dist, min(raw_sl_dist, max_sl_dist)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                trades_today += 1

                rr_ratio = 2.4 # बड़े टाइमफ्रेम पर हाई-प्रोबेबिलिटी 1:2.4 रिस्क-रिवॉर्ड
                target_dist = round(sl_dist * rr_ratio, dec)

                if direction == "BUY":
                    sl_level          = round(price - sl_dist, dec)
                    half_target_level = round(price + sl_dist, dec)
                    target_level      = round(price + target_dist, dec)
                else:
                    sl_level          = round(price + sl_dist, dec)
                    half_target_level = round(price - sl_dist, dec)
                    target_level      = round(price - target_dist, dec)

                max_risk_usd  = TOTAL_CAPITAL_USD * (RISK_PER_TRADE_PCT / 100.0) # $10 risk
                expected_prof = max_risk_usd * rr_ratio

                trade_id = trades_today
                daily_trades_log.append({
                    "id": trade_id,
                    "pair": name,
                    "mt5_name": info["mt5_name"],
                    "action": signal,
                    "direction": direction,
                    "strategy": strategy_used,
                    "entry": price,
                    "sl": sl_level,
                    "sl_dist": sl_dist,
                    "half_target": half_target_level,
                    "target": target_level,
                    "max_risk": max_risk_usd,
                    "expected_profit": expected_prof,
                    "entry_candle_time": closed_time,
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
                htf_info        = analyze_htf_1h_structure(df_1h)

                msg = (
                    f"🏦 <b>HTF (1H + 15M) OPERATOR SIGNAL ({trades_today}/{MAX_TRADES_PER_DAY})</b> 🏦\n\n"
                    f"🌍 <b>Pair:</b> <b>{name}</b>\n"
                    f"📊 <b>Timeframe:</b> 1-Hour Trend ({htf_info['bias']}) + 15M Confirmed Close\n\n"
                    f"🧠 <b>Operator Setup:</b> {strategy_used}\n\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n\n"
                    f"🔍 <b>Exness / MT5 Symbol:</b> <code>{info['mt5_name']}</code>\n\n"
                    f"📍 <b>Entry Price:</b> {entry_s}\n"
                    f"🛑 <b>Protected SL:</b> {sl_s}  <i>({sl_pips_str} का गैप)</i>\n"
                    f"🛡️ <b>Breakeven (1:1):</b> {half_s}\n"
                    f"🎯 <b>Full Target (1:2.4):</b> {target_s}  <i>({target_pips_str} का टारगेट)</i>\n\n"
                    f"💼 <b>Recommended Lot:</b> 0.01 - 0.02 Lot (Max Risk: ${max_risk_usd:.0f} / ~₹{max_risk_usd*85:.0f})\n\n"
                    f"⏳ <b>Pro Guard:</b> यह सिग्नल 15-मिनट की कैंडल पूरी तरह क्लोज होने और 1-Hour स्ट्रक्चर मैच होने के बाद ही दिया गया है!\n\n"
                    f"────────────────────\n\n"
                    f"{get_trades_scoreboard()}"
                )
                send_telegram_message(msg)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    mkt_msg = "🟢 फॉरेक्स मार्केट खुला है (1H + 15M मल्टी-टाइमफ्रेम स्कैनिंग चालू है)।" if is_forex_market_open() else "🔴 अभी वीकेंड की वजह से ग्लोबल फॉरेक्स मार्केट बंद है। सोमवार सुबह से अपने आप ट्रेड आएंगे।"

    send_telegram_message(
        "🏦 <b>Forex HTF (1H + 15M) Operator Bot Active!</b>\n\n"
        f"📢 <b>Status:</b> {mkt_msg}\n\n"
        "💎 <b>Pairs Monitored:</b>\n"
        "• XAU/USD (Gold)\n"
        "• EUR/USD, GBP/USD, USD/JPY, GBP/JPY\n\n"
        "🐋 <b>4 New Multi-Timeframe Strategies (Confirmed Close Only):</b>\n"
        "1️⃣ 1H Order Flow + 15M Judas Swing Sweep (Discount/Premium)\n"
        "2️⃣ 15M Unmitigated Order Block + FVG Mitigation\n"
        "3️⃣ 6-Hour Session AMD Manipulation Reversal\n"
        "4️⃣ 1H+15M Institutional Displacement & Structure Break"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 25:
            scan_forex_market()
            last_scan_time = time.time()
        time.sleep(3)
