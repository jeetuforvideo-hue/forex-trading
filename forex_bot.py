import os
import time
import requests
import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone

# =====================================================================
# 1. आपकी पर्सनल और सिक्योर सेटिंग्स (FOREX PRO EDITION)
# =====================================================================
BOT_TOKEN = os.getenv("BOT_TOKEN", "9153301687:AAEO3EFmMW6BhenVqk01NjwbJLHvyvN479o")
CHAT_ID   = "5610520408"  # सिर्फ आपकी टेलीग्राम आईडी पर काम करेगा[span_0](start_span)[span_0](end_span)

TOTAL_CAPITAL_USD  = 1000   # $1,000 (लगभग ₹85,000) के हिसाब से स्टैंडर्ड रिस्क कैलकुलेशन
RISK_PER_TRADE_PCT = 1.0    # प्रति ट्रेड 1% रिस्क ($10)
MAX_TRADES_PER_DAY = 3      # 24 घंटे में सिर्फ 3 सबसे बेस्ट ट्रेड्स
ADX_THRESHOLD      = 20     # साइडवेज मार्केट फिल्टर
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
        return "📋 <b>आज (24h) के फॉरेक्स ट्रेड्स का हाल:</b>\n\n• अभी कोई ट्रेड नहीं लिया गया है।"
    board = "📋 <b>आज के तीनों फॉरेक्स ट्रेड्स का रिजल्ट:</b>\n\n"
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
    df['EMA20']  = df['Close'].ewm(span=20, adjust=False).mean()
    df['EMA50']  = df['Close'].ewm(span=50, adjust=False).mean()
    df['EMA200'] = df['Close'].ewm(span=200, adjust=False).mean()

    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    ema12 = df['Close'].ewm(span=12, adjust=False).mean()
    ema26 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = ema12 - ema26
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()

    df['BB_Mid']   = df['Close'].rolling(window=20).mean()
    df['BB_Std']   = df['Close'].rolling(window=20).std()
    df['BB_Upper'] = df['BB_Mid'] + (2 * df['BB_Std'])
    df['BB_Lower'] = df['BB_Mid'] - (2 * df['BB_Std'])

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
    return df

def is_strong_candle(latest, direction):
    """फेक ब्रेकआउट (Stop-Hunt Wick) से बचने का नियम"""
    high_p  = float(latest['High'])
    low_p   = float(latest['Low'])
    open_p  = float(latest['Open'])
    close_p = float(latest['Close'])

    total_range = high_p - low_p
    if total_range <= 0:
        return False

    body = abs(close_p - open_p)
    if (body / total_range) < 0.45:
        return False

    if direction == "BUY":
        upper_wick = high_p - close_p
        return close_p > open_p and (upper_wick / total_range) < 0.30
    else:
        lower_wick = close_p - low_p
        return close_p < open_p and (lower_wick / total_range) < 0.30

def evaluate_forex_strategies(df):
    latest = df.iloc[-1]
    prev   = df.iloc[-2]

    price    = float(latest['Close'])
    high_p   = float(latest['High'])
    low_p    = float(latest['Low'])
    ema20    = float(latest['EMA20'])
    ema50    = float(latest['EMA50'])
    ema200   = float(latest['EMA200'])
    rsi      = float(latest['RSI'])
    macd     = float(latest['MACD'])
    macd_sig = float(latest['MACD_Signal'])
    bb_upper = float(latest['BB_Upper'])
    bb_lower = float(latest['BB_Lower'])
    atr      = float(latest['ATR'])

    if np.isnan(atr) or atr <= 0 or (high_p - low_p) < (atr * 0.5):
        return None, None, None

    # पिछले 1 घंटे (12 कैंडल्स) का High और Low (Session Liquidity Breakout के लिए)
    recent_12 = df.iloc[-13:-1]
    box_high = float(recent_12['High'].max())
    box_low  = float(recent_12['Low'].min())

    # 1. Institutional Triple EMA Trend + MACD
    if price > ema20 and ema20 > ema50 and ema50 > ema200 and 55 < rsi < 72 and macd > macd_sig:
        if is_strong_candle(latest, "BUY"):
            return "BUY (LONG) 🟢", "BUY", "Institutional EMA 20/50/200 + MACD Trend"
    if price < ema20 and ema20 < ema50 and ema50 < ema200 and 28 < rsi < 45 and macd < macd_sig:
        if is_strong_candle(latest, "SELL"):
            return "SELL (SHORT) 🔴", "SELL", "Institutional EMA 20/50/200 + MACD Downtrend"

    # 2. Session Liquidity Box Breakout (1-Hour High/Low Break)
    if prev['Close'] <= box_high and price > box_high and price > ema50 and rsi > 58:
        if is_strong_candle(latest, "BUY"):
            return "BUY (LONG) 🟢", "BUY", "Session 1H Liquidity High Breakout"
    if prev['Close'] >= box_low and price < box_low and price < ema50 and rsi < 42:
        if is_strong_candle(latest, "SELL"):
            return "SELL (SHORT) 🔴", "SELL", "Session 1H Liquidity Low Breakdown"

    # 3. Bollinger Band Momentum Breakout
    if prev['Close'] <= prev['BB_Upper'] and price > bb_upper and rsi > 60 and macd > macd_sig:
        if is_strong_candle(latest, "BUY"):
            return "BUY (LONG) 🟢", "BUY", "Bollinger Band Volatility Breakout"
    if prev['Close'] >= prev['BB_Lower'] and price < bb_lower and rsi < 40 and macd < macd_sig:
        if is_strong_candle(latest, "SELL"):
            return "SELL (SHORT) 🔴", "SELL", "Bollinger Band Volatility Breakdown"

    # 4. 20 EMA + 50 EMA Golden Pullback Bounce
    if ema20 > ema50 and ema50 > ema200 and low_p <= ema20 and price > ema20 and 50 < rsi < 66:
        if is_strong_candle(latest, "BUY"):
            return "BUY (LONG) 🟢", "BUY", "20 EMA Institutional Pullback Bounce"
    if ema20 < ema50 and ema50 < ema200 and high_p >= ema20 and price < ema20 and 34 < rsi < 50:
        if is_strong_candle(latest, "SELL"):
            return "SELL (SHORT) 🔴", "SELL", "20 EMA Institutional Resistance Drop"

    return None, None, None

def send_live_forex_summary():
    market_state = "🟢 ग्लोबल फॉरेक्स मार्केट खुला है (24/5 LIVE)" if is_forex_market_open() else "🔴 वीकेंड: फॉरेक्स मार्केट अभी बंद है"
    report = f"🌍 <b>FOREX & GOLD PRO STATUS REPORT</b> 🌍\n\n📢 <b>Status:</b> {market_state}\n\n"

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
            mood = "⚠️ साइडवेज (No Trade)" if adx < ADX_THRESHOLD else "🔥 ट्रेंडिंग (Active)"
            p_str = format_price(price, info["decimals"])

            report += f"🔹 <b>{name}:</b> {p_str}\n   • मूड: {mood} (RSI: {rsi:.0f})\n\n"
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
                    f"🎯 <b>FOREX TRADE #{trade['id']} : FULL TARGET HIT!</b> 🎯\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']} ({trade['mt5_name']})\n\n"
                    f"⚡ <b>Direction:</b> {trade['action']}\n\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Target Hit:</b> {target_str}\n\n"
                    f"💰 <b>Result:</b> <b>✅ +${trade['expected_profit']:.0f} (लगभग +₹{trade['expected_profit']*85:.0f}) प्रॉफिट</b>\n\n"
                    f"📢 <b>प्रो सलाह:</b> शानदार! अपना पूरा प्रॉफिट बुक कर लें।\n\n"
                    f"────────────────────\n\n{get_trades_scoreboard()}"
                )
                continue

            elif hit_type == "TRAILING_SL":
                trade["is_open"] = False
                trade["status"] = "🛡️ BREAKEVEN EXIT ($0 No Loss)"
                send_telegram_message(
                    f"🛡️ <b>FOREX TRADE #{trade['id']} : TRAILING SL HIT (NO LOSS)!</b> 🛡️\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                    f"📢 <b>प्रो अपडेट:</b> मार्केट वापस घूम गया, लेकिन स्टॉप-लॉस कॉस्ट (Breakeven) पर होने की वजह से <b>आपका $0 का नुकसान हुआ!</b>\n\n"
                    f"────────────────────\n\n{get_trades_scoreboard()}"
                )
                continue

            elif hit_type == "SL":
                trade["is_open"] = False
                trade["status"] = f"🛑 STOP-LOSS HIT (-${trade['max_risk']:.0f} लॉस)"
                send_telegram_message(
                    f"🛑 <b>FOREX TRADE #{trade['id']} : STOP-LOSS HIT!</b> 🛑\n\n"
                    f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                    f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>SL Hit:</b> {sl_str}\n\n"
                    f"⚠️ <b>Result:</b> <b>🛑 -${trade['max_risk']:.0f} लॉस</b>\n\n"
                    f"📢 <b>प्रो सलाह:</b> स्टॉप-लॉस हिट हो गया है, ट्रेड से बाहर रहें और अगले सेटअप का इंतजार करें।\n\n"
                    f"────────────────────\n\n{get_trades_scoreboard()}"
                )
                continue

            # 2. प्रो नियम: 1:1 (50% टारगेट) आने पर SL को Breakeven (Cost) पर लाना
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
                        f"🛡️ <b>PRO UPDATE (Trade #{trade['id']}) : MOVE SL TO BREAKEVEN!</b> 🛡️\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"✅ <b>50% टारगेट पूरा हो चुका है! (Current Price: {price_str})</b>\n\n"
                        f"📢 <b>प्रो एक्शन:</b> अब अपने MT4/MT5 में स्टॉप-लॉस को उठाकर <b>Entry Price ({entry_str})</b> पर लगा लें और चाहें तो 50% लॉट बुक कर लें। अब यह ट्रेड 100% रिस्क-फ्री है!"
                    )

            # 3. प्रो नियम: 45 मिनट तक मार्केट साइडवेज अटक जाए तो एग्जिट
            hold_minutes = (candle_time - trade["entry_candle_time"]).total_seconds() / 60.0
            if hold_minutes >= MAX_HOLD_MINUTES and not trade["trailed_to_cost"]:
                if adx < ADX_THRESHOLD or abs(price - trade["entry"]) < (trade["sl_dist"] * 0.5):
                    trade["is_open"] = False
                    trade["status"] = "⏳ SIDEWAYS CONSOLIDATION EXIT"
                    send_telegram_message(
                        f"⏳ <b>PRO ALERT (Trade #{trade['id']}) : SIDEWAYS MARKET EXIT!</b> ⏳\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>कारण:</b> ट्रेड लिए 45 मिनट हो चुके हैं और मार्केट बिना मोमेंटम के एक ही जगह अटका हुआ है।\n\n"
                        f"📢 <b>प्रो सलाह:</b> फॉरेक्स में सुस्त मार्केट अचानक उल्टा स्पाइक मार सकता है। स्टॉप-लॉस का इंतजार न करें, <b>अभी तुरंत कॉस्ट या छोटे प्रॉफिट/लॉस पर ट्रेड क्लोज कर दें!</b>\n\n"
                        f"────────────────────\n\n{get_trades_scoreboard()}"
                    )
                    continue

            # 4. प्रो नियम: अचानक ट्रेंड पलट जाए (EMA20 और EMA50 दोनों उलटे क्रॉस हो जाएं)
            if hold_minutes >= 15:
                trend_reversed = False
                if trade["direction"] == "BUY" and price < ema20 and price < ema50:
                    trend_reversed = True
                elif trade["direction"] == "SELL" and price > ema20 and price > ema50:
                    trend_reversed = True

                if trend_reversed:
                    trade["is_open"] = False
                    trade["status"] = "🔄 TREND REVERSAL EXIT (जल्दी बचाव)"
                    send_telegram_message(
                        f"🔄 <b>PRO ALERT (Trade #{trade['id']}) : TREND REVERSAL EXIT!</b> 🔄\n\n"
                        f"🌍 <b>Pair:</b> {trade['pair']}\n\n"
                        f"📍 <b>Entry:</b> {entry_str}  ➡️  <b>Current Price:</b> {price_str}\n\n"
                        f"⚠️ <b>कारण:</b> इस पेयर का ट्रेंड अचानक पलट गया है (Price ने 20 EMA और 50 EMA दोनों को तोड़ दिया है)।\n\n"
                        f"📢 <b>प्रो सलाह:</b> पूरा स्टॉप-लॉस देने से बेहतर है कि अभी तुरंत ट्रेड से बाहर निकल जाएं!\n\n"
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
            adx   = float(latest['ADX']) if not np.isnan(latest['ADX']) else 15.0
            dec   = info["decimals"]

            # 1. पहले चल रहे ट्रेड्स को प्रो ट्रेडर की तरह मैनेज करें
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

            # 5. अगर मार्केट साइडवेज है (ADX < 20) तो ट्रेड न लें
            if adx < ADX_THRESHOLD:
                continue

            signal, direction, strategy_used = evaluate_forex_strategies(df)

            if signal:
                last_candle_traded[name] = candle_time
                trades_today += 1

                sl_dist = round(atr * 1.5, dec)
                if sl_dist <= 0:
                    continue
                rr_ratio = 2.2
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
                    f"🚨 <b>FOREX PRO SIGNAL (Trade {trades_today} of {MAX_TRADES_PER_DAY})</b> 🚨\n\n"
                    f"🌍 <b>Pair:</b> <b>{name}</b>\n\n"
                    f"🧠 <b>Setup:</b> {strategy_used}\n\n"
                    f"⚡ <b>Action:</b> <b>{signal}</b>\n\n"
                    f"🔍 <b>MT4/MT5 Symbol:</b> <code>{info['mt5_name']}</code>\n\n"
                    f"📍 <b>Entry Price:</b> {entry_s}\n\n"
                    f"🛑 <b>Stop-Loss:</b> {sl_s}\n\n"
                    f"🛡️ <b>Breakeven Level (1:1):</b> {half_s}\n\n"
                    f"🎯 <b>Full Target (1:2.2):</b> {target_s}\n\n"
                    f"💼 <b>Recommended Lot:</b> 0.01 - 0.02 Lot (Max Risk: ${max_risk_usd:.0f} / ~₹{max_risk_usd*85:.0f})\n\n"
                    f"⏳ <b>Pro Guard:</b> 1:1 पर SL कॉस्ट पर आएगा, और 45 मिनट में मूव न आने या ट्रेंड पलटने पर बॉट खुद एग्जिट करा देगा!\n\n"
                    f"────────────────────\n\n"
                    f"{get_trades_scoreboard()}"
                )
                send_telegram_message(msg)

        except Exception as e:
            print(f"Error scanning {name}: {e}")

if __name__ == "__main__":
    mkt_msg = "🟢 फॉरेक्स मार्केट खुला है (24/5 लाइव स्कैनिंग चालू है)।" if is_forex_market_open() else "🔴 अभी वीकेंड की वजह से ग्लोबल फॉरेक्स मार्केट बंद है। सोमवार सुबह से अपने आप ट्रेड आएंगे।"

    send_telegram_message(
        "🌍 <b>Forex & Gold PRO Bot Active! (24/5 Mode)</b>\n\n"
        f"📢 <b>Status:</b> {mkt_msg}\n\n"
        "💎 <b>Pairs Monitored:</b>\n"
        "• XAU/USD (Gold)\n"
        "• EUR/USD, GBP/USD, USD/JPY, GBP/JPY\n\n"
        "✅ <b>Pro Rules Active:</b>\n"
        "• Stop-Hunt Wick Filter\n"
        "• 1:1 Breakeven Auto-Trail\n"
        "• 45-Min Consolidation Exit\n"
        "• Trend Reversal Early Exit"
    )

    start_run = time.time()
    last_scan_time = 0
    # GitHub Actions में एक बार में लगभग 5 घंटे 50 मिनट (21000 सेकंड) तक लगातार चलेगा
    while time.time() - start_run < 21000:
        check_telegram_commands()
        if time.time() - last_scan_time >= 20:
            scan_forex_market()
            last_scan_time = time.time()
        time.sleep(3)
