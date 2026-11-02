import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# =====================================================================
# 1. आपकी पर्सनल और सिक्योर सेटिंग्स (SMART MONEY OPERATOR EDITION)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9153301687:AAEO3EFmMW6BhenVqk01NjwbJLHvyvN479o")
CHAT_ID   = "5610520408"  # सिर्फ आपकी टेलीग्राम आईडी पर काम करेगा

TOTAL_CAPITAL_USD  = 1000   # $1,000 के हिसाब से स्टैंडर्ड रिस्क कैलकुलेशन
RISK_PER_TRADE_PCT = 1.0    # प्रति ट्रेड 1% रिस्क ($10)
MAX_TRADES_PER_DAY = 3      # 24 घंटे में सिर्फ 3 सबसे बेस्ट ऑपरेटर ट्रेड्स
ADX_THRESHOLD      = 18     # बेसिक वोलैटिलिटी फिल्टर
MAX_HOLD_MINUTES   = 45     # 45 मिनट में मूव न आए तो साइडवेज एग्जिट

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

def send_telegram_message(message, target_chat_id=CHAT_ID):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": target_chat_id, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Telegram Send Error: {e}")

def format_price(price, decimals):
    return f"{price:.{decimals}f}"

def get_trades_scoreboard():
    if not daily_trades_log:
        return "📋 <b>आज (24h) के ऑपरेटर ट्रेड्स का हाल:</b>\n\n• अभी कोई स्मार्ट मनी ट्रेड नहीं लिया गया है।"
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
    # इंस्टीट्यूशनल ट्रेंड और वोलेटिलिटी मापने के लिए बेसिक टूल्स
    df['EMA20']  = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50']  = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    hl = df['High'] - df['Low']
    hc = np.abs(df['High'] - df['Close'].shift())
    lc = np.abs(df['Low'] - df['Close'].shift())
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    up_move = df['High'] - df['High'].shift(1)
    down_move = df['Low'].shift(1) - df['Low']
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

    tr_smooth = tr.rolling(14).mean()
    plus_di = 100 * (pd.Series(plus_dm, index=df.index).rolling(14).mean() / tr_smooth)
    minus_di = 100 * (pd.Series(minus_dm, index=df.index).rolling(14).mean() / tr_smooth)
    dx = (np.abs(plus_di - minus_di) / (plus_di + minus_di)) * 100
    df['ADX'] = dx.rolling(14).mean()

    # ऑपरेटर कैंडल साइज और वॉल्यूम स्पाइक फिल्टर
    df['Candle_Body']  = np.abs(df['Close'] - df['Open'])
    df['Candle_Range'] = df['High'] - df['Low']
    df['Vol_MA20']     = df['Volume'].rolling(20).mean()
    return df

def evaluate_operator_strategies(df):
    """
    रिटेल इंडिकेटर्स के बजाय 4 शुद्ध ऑपरेटर (Smart Money / SMC) स्ट्रैटेजी:
    Returns: (signal_text, direction, strategy_name, custom_sl_level)
    """
    latest = df.iloc[-1]
    prev   = df.iloc[-2]

    price    = float(latest['Close'])
    open_p   = float(latest['Open'])
    high_p   = float(latest['High'])
    low_p    = float(latest['Low'])
    body     = float(latest['Candle_Body'])
    c_range  = float(latest['Candle_Range'])
    atr      = float(latest['ATR'])
    ema50    = float(latest['EMA50'])
    ema200   = float(latest['EMA200'])
    rsi      = float(latest['RSI'])

    if np.isnan(atr) or atr <= 0 or c_range <= 0:
        return None, None, None, None

    # पिछले 15 कैंडल्स (1 घंटा 15 मिनट) और 30 कैंडल्स (2.5 घंटे) के लिक्विडिटी ज़ोन
    lookback_15 = df.iloc[-16:-1]
    swing_high_15 = float(lookback_15['High'].max())
    swing_low_15  = float(lookback_15['Low'].min())

    lookback_30 = df.iloc[-31:-2]
    major_high_30 = float(lookback_30['High'].max())
    major_low_30  = float(lookback_30['Low'].min())

    upper_wick = high_p - max(open_p, price)
    lower_wick = min(open_p, price) - low_p

    # =========================================================================
    # 1. OPERATOR LIQUIDITY SWEEP & STOP-HUNT REVERSAL (रिटेल स्टॉप-लॉस हंटिंग)
    # नियम: ऑपरेटर पहले स्विंग लो/हाई तोड़कर रिटेलर्स का SL खाते हैं और तुरंत कैंडल अंदर क्लोज करते हैं
    # =========================================================================
    # Bullish Stop-Hunt (नीचे के SL खाकर ऊपर खींचना)
    if low_p < swing_low_15 and price > swing_low_15 and price > open_p:
        if lower_wick >= (c_range * 0.40) and c_range >= (atr * 0.8):
            sl_trap = low_p - (atr * 0.35) # विक के नीचे सुरक्षित SL
            return "BUY (LONG) 🟢", "BUY", "Operator Stop-Hunt & Liquidity Sweep (Bullish)", sl_trap

    # Bearish Stop-Hunt (ऊपर के बायर्स को ट्रैप करके नीचे गिराना)
    if high_p > swing_high_15 and price < swing_high_15 and price < open_p:
        if upper_wick >= (c_range * 0.40) and c_range >= (atr * 0.8):
            sl_trap = high_p + (atr * 0.35) # विक के ऊपर सुरक्षित SL
            return "SELL (SHORT) 🔴", "SELL", "Operator Bull-Trap & Liquidity Sweep (Bearish)", sl_trap

    # =========================================================================
    # 2. SMART MONEY FAIR VALUE GAP (FVG) + ORDER BLOCK MITIGATION
    # नियम: जब ऑपरेटर बड़ी मूव देते हैं तो गैप (FVG) छूटता है। कीमत वापस उस ज़ोन में आने पर एंट्री
    # =========================================================================
    for i in range(3, 9):
        c1 = df.iloc[-i-2]
        c2 = df.iloc[-i-1] # बड़ी ऑपरेटर कैंडल
        c3 = df.iloc[-i]

        # Bullish FVG + Order Block Retest
        if float(c3['Low']) > float(c1['High']) and float(c2['Close']) > float(c2['Open']) and float(c2['Candle_Body']) > (atr * 1.1):
            fvg_top = float(c3['Low'])
            fvg_bottom = float(c1['High'])
            if price > ema200 and low_p <= fvg_top and price >= fvg_bottom and price > open_p and (body / c_range) >= 0.45:
                sl_ob = min(low_p, fvg_bottom) - (atr * 0.4)
                return "BUY (LONG) 🟢", "BUY", "Smart Money FVG + Order Block Mitigation", sl_ob

        # Bearish FVG + Order Block Retest
        if float(c3['High']) < float(c1['Low']) and float(c2['Close']) < float(c2['Open']) and float(c2['Candle_Body']) > (atr * 1.1):
            fvg_bottom = float(c3['High'])
            fvg_top = float(c1['Low'])
            if price < ema200 and high_p >= fvg_bottom and price <= fvg_top and price < open_p and (body / c_range) >= 0.45:
                sl_ob = max(high_p, fvg_top) + (atr * 0.4)
                return "SELL (SHORT) 🔴", "SELL", "Smart Money Bearish FVG + Order Block Supply", sl_ob

    # =========================================================================
    # 3. INSTITUTIONAL DISPLACEMENT & VOLUME INJECTION (Break of Structure - BOS)
    # नियम: बिना बड़ी विक के 1.6x ATR की विशाल ऑपरेटर कैंडल जो पिछले 15 कैंडल के स्ट्रक्चर को तोड़े
    # =========================================================================
    vol_now = float(latest['Volume'])
    vol_avg = float(latest['Vol_MA20']) if not np.isnan(latest['Vol_MA20']) else 0
    vol_surge = (vol_avg == 0) or (vol_now > vol_avg * 1.3)

    if body >= (atr * 1.5) and (body / c_range) >= 0.72 and vol_surge:
        # Bullish Institutional Displacement
        if price > open_p and prev['Close'] <= swing_high_15 and price > swing_high_15 and price > ema50 and rsi < 76:
            sl_disp = low_p - (atr * 0.3)
            return "BUY (LONG) 🟢", "BUY", "Institutional Displacement & BOS (Heavy Buying)", sl_disp

        # Bearish Institutional Displacement
        if price < open_p and prev['Close'] >= swing_low_15 and price < swing_low_15 and price < ema50 and rsi > 24:
            sl_disp = high_p + (atr * 0.3)
            return "SELL (SHORT) 🔴", "SELL", "Institutional Displacement & BOS (Heavy Selling)", sl_disp

    # =========================================================================
    # 4. WYCKOFF SPRING / UPTHRUST TRAP (2-Candle False Breakout Reversal)
    # नियम: पिछली कैंडल ने 2.5 घंटे का रेंज तोड़ा (रिटेलर्स ने ब्रेकआउट ट्रेड लिया), और अगली ही कैंडल ने पूरा पलट दिया
    # =========================================================================
    prev_open  = float(prev['Open'])
    prev_close = float(prev['Close'])
    prev_high  = float(prev['High'])
    prev_low   = float(prev['Low'])

    # Wyckoff Spring (झूठा ब्रेकडाउन करके तेजी से ऊपर भागना)
    if (prev_low < major_low_30 or prev_close < major_low_30) and price > major_low_30:
        if price > open_p and price > prev_high and body >= (atr * 0.7):
            sl_spring = min(low_p, prev_low) - (atr * 0.35)
            return "BUY (LONG) 🟢", "BUY", "Wyckoff Spring Accumulation (Bear Trap Reversal)", sl_spring

    # Wyckoff Upthrust (झूठा ब्रेकआउट दिखाकर तेजी से नीचे गिराना)
    if (prev_high > major_high_30 or prev_close > major_high_30) and price < major_high_30:
        if price < open_p and price < prev_low and body >= (atr * 0.7):
            sl_upthrust = max(high_p, prev_high) + (atr * 0.35)
            return "SELL (SHORT) 🔴", "SELL", "Wyckoff Upthrust Distribution (Bull Trap Reversal)", sl_upthrust

    return None, None, None, None

def send_live_forex_summary():
    market_state = "🟢 ग्लोबल फॉरेक्स मार्केट खुला है (24/5 OPERATOR MODE)" if is_forex_market_open() else "🔴 वीकेंड: फॉरेक्स मार्केट अभी बंद है"
    report = f"🏦 <b>FOREX SMART MONEY (OPERATOR) REPORT</b> 🏦\n\n📢 <b>Status:</b> {market_state}\n\n"

    for name, info in FOREX_PAIRS.items():
        try:
            df = yf.Ticker(info["symbol"]).history(period="5d", interval="5m")
            if df.empty or len(df) < 60:
                continue
            df = calculate_indicators(df)
            latest = df.iloc[-1]
            price = float(latest['Close'])
            rsi = float(latest['RSI'])
            adx = float(latest['ADX']) if not np.isnan(latest['ADX']) else 15.0
            mood = "🕸️ Accumulation (Sideways)" if adx < ADX_THRESHOLD else "🐋 Smart Money Active"
            p_str = format_price(price, info["decimals"])

            report += f"🔹 <b>{name}:</b> {p_str}\n   • ज़ोन: {mood} (RSI: {rsi:.0f})\n\n"
        except Exception:
            report += f"🔹 <b>{name}:</b> डेटा लोड हो रहा है...\n\n"

    report += f"🛡️ <b>आज के कुल ट्रेड:</b> {trades_today} / {MAX_TRADES_PER_DAY}\n\n"
    report += get_trades_scoreboard()
    send_telegram_message(report)

def manage_active_forex_trades(pair_name, candle_time, latest, decimals):
    price        = float(latest['Close'])
    current_high = float(latest['High'])
    current_low  = float(latest['Low'])
    ema20        = float(latest['EMA20'])
    ema50        = float(latest['EMA50'])
    adx          = float(latest['ADX']) if not np.isnan(latest['ADX']) else 15.0

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
                    f"📢 <b>ऑपरेटर सलाह:</b> शानदार! स्मार्ट मनी टारगेट पूरा हुआ, अपना प्रॉफिट बुक कर लें।\n\n"
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
                    f"📢 <b>सलाह:</b> स्टॉप-लॉस हिट हो गया है, अगले स्मार्ट मनी सेटअप का इंतजार करें।\n\n"
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
                        f"📢 <b>एक्शन:</b> अब अपने MT4/MT5 में स्टॉप-लॉस को उठाकर <b>Entry Price ({entry_str})</b> पर लगा लें और चाहें तो 50% लॉट बुक कर लें। अब यह ट्रेड 100% रिस्क-फ्री है!"
                    )

            # 3. प्रो नियम: 45 मिनट तक मार्केट साइडवेज अटक जाए तो एग्जिट
            hold_minutes = (candle_time - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= MAX_HOLD_MINUTES and not trade["trailed_to_cost"]:
                if adx < ADX_THRESHOLD or abs(price - trade["entry"]) < (trade["sl_dist"] * 0.5):
                    trade["is_open"] = False
                    trade["status"] = "⏳ SIDEWAYS CONSOLIDATION EXIT"
                    send_telegram_message(
                        f"⏳ <b>OPERATOR ALERT (Trade #{trade['id']}) : SIDEWAYS MARKET EXIT!</b> ⏳\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>कारण:</b> 45 मिनट हो चुके हैं और ऑपरेटर फॉलो-थ्रू वॉल्यूम नहीं आया है।\n\n"
                        f"📢 <b>सलाह:</b> स्टॉप-लॉस का इंतजार न करें, <b>अभी तुरंत कॉस्ट या छोटे प्रॉफिट/लॉस पर ट्रेड क्लोज कर दें!</b>\n\n"
                        f"────────────────────\n\n{get_trades_scoreboard()}"
                    )
                    continue

            # 4. प्रो नियम: अचानक ऑपरेटर स्ट्रक्चर पलट जाए
            if hold_minutes >= 20:
                trend_reversed = False
                if trade["direction"] == "BUY" and price < ema20 and price < ema50 and price < (trade["entry"] - trade["sl_dist"] * 0.6):
                    trend_reversed = True
                elif trade["direction"] == "SELL" and price > ema20 and price > ema50 and price > (trade["entry"] + trade["sl_dist"] * 0.6):
                    trend_reversed = True

                if trend_reversed:
                    trade["is_open"] = False
                    trade["status"] = "🔄 STRUCTURE SHIFT EXIT (जल्दी बचाव)"
                    send_telegram_message(
                        f"🔄 <b>OPERATOR ALERT (Trade #{trade['id']}) : STRUCTURE SHIFT EXIT!</b> 🔄\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>कारण:</b> ऑपरेटर फ्लो आपके ट्रेड के खिलाफ मुड़ गया है।\n\n"
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
            df = yf.Ticker(info["symbol"]).history(period="5d", interval="5m")
            if df.empty or len(df) < 200:
                continue

            df = calculate_indicators(df)
            latest = df.iloc[-1]
            candle_time = df.index[-1]

            price = float(latest['Close'])
            atr   = float(latest['ATR'])
            dec   = info["decimals"]

            # 1. पहले चल रहे ट्रेड्स को मैनेज करें
            manage_active_forex_trades(name, candle_time, latest, dec)

            # 2. अगर इस पेयर में पहले से ट्रेड खुला है तो नया न लें
            if any(t["pair"] == name and t["is_open"] for t in daily_trades_log):
                continue

            # 3. 24 घंटे के 3 बेस्ट ट्रेड पूरे हो गए हों तो रुक जाएं
            if trades_today >= MAX_TRADES_PER_DAY:
                continue

            # 4. एक ही कैंडल पर दोबारा सिग्नल न दें
            if last_candle_traded[name] == candle_time:
                continue

            signal, direction, strategy_used, custom_sl = evaluate_operator_strategies(df)

            if signal and custom_sl is not None:
                # स्टॉप-लॉस की दूरी को लिक्विडिटी विक के हिसाब से सेट करना (कम से कम 1.2 ATR और अधिकतम 2.5 ATR)
                raw_sl_dist = abs(price - custom_sl)
                min_sl_dist = atr * 1.2
                max_sl_dist = atr * 2.5
                sl_dist = round(max(min_sl_dist, min(raw_sl_dist, max_sl_dist)), dec)

                if sl_dist <= 0:
                    continue

                last_candle_traded[name] = candle_time
                trades_today += 1

                rr_ratio = 2.5 # ऑपरेटर ट्रेड्स में 1:2.5 का बड़ा रिस्क-रिवॉर्ड
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
                    "entry_candle_time": candle_time,
                    "trailed_to_cost": False,
                    "is_open": True,
                    "status": "⏳ RUNNING (चल रहा है)"
                })

                entry_s  = format_price(price, dec)
                sl_s     = format_price(sl_level, dec)
                half_s   = format_price(half_target_level, dec)
                target_s = format_price(target_level, dec)

                msg = (
                    f"🏦 <b>SMART MONEY OPERATOR SIGNAL ({trades_today}/{MAX_TRADES_PER_DAY})</b> 🏦\n\n"
                    f"🌍 <b>Pair:</b> <b>{name}</b>\n\n"
                    f"🧠 <b>Operator Setup:</b> {strategy_used}\n\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n\n"
                    f"🔍 <b>MT4/MT5 Symbol:</b> <code>{info['mt5_name']}</code>\n\n"
                    f"📍 <b>Entry Price:</b> {entry_s}\n\n"
                    f"🛑 <b>Liquidity Protected SL:</b> {sl_s}\n\n"
                    f"🛡️ <b>Breakeven Level (1:1):</b> {half_s}\n\n"
                    f"🎯 <b>Operator Target (1:2.5):</b> {target_s}\n\n"
                    f"💼 <b>Recommended Lot:</b> 0.01 - 0.02 Lot (Max Risk: ${max_risk_usd:.0f} / ~₹{max_risk_usd*85:.0f})\n\n"
                    f"⏳ <b>Pro Guard:</b> स्टॉप-लॉस को स्टॉप-हंट विक के पीछे रखा गया है। 1:1 आने पर SL कॉस्ट पर आ जाएगा!\n\n"
                    f"────────────────────\n\n"
                    f"{get_trades_scoreboard()}"
                )
                send_telegram_message(msg)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    mkt_msg = "🟢 फॉरेक्स मार्केट खुला है (24/5 स्मार्ट मनी स्कैनिंग चालू है)।" if is_forex_market_open() else "🔴 अभी वीकेंड की वजह से ग्लोबल फॉरेक्स मार्केट बंद है। सोमवार सुबह से अपने आप ट्रेड आएंगे।"

    send_telegram_message(
        "🏦 <b>Forex Smart Money (Operator) Bot Active!</b>\n\n"
        f"📢 <b>Status:</b> {mkt_msg}\n\n"
        "💎 <b>Pairs Monitored:</b>\n"
        "• XAU/USD (Gold)\n"
        "• EUR/USD, GBP/USD, USD/JPY, GBP/JPY\n\n"
        "🐋 <b>4 Operator Strategies Active:</b>\n"
        "1️⃣ Liquidity Sweep & Stop-Hunt Reversal\n"
        "2️⃣ Fair Value Gap (FVG) + Order Block Fill\n"
        "3️⃣ Institutional Displacement & BOS\n"
        "4️⃣ Wyckoff Spring / Upthrust Trap"
    )

    start_run = time.time()
    last_scan_time = 0
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 20:
            scan_forex_market()
            last_scan_time = time.time()
        time.sleep(3)
