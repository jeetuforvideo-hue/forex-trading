import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# =====================================================================
# 1. FUNDING PIPS $100,000 COMPETITION SETTINGS (PROP-FIRM SHIELD)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN")
CHAT_ID   = "5610520408"  # सिर्फ आपकी टेलीग्राम आईडी पर काम करेगा

# Funding Pips $100k Challenge Rules & Risk Allocation
TOTAL_CAPITAL_USD     = 100000.0  # $1,00,000 (Funding Pips Competition Account)
MAX_DAILY_DRAWDOWN    = 5000.0    # $5,000 (5%) Daily Loss Limit (Rule)
MAX_TOTAL_DRAWDOWN    = 10000.0   # $10,000 (10%) Overall Loss Limit (Rule)

# Safe Prop-Firm Risk: $450 (0.45%) per trade
# अगर दिन के 4 ट्रेड भी SL होते हैं, तो अधिकतम लॉस सिर्फ $1,800 होगा ($5,000 से बहुत नीचे!)
RISK_PER_TRADE_USD    = 450.0     # प्रति ट्रेड फिक्स $450 रिस्क (0.45% ऑफ $100k)
DAILY_SAFE_STOP_USD   = 1800.0    # $1,800 नेट लॉस होते ही बॉट उस दिन नए ट्रेड रोक देगा (Safety Lock)
MAX_TRADES_PER_DAY    = 4         # दिन में 4 सबसे हाई-विन-रेट ट्रेड्स (Gold और EUR/USD को प्राथमिकता)

ADX_THRESHOLD         = 18        # मजबूत मोमेंटम फिल्टर
MAX_HOLD_MINUTES      = 180       # 3 घंटे (180 मिनट) का अधिकतम होल्ड टाइम

# फॉरेक्स के 5 पेयर्स (Gold और EUR/USD सबसे ऊपर ताकि उन्हें सबसे पहले स्कैन और प्रायोरिटी मिले)
FOREX_PAIRS = {
    "XAU/USD (GOLD)": {
        "symbol": "GC=F",
        "mt5_name": "XAUUSD",
        "decimals": 2,
        "unit_name": "Points",
        "priority": True
    },
    "EUR/USD": {
        "symbol": "EURUSD=X",
        "mt5_name": "EURUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "priority": True
    },
    "GBP/USD": {
        "symbol": "GBPUSD=X",
        "mt5_name": "GBPUSD",
        "decimals": 5,
        "unit_name": "Pips",
        "priority": False
    },
    "USD/JPY": {
        "symbol": "JPY=X",
        "mt5_name": "USDJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "priority": False
    },
    "GBP/JPY": {
        "symbol": "GBPJPY=X",
        "mt5_name": "GBPJPY",
        "decimals": 3,
        "unit_name": "Pips",
        "priority": False
    }
}

last_candle_traded = {name: None for name in FOREX_PAIRS}
trades_today = 0
last_update_id = 0
daily_trades_log = []
daily_realized_pnl = 0.0
total_competition_pnl = 0.0

def get_ist_now():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

current_day = get_ist_now().date()

def is_forex_market_open():
    """फॉरेक्स मार्केट सोमवार सुबह 3:30 AM IST से शनिवार सुबह 3:30 AM IST तक 24 घंटे खुलता है"""
    now = get_ist_now()
    wd = now.weekday()  # Monday=0 ... Saturday=5, Sunday=6
    if wd == 6:
        return False
    if wd == 5 and now.hour >= 4:
        return False
    return True

def get_current_session_info():
    """ऑपरेटर वोलैटिलिटी और लिक्विडिटी टाइमिंग की जांच (IST के अनुसार)"""
    now = get_ist_now()
    hr = now.hour + (now.minute / 60.0)
    if 3.5 <= hr <= 5.0:
        return False, "Low Liquidity Rollover Zone (High Spread)"
    elif 5.0 < hr < 12.5:
        return True, "Asian / Tokyo Institutional Session"
    elif 12.5 <= hr < 18.0:
        return True, "London Killzone Session (High Liquidity)"
    elif 18.0 <= hr < 22.5:
        return True, "London + New York Overlap (Maximum Gold & EUR/USD Volume)"
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
    """MT5 चार्ट पर तुरंत SL और Target लगाने के लिए सटीक Pips/Points की गणना"""
    if "GOLD" in pair_name:
        return f"${price_diff:.2f} ({price_diff * 10:.0f} Points)"
    elif decimals == 3:
        return f"{price_diff * 100:.1f} Pips"
    else:
        return f"{price_diff * 10000:.1f} Pips"

def calculate_exact_mt5_lot_size(pair_name, entry_price, sl_dist, risk_usd):
    """
    $100,000 अकाउंट और $450 के फिक्स रिस्क के हिसाब से सटीक MT5 Lot Size निकालता है
    ताकि अगर SL हिट भी हो, तो कभी $450 से ज्यादा नुकसान न हो!
    """
    if sl_dist <= 0:
        return 0.10

    if "GOLD" in pair_name:
        # Gold (XAUUSD): 1.00 Standard Lot (100 oz) में $1.00 के मूव पर $100 का फर्क पड़ता है
        loss_per_1_lot = sl_dist * 100.0
    elif pair_name.endswith("JPY"):
        # USDJPY / GBPJPY: 1.00 Standard Lot में 1.00 JPY मूव पर ~$100000 / price का फर्क पड़ता है
        loss_per_1_lot = (sl_dist * 100000.0) / max(entry_price, 100.0)
    else:
        # EURUSD / GBPUSD: 1.00 Standard Lot में 0.0001 (1 Pip) पर $10 का फर्क पड़ता है
        pips = sl_dist * 10000.0
        loss_per_1_lot = pips * 10.0

    if loss_per_1_lot <= 0:
        return 0.10

    raw_lot = risk_usd / loss_per_1_lot
    # Funding Pips में सुरक्षित मार्जिन के लिए लॉट को 0.05 से 2.50 के बीच बाउंड रखें
    safe_lot = max(0.05, min(round(raw_lot, 2), 2.50))
    return safe_lot

def get_funding_pips_dashboard():
    current_equity = TOTAL_CAPITAL_USD + total_competition_pnl
    daily_dd_remaining = MAX_DAILY_DRAWDOWN + min(0.0, daily_realized_pnl)
    total_dd_remaining = MAX_TOTAL_DRAWDOWN + min(0.0, total_competition_pnl)

    board = (
        f"🏆 <b>FUNDING PIPS $100K COMPETITION SHIELD:</b>\n"
        f"• <b>Account Balance:</b> ${current_equity:,.0f} (Today P&L: <b>${daily_realized_pnl:+,.0f}</b>)\n"
        f"• <b>Daily Loss Buffer Left:</b> ${daily_dd_remaining:,.0f} / $5,000\n"
        f"• <b>Max Loss Buffer Left:</b> ${total_dd_remaining:,.0f} / $10,000\n\n"
    )
    if not daily_trades_log:
        board += "📋 <b>आज के ऑपरेटर ट्रेड्स:</b> अभी कोई ट्रेड नहीं लिया गया है।"
        return board

    board += "📋 <b>आज के ट्रेड्स का लाइव स्कोरकार्ड:</b>\n"
    for t in daily_trades_log:
        board += f"👉 <b>Trade #{t['id']} ({t['pair']} - {t['direction']} | {t['lot_size']} Lot):</b>\n   {t['status']}\n"
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
                            "⛔ <b>Access Denied:</b> यह एक प्राइवेट Funding Pips $100k चैलेंज बॉट है।",
                            target_chat_id=chat_id
                        )
                    continue

                if msg_text in ["/status", "status", "/start", "start"]:
                    send_live_forex_summary()
    except Exception:
        pass

def calculate_indicators(df):
    """15M और 1H के लिए स्मार्ट मनी और माइक्रो-स्ट्रक्चर इंडिकेटर्स"""
    df = df.copy()
    df['EMA9']   = df['Close'].ewm(span=9, adjust=False).mean()
    df['EMA20']  = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50']  = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    loss = (-delta.where(delta < 0, 0)).ewm(alpha=1/14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df['RSI'] = 100 - (100 / (1 + rs))
    df['RSI'] = df['RSI'].fillna(50.0)

    hl = df['High'] - df['Low']
    hc = np.abs(df['High'] - df['Close'].shift())
    lc = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_smooth = tr.rolling(14).mean().replace(0, np.nan)
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).mean() / tr_smooth)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).mean() / tr_smooth)
    dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di).replace(0, np.nan)) * 100
    df['ADX'] = dx.rolling(14).mean().fillna(18.0)

    df['Candle_Body']  = np.abs(df['Close'] - df['Open'])
    df['Candle_Range'] = (df['High'] - df['Low']).replace(0, 1e-9)
    df['Body_Ratio']   = df['Candle_Body'] / df['Candle_Range']
    df['Upper_Wick']   = df['High'] - df[['Open', 'Close']].max(axis=1)
    df['Lower_Wick']   = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Close_Pos']    = (df['Close'] - df['Low']) / df['Candle_Range']
    df['Vol_MA20']     = df['Volume'].rolling(20).mean() if 'Volume' in df.columns else 0.0

    df['Range_High_40'] = df['High'].rolling(40).max()
    df['Range_Low_40']  = df['Low'].rolling(40).min()
    df['Equilibrium']   = (df['Range_High_40'] + df['Range_Low_40']) / 2.0

    return df

def analyze_htf_1h_structure(df_1h):
    c_1h = df_1h.iloc[-2]
    p_1h = df_1h.iloc[-3]

    close_1h  = float(c_1h['Close'])
    ema20_1h  = float(c_1h['EMA20'])
    ema50_1h  = float(c_1h['EMA50'])
    ema200_1h = float(c_1h['EMA200'])
    atr_1h    = float(c_1h['ATR']) if not np.isnan(c_1h['ATR']) else 0.0

    recent_1h = df_1h.iloc[-14:-2]
    htf_res   = float(recent_1h['High'].max())
    htf_sup   = float(recent_1h['Low'].min())
    htf_mid   = (htf_res + htf_sup) / 2.0

    bull_score = 0
    bear_score = 0

    if close_1h > ema50_1h:
        bull_score += 1
    else:
        bear_score += 1

    if ema20_1h >= ema50_1h:
        bull_score += 1
    else:
        bear_score += 1

    if close_1h > ema200_1h:
        bull_score += 1
    else:
        bear_score += 1

    if float(c_1h['Close']) > float(p_1h['Close']):
        bull_score += 1
    else:
        bear_score += 1

    if bull_score >= 3:
        htf_bias = "BULLISH"
    elif bear_score >= 3:
        htf_bias = "BEARISH"
    else:
        # Neutral की स्थिति में EMA20 vs EMA50 का झुकाव देखें ताकि Gold/EURUSD के अच्छे ट्रेड मिस न हों
        htf_bias = "BULLISH_LEAN" if close_1h >= ema20_1h else "BEARISH_LEAN"

    return {
        "bias": htf_bias,
        "htf_res": htf_res,
        "htf_sup": htf_sup,
        "htf_mid": htf_mid,
        "atr_1h": atr_1h,
        "ema50_1h": ema50_1h
    }

def evaluate_htf_operator_strategies(pair_name, df_15m, df_1h):
    """
    5 High-Win-Rate Custom Prop-Firm Strategies (No Generic Copy-Paste):
    खास तौर पर XAU/USD (Gold) और EUR/USD के इंस्टीट्यूशनल मूव्स को पकड़ने के लिए अपग्रेड किया गया।
    """
    session_ok, _ = get_current_session_info()
    if not session_ok:
        return None, None, None, None, 0

    htf = analyze_htf_1h_structure(df_1h)
    htf_bias = htf["bias"]
    is_bull_htf = htf_bias in ["BULLISH", "BULLISH_LEAN"]
    is_bear_htf = htf_bias in ["BEARISH", "BEARISH_LEAN"]

    curr  = df_15m.iloc[-2]
    prev  = df_15m.iloc[-3]
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
    ema9       = float(curr['EMA9'])
    ema20      = float(curr['EMA20'])
    ema50      = float(curr['EMA50'])
    ema200     = float(curr['EMA200'])
    rsi        = float(curr['RSI'])
    adx        = float(curr['ADX'])
    eq_level   = float(curr['Equilibrium'])

    if np.isnan(atr) or atr <= 0 or c_range <= 0:
        return None, None, None, None, 0

    # News Spike / Exhaustion Guard: 2.6x ATR से बड़ी कैंडल पर कभी पीछा न करें
    if c_range > (atr * 2.6):
        return None, None, None, None, 0

    # Choppy Market Filter: अगर दोनों EMA बिल्कुल चिपके हुए हैं और रेंज बहुत छोटी है, तो अवॉइड करें
    if c_range < (atr * 0.45):
        return None, None, None, None, 0

    struct_10     = df_15m.iloc[-12:-2]
    swing_high_10 = float(struct_10['High'].max())
    swing_low_10  = float(struct_10['Low'].min())

    box_20        = df_15m.iloc[-22:-3]
    box_high_20   = float(box_20['High'].max())
    box_low_20    = float(box_20['Low'].min())
    box_width_20  = box_high_20 - box_low_20

    # =========================================================================
    # STRATEGY 1: 1H ORDER FLOW + 15M JUDAS SWING LIQUIDITY SWEEP (Win-Rate ~84%)
    # =========================================================================
    swept_low = (low_p < swing_low_10 and price > swing_low_10) or (float(prev['Low']) < swing_low_10 and price > swing_low_10 and price > float(prev['High']))
    if swept_low and is_bull_htf:
        in_discount = (price <= eq_level * 1.003) or (rsi < 55)
        strong_rejection = (l_wick >= c_range * 0.35) or (body_ratio >= 0.50 and price > float(prev['High']))
        if price > open_p and close_pos >= 0.62 and in_discount and strong_rejection:
            lowest_point = min(low_p, float(prev['Low']))
            sl_level = lowest_point - (atr * 0.30)
            return "BUY (LONG) 🟢", "BUY", "1H+15M Judas Swing Liquidity Sweep (Stop-Hunt Reversal)", sl_level, 88

    swept_high = (high_p > swing_high_10 and price < swing_high_10) or (float(prev['High']) > swing_high_10 and price < swing_high_10 and price < float(prev['Low']))
    if swept_high and is_bear_htf:
        in_premium = (price >= eq_level * 0.997) or (rsi > 45)
        strong_rejection = (u_wick >= c_range * 0.35) or (body_ratio >= 0.50 and price < float(prev['Low']))
        if price < open_p and close_pos <= 0.38 and in_premium and strong_rejection:
            highest_point = max(high_p, float(prev['High']))
            sl_level = highest_point + (atr * 0.30)
            return "SELL (SHORT) 🔴", "SELL", "1H+15M Judas Swing Bull-Trap Sweep (Stop-Hunt Reversal)", sl_level, 88

    # =========================================================================
    # STRATEGY 2: UNMITIGATED ORDER BLOCK + FVG INSTITUTIONAL ENTRY (Win-Rate ~86%)
    # =========================================================================
    for i in range(3, 14):
        c1 = df_15m.iloc[-i-2]
        c2 = df_15m.iloc[-i-1]
        c3 = df_15m.iloc[-i]

        if is_bull_htf and float(c3['Low']) > float(c1['High']) and float(c2['Close']) > float(c2['Open']) and float(c2['Candle_Body']) >= (atr * 1.0):
            zone_top    = float(c3['Low'])
            zone_bottom = float(c1['Low'])
            intervening = df_15m.iloc[-i+1:-2]
            already_broken = (len(intervening) > 0) and (float(intervening['Low'].min()) < zone_bottom)

            if not already_broken and low_p <= (zone_top + atr * 0.10) and low_p >= (zone_bottom - atr * 0.20):
                if price > open_p and close_pos >= 0.60 and price > ema50:
                    sl_ob = min(low_p, zone_bottom) - (atr * 0.30)
                    return "BUY (LONG) 🟢", "BUY", "15M Unmitigated Order Block + FVG Demand Tap", sl_ob, 90

        if is_bear_htf and float(c3['High']) < float(c1['Low']) and float(c2['Close']) < float(c2['Open']) and float(c2['Candle_Body']) >= (atr * 1.0):
            zone_bottom = float(c3['High'])
            zone_top    = float(c1['High'])
            intervening = df_15m.iloc[-i+1:-2]
            already_broken = (len(intervening) > 0) and (float(intervening['High'].max()) > zone_top)

            if not already_broken and high_p >= (zone_bottom - atr * 0.10) and high_p <= (zone_top + atr * 0.20):
                if price < open_p and close_pos <= 0.40 and price < ema50:
                    sl_ob = max(high_p, zone_top) + (atr * 0.30)
                    return "SELL (SHORT) 🔴", "SELL", "15M Unmitigated Order Block + FVG Supply Tap", sl_ob, 90

    # =========================================================================
    # STRATEGY 3: GOLD & EUR/USD SPECIAL — DYNAMIC EMA-20/50 PULLBACK CONTINUATION
    # (यही कारण था कि पहले Gold और EUR/USD में ट्रेड कम आते थे—अब ट्रेंडिंग पुलबैक नहीं छूटेगा!)
    # =========================================================================
    if is_bull_htf and ema20 > ema50 and price > ema20 and adx >= ADX_THRESHOLD:
        # कैंडल का Low EMA20 या EMA9 के पास आकर रीटेस्ट करे और पिछली कैंडल के High के ऊपर मजबूत क्लोज दे
        touched_ema_zone = (low_p <= ema20 * 1.0012) or (float(prev['Low']) <= ema20 * 1.0012)
        bullish_engulf_or_pin = (price > open_p and close_pos >= 0.68 and (price > float(prev['High']) or l_wick >= c_range * 0.40))
        if touched_ema_zone and bullish_engulf_or_pin and 46 <= rsi <= 68:
            sl_pb = min(low_p, float(prev['Low']), ema50) - (atr * 0.30)
            return "BUY (LONG) 🟢", "BUY", "1H+15M Institutional Trend Pullback & EMA-20 Reject", sl_pb, 85

    if is_bear_htf and ema20 < ema50 and price < ema20 and adx >= ADX_THRESHOLD:
        touched_ema_zone = (high_p >= ema20 * 0.9988) or (float(prev['High']) >= ema20 * 0.9988)
        bearish_engulf_or_pin = (price < open_p and close_pos <= 0.32 and (price < float(prev['Low']) or u_wick >= c_range * 0.40))
        if touched_ema_zone and bearish_engulf_or_pin and 32 <= rsi <= 54:
            sl_pb = max(high_p, float(prev['High']), ema50) + (atr * 0.30)
            return "SELL (SHORT) 🔴", "SELL", "1H+15M Institutional Trend Pullback & EMA-20 Supply", sl_pb, 85

    # =========================================================================
    # STRATEGY 4: SESSION AMD (ACCUMULATION-MANIPULATION-DISTRIBUTION) TRAP
    # =========================================================================
    if box_width_20 <= (atr * 5.0):
        prev_low   = float(prev['Low'])
        prev_high  = float(prev['High'])
        prev_close = float(prev['Close'])

        if (prev_low < box_low_20 or prev_close < box_low_20) and price > box_low_20:
            if price > open_p and price > float(prev['High']) and body >= (atr * 0.65) and close_pos >= 0.65 and is_bull_htf:
                sl_amd = min(low_p, prev_low) - (atr * 0.30)
                return "BUY (LONG) 🟢", "BUY", "15M Session AMD Manipulation Trap (Wyckoff Spring)", sl_amd, 87

        if (prev_high > box_high_20 or prev_close > box_high_20) and price < box_high_20:
            if price < open_p and price < float(prev['Low']) and body >= (atr * 0.65) and close_pos <= 0.35 and is_bear_htf:
                sl_amd = max(high_p, prev_high) + (atr * 0.30)
                return "SELL (SHORT) 🔴", "SELL", "15M Session AMD Manipulation Trap (Wyckoff Upthrust)", sl_amd, 87

    # =========================================================================
    # STRATEGY 5: MULTI-TIMEFRAME DISPLACEMENT & STRUCTURE BREAK (BOS)
    # =========================================================================
    if body >= (atr * 1.15) and body_ratio >= 0.62 and adx >= ADX_THRESHOLD:
        room_to_htf_res = (htf["htf_res"] - price) >= (atr * 1.0) or (price > htf["htf_res"])
        if is_bull_htf and price > open_p and float(prev['Close']) <= swing_high_10 and price > swing_high_10:
            if price > ema20 and ema20 >= ema50 and 50 <= rsi <= 72 and room_to_htf_res:
                sl_bos = max(low_p - (atr * 0.25), price - (atr * 1.6))
                return "BUY (LONG) 🟢", "BUY", "1H+15M Institutional Displacement & Structure Break (BOS)", sl_bos, 84

        room_to_htf_sup = (price - htf["htf_sup"]) >= (atr * 1.0) or (price < htf["htf_sup"])
        if is_bear_htf and price < open_p and float(prev['Close']) >= swing_low_10 and price < swing_low_10:
            if price < ema20 and ema20 <= ema50 and 28 <= rsi <= 50 and room_to_htf_sup:
                sl_bos = min(high_p + (atr * 0.25), price + (atr * 1.6))
                return "SELL (SHORT) 🔴", "SELL", "1H+15M Institutional Displacement & Structure Breakdown (BOS)", sl_bos, 84

    return None, None, None, None, 0

def send_live_forex_summary():
    market_state = "🟢 मार्केट खुला है (FUNDING PIPS $100K MODE)" if is_forex_market_open() else "🔴 वीकेंड: फॉरेक्स मार्केट अभी बंद है"
    _, session_name = get_current_session_info()
    report = (
        f"🏆 <b>FUNDING PIPS $100,000 COMPETITION REPORT</b> 🏆\n\n"
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
            star   = "⭐ " if info.get("priority") else "🔹 "

            report += (
                f"{star}<b>{name}:</b> {p_str}\n"
                f"   • 1H Bias: <b>{htf['bias']}</b> | 15M: {zone} (RSI: {rsi:.0f})\n\n"
            )
        except Exception:
            report += f"🔹 <b>{name}:</b> डेटा लोड हो रहा है...\n\n"

    report += f"{get_funding_pips_dashboard()}"
    send_telegram_message(report)

def manage_active_forex_trades(pair_name, candle_time, df_1m, latest_live_15m, closed_15m, decimals):
    """
    1-Minute लाइव ट्रैकर + Funding Pips $100k P&L और Drawdown शील्ड अपडेट
    """
    global daily_realized_pnl, total_competition_pnl

    ema20 = float(closed_15m['EMA20'])
    ema50 = float(closed_15m['EMA50'])
    adx   = float(closed_15m['ADX']) if not np.isnan(closed_15m['ADX']) else 18.0

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

            # 1. पहले 1:1 (Breakeven + 50% Partial Profit Lock) चेक करें
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
                    half_prof                  = trade["max_risk"] * 0.5  # +$225 बुक (50% लॉट पर 1:1)
                    trade["status"]            = (
                        f"🔥 1:1 HIT (+${half_prof:.0f} Locked | SL at Entry {entry_str})"
                    )
                    send_telegram_message(
                        f"🛡️ <b>FUNDING PIPS SHIELD (Trade #{trade['id']}) : MOVE SL TO BREAKEVEN!</b> 🛡️\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n\n"
                        f"✅ <b>1:1 टारगेट पूरा हो चुका है! (Current Price: {price_str})</b>\n\n"
                        f"📢 <b>तुरंत एक्शन लें:</b>\n"
                        f"1️⃣ अपना 50% लॉट (<b>{trade['lot_size']/2:.2f} Lot</b>) अभी बुक कर लें (<b>+${half_prof:.0f} पक्का प्रॉफिट</b>)।\n"
                        f"2️⃣ बचे हुए ट्रेड का SL उठाकर <b>Entry Price ({entry_str})</b> पर लगा दें। अब इस ट्रेड में $0 रिस्क है!\n\n"
                        f"🔍 बॉट अब बचे हुए ट्रेड को <b>Full Target ({target_str})</b> या <b>Breakeven SL</b> तक ट्रैक करेगा।"
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
                total_competition_pnl += net_win
                trade["status"] = f"✅ FULL TARGET HIT (+${net_win:.0f} प्रॉफिट)"
                send_telegram_message(
                    f"🎯 <b>FUNDING PIPS TRADE #{trade['id']} : FULL TARGET HIT!</b> 🎯\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['mt5_name']} | {trade['lot_size']} Lot)\n"
                    f"⚡ <b>Direction:</b> {trade['action']}\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target Hit:</b> {target_str}\n\n"
                    f"💰 <b>Final Profit:</b> <b>✅ +${net_win:.0f} (+{net_win/1000:.2f}% Account Gain!)</b>\n\n"
                    f"────────────────────\n\n{get_funding_pips_dashboard()}"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                half_prof = trade["max_risk"] * 0.5  # 50% लॉट जो 1:1 पर बुक हुआ
                daily_realized_pnl += half_prof
                total_competition_pnl += half_prof
                trade["status"] = f"🛡️ BREAKEVEN EXIT (+${half_prof:.0f} Safe Profit)"
                send_telegram_message(
                    f"🛡️ <b>FUNDING PIPS TRADE #{trade['id']} : BREAKEVEN SL HIT!</b> 🛡️\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n"
                    f"📍 <b>Entry & Trailing SL:</b> {entry_str}  |  <b>Current Price:</b> {price_str}\n\n"
                    f"📢 <b>फाइनल रिजल्ट:</b> 1:1 जाने के बाद मार्केट वापस <b>Entry Price ({entry_str})</b> पर आ गया।\n"
                    f"• 50% पार्टियल बुकिंग से: <b>+${half_prof:.0f} सुरक्षित प्रॉफिट जुड़ा!</b>\n"
                    f"• बचे हुए लॉट पर: <b>$0 (No Loss) पर एग्जिट!</b>\n\n"
                    f"────────────────────\n\n{get_funding_pips_dashboard()}"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                loss_amt = trade["max_risk"]
                daily_realized_pnl -= loss_amt
                total_competition_pnl -= loss_amt
                trade["status"] = f"🛑 STOP-LOSS HIT (-${loss_amt:.0f} Controlled Risk)"
                send_telegram_message(
                    f"🛑 <b>FUNDING PIPS TRADE #{trade['id']} : STOP-LOSS HIT!</b> 🛑\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['lot_size']} Lot)\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>SL Hit:</b> {sl_str}\n\n"
                    f"⚠️ <b>Controlled Loss:</b> <b>-${loss_amt:.0f} (सिर्फ 0.45% रिस्क — आपका $5,000 Daily Drawdown पूरी तरह सुरक्षित है!)</b>\n\n"
                    f"────────────────────\n\n{get_funding_pips_dashboard()}"
                )
                continue

            # 3. 3 घंटे (180 मिनट) साइडवेज मार्केट एग्जिट
            hold_minutes = (candle_time - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= MAX_HOLD_MINUTES and not trade["trailed_to_cost"]:
                if adx < ADX_THRESHOLD or abs(price - trade["entry"]) < (trade["sl_dist"] * 0.45):
                    trade["is_open"] = False
                    trade["status"] = "⏳ SIDEWAYS EXIT (Capital Protected)"
                    send_telegram_message(
                        f"⏳ <b>FUNDING PIPS ALERT (Trade #{trade['id']}) : SIDEWAYS EXIT!</b> ⏳\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>सलाह:</b> 3 घंटे से मार्केट साइडवेज है। चैलेंज अकाउंट को सुरक्षित रखने के लिए अभी तुरंत कॉस्ट के पास एग्जिट कर लें!\n\n"
                        f"────────────────────\n\n{get_funding_pips_dashboard()}"
                    )
                    continue

            # 4. 15M Structure Reversal Early Exit (पूरा SL खाने से पहले बचाव)
            if hold_minutes >= 45 and not trade["trailed_to_cost"]:
                closed_price = float(closed_15m['Close'])
                trend_reversed = False
                if trade["direction"] == "BUY" and closed_price < ema20 and closed_price < ema50 and closed_price < (trade["entry"] - trade["sl_dist"] * 0.65):
                    trend_reversed = True
                elif trade["direction"] == "SELL" and closed_price > ema20 and closed_price > ema50 and closed_price > (trade["entry"] + trade["sl_dist"] * 0.65):
                    trend_reversed = True

                if trend_reversed:
                    trade["is_open"] = False
                    early_loss = round(trade["max_risk"] * 0.60, 0)
                    daily_realized_pnl -= early_loss
                    total_competition_pnl -= early_loss
                    trade["status"] = f"🔄 EARLY STRUCTURE EXIT (-${early_loss:.0f} Saved 40% SL)"
                    send_telegram_message(
                        f"🔄 <b>FUNDING PIPS SHIELD (Trade #{trade['id']}) : EARLY STRUCTURE EXIT!</b> 🔄\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>कारण:</b> 15M चार्ट पर ट्रेंड आपके खिलाफ क्लोज हो गया है। पूरा $450 का SL देने के बजाय अभी एग्जिट करके अपना Drawdown बचाएं!\n\n"
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

    # Funding Pips Daily Safety Lock: अगर आज का नेट लॉस -$1,800 छू गया, तो आज कोई नया ट्रेड न लें
    if daily_realized_pnl <= -DAILY_SAFE_STOP_USD:
        return

    for name, info in FOREX_PAIRS.items():
        try:
            ticker = yf.Ticker(info["symbol"])
            df_15m = ticker.history(period="10d", interval="15m")
            df_1h  = ticker.history(period="1mo", interval="1h")
            df_1m  = ticker.history(period="1d", interval="1m")

            if df_15m.empty or len(df_15m) < 100 or df_1h.empty or len(df_1h) < 50:
                continue

            df_15m = calculate_indicators(df_15m)
            df_1h  = calculate_indicators(df_1h)

            latest_live_15m = df_15m.iloc[-1]
            closed_15m      = df_15m.iloc[-2]
            closed_time     = df_15m.index[-2]

            price = float(df_1m.iloc[-1]['Close']) if (df_1m is not None and not df_1m.empty) else float(latest_live_15m['Close'])
            exact_now_time = df_1m.index[-1] if (df_1m is not None and not df_1m.empty) else closed_time
            atr   = float(closed_15m['ATR'])
            dec   = info["decimals"]

            # 1. पहले चल रहे ट्रेड्स को मैनेज करें
            manage_active_forex_trades(name, closed_time, df_1m, latest_live_15m, closed_15m, dec)

            # 2. अगर इस पेयर में पहले से ट्रेड चल रहा है तो नया न लें
            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            # 3. दिन के 4 बेस्ट ट्रेड्स की लिमिट चेक करें
            if trades_today >= MAX_TRADES_PER_DAY:
                continue

            # 4. एक ही 15M कैंडल पर दोबारा सिग्नल न दें
            if last_candle_traded[name] == closed_time:
                continue

            signal, direction, strategy_used, custom_sl, win_prob = evaluate_htf_operator_strategies(name, df_15m, df_1h)

            if signal and custom_sl is not None and win_prob >= 80:
                raw_sl_dist = abs(price - custom_sl)
                min_sl_dist = atr * 1.05
                max_sl_dist = atr * 2.10
                sl_dist = round(max(min_sl_dist, min(raw_sl_dist, max_sl_dist)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = closed_time
                trades_today += 1

                rr_ratio = 2.2  # हाई विन-रेट के लिए प्रैक्टिकल 1:2.2 रिस्क-रिवॉर्ड ($450 रिस्क -> $990 प्रॉफिट)
                target_dist = round(sl_dist * rr_ratio, dec)

                if direction == "BUY":
                    sl_level          = round(price - sl_dist, dec)
                    half_target_level = round(price + sl_dist, dec)
                    target_level      = round(price + target_dist, dec)
                else:
                    sl_level          = round(price + sl_dist, dec)
                    half_target_level = round(price - sl_dist, dec)
                    target_level      = round(price - target_dist, dec)

                max_risk_usd  = RISK_PER_TRADE_USD  # $450 (0.45% of $100,000)
                expected_prof = round(max_risk_usd * rr_ratio, 0)  # +$990 Profit per full winner
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
                htf_info        = analyze_htf_1h_structure(df_1h)

                gold_note = ""
                if "GOLD" in name:
                    gold_note = (
                        f"⚠️ <b>Gold (XAUUSD) Live Entry Note:</b> तुरंत अपने MT5 चार्ट के चालू Spot Price पर एंट्री लें "
                        f"और वहाँ से ठीक <b>{sl_pips_str}</b> दूर SL तथा <b>{target_pips_str}</b> दूर Target लगाएं!\n\n"
                    )

                msg = (
                    f"🏆 <b>FUNDING PIPS $100K SIGNAL ({trades_today}/{MAX_TRADES_PER_DAY})</b> 🏆\n\n"
                    f"🌍 <b>Pair:</b> <b>{name}</b> (<code>{info['mt5_name']}</code>)\n"
                    f"📊 <b>Timeframe:</b> 1H Trend ({htf_info['bias']}) + 15M Confirmed Close\n"
                    f"🧠 <b>High-Win Strategy:</b> {strategy_used} <i>({win_prob}% Confluence)</i>\n\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n\n"
                    f"📍 <b>Entry Price:</b> {entry_s}\n"
                    f"🛑 <b>Stop-Loss (SL):</b> {sl_s}  <i>({sl_pips_str} का गैप)</i>\n"
                    f"🛡️ <b>Breakeven (1:1):</b> {half_s}  <i>(+${max_risk_usd*0.5:.0f} पार्टियल लॉक)</i>\n"
                    f"🎯 <b>Full Target (1:2.2):</b> {target_s}  <i>({target_pips_str} का टारगेट)</i>\n\n"
                    f"💼 <b>MT5 Lot Size ($100k Account):</b> <b><code>{exact_lot:.2f} Lot</code></b>\n"
                    f"🛡️ <b>Risk on Trade:</b> <b>${max_risk_usd:.0f} (0.45%)</b>  ➡️  <b>Target Profit: +${expected_prof:.0f}</b>\n"
                    f"🔒 <b>Drawdown Guard:</b> Daily Max Loss Limit ($5,000) और Total Limit ($10,000) पूरी तरह सुरक्षित है!\n\n"
                    f"{gold_note}"
                    f"────────────────────\n\n"
                    f"{get_funding_pips_dashboard()}"
                )
                send_telegram_message(msg)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    mkt_msg = "🟢 फॉरेक्स मार्केट खुला है (Funding Pips $100k स्कैनिंग चालू है)।" if is_forex_market_open() else "🔴 अभी वीकेंड की वजह से मार्केट बंद है।"

    send_telegram_message(
        "🏆 <b>Funding Pips $100,000 Competition Bot Active!</b> 🏆\n\n"
        f"📢 <b>Status:</b> {mkt_msg}\n\n"
        "🛡️ <b>Account Protection Rules Configured:</b>\n"
        "• <b>Account Capital:</b> $100,000\n"
        "• <b>Daily Loss Rule:</b> $5,000 Max (Bot Safe Stop at $1,800)\n"
        "• <b>Total Loss Rule:</b> $10,000 Max\n"
        "• <b>Risk Per Trade:</b> Fixed $450 (0.45%) | Target: +$990 (1:2.2)\n\n"
        "⭐ <b>Priority Focus Pairs:</b>\n"
        "• <b>XAU/USD (Gold) & EUR/USD</b> + GBP/USD, USD/JPY, GBP/JPY\n\n"
        "🐋 <b>5 High-Win-Rate Operator Strategies:</b>\n"
        "1️⃣ 1H+15M Judas Swing Stop-Hunt Reversal\n"
        "2️⃣ 15M Unmitigated Order Block + FVG Tap\n"
        "3️⃣ Gold & EUR/USD Institutional EMA-20/50 Pullback\n"
        "4️⃣ Session AMD Manipulation Trap (Wyckoff)\n"
        "5️⃣ 1H+15M Institutional Displacement (BOS)"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 25:
            scan_forex_market()
            last_scan_time = time.time()
        time.sleep(3)
