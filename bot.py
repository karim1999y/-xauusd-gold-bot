# XAU Gold Signals Bot - 4/4 only
# استبدل bot.py بهذا الملف، ثم اعمل Commit و Deploy على Railway.

import os, time, json, urllib.request, urllib.parse

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")
if not TWELVE_DATA_API_KEY:
    raise RuntimeError("TWELVE_DATA_API_KEY is not set")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SYMBOL = "XAU/USD"
INTERVAL = "15min"
CHECK_SECONDS = 60
last_signal_candle = None

def get_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": "XAU-Gold-Signals/1.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())

def telegram(method, data=None):
    url = f"{TELEGRAM_API}/{method}"
    encoded = urllib.parse.urlencode(data).encode() if data else None
    with urllib.request.urlopen(urllib.request.Request(url, data=encoded), timeout=35) as r:
        return json.loads(r.read().decode())

def send_message(chat_id, text):
    telegram("sendMessage", {"chat_id": str(chat_id), "text": text})

def get_candles():
    params = {"symbol": SYMBOL, "interval": INTERVAL, "outputsize": 120,
              "timezone": "UTC", "apikey": TWELVE_DATA_API_KEY}
    url = "https://api.twelvedata.com/time_series?" + urllib.parse.urlencode(params)
    data = get_json(url)
    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "Twelve Data error"))
    values = data.get("values", [])
    if len(values) < 60:
        raise RuntimeError("Not enough XAUUSD candles")
    values.reverse()
    return [{"time": c["datetime"], "open": float(c["open"]), "high": float(c["high"]),
             "low": float(c["low"]), "close": float(c["close"])} for c in values]

def ema(values, period):
    if len(values) < period: return None
    m = 2 / (period + 1)
    result = sum(values[:period]) / period
    for price in values[period:]:
        result = (price - result) * m + result
    return result

def rsi(values, period=14):
    if len(values) < period + 1: return None
    gains, losses = [], []
    for i in range(1, len(values)):
        ch = values[i] - values[i-1]
        gains.append(max(ch, 0)); losses.append(max(-ch, 0))
    ag = sum(gains[:period]) / period
    al = sum(losses[:period]) / period
    result = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(period, len(gains)):
        ag = ((ag * (period-1)) + gains[i]) / period
        al = ((al * (period-1)) + losses[i]) / period
        result = 100 if al == 0 else 100 - 100 / (1 + ag / al)
    return result

def atr(candles, period=14):
    if len(candles) < period + 1: return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i-1]["close"]
        trs.append(max(h-l, abs(h-pc), abs(l-pc)))
    return sum(trs[-period:]) / period

def analyze(candles):
    # الشموع المغلقة فقط
    closed = candles[:-1]
    if len(closed) < 60: return None

    closes = [c["close"] for c in closed]
    e20, e50 = ema(closes, 20), ema(closes, 50)
    rr, aa = rsi(closes, 14), atr(closed, 14)
    if None in (e20, e50, rr, aa) or aa <= 0: return None

    candle, previous = closed[-1], closed[-2]
    price = candle["close"]

    # BUY: يجب تحقق الشروط الأربعة كلها
    buy_reasons = []
    if e20 > e50: buy_reasons.append("EMA20 > EMA50")
    if price > e20: buy_reasons.append("Price > EMA20")
    if 52 <= rr <= 70: buy_reasons.append("RSI bullish")
    if candle["close"] > previous["high"]: buy_reasons.append("Breakout")

    # SELL: يجب تحقق الشروط الأربعة كلها
    sell_reasons = []
    if e20 < e50: sell_reasons.append("EMA20 < EMA50")
    if price < e20: sell_reasons.append("Price < EMA20")
    if 30 <= rr <= 48: sell_reasons.append("RSI bearish")
    if candle["close"] < previous["low"]: sell_reasons.append("Breakdown")

    if len(buy_reasons) == 4 and len(sell_reasons) < 4:
        direction, reasons = "BUY", buy_reasons
    elif len(sell_reasons) == 4 and len(buy_reasons) < 4:
        direction, reasons = "SELL", sell_reasons
    else:
        return None

    risk = aa * 1.2
    entry = price
    if direction == "BUY":
        sl, tp1, tp2 = entry-risk, entry+risk*1.5, entry+risk*2.2
    else:
        sl, tp1, tp2 = entry+risk, entry-risk*1.5, entry-risk*2.2

    return {"direction": direction, "entry": entry, "sl": sl, "tp1": tp1,
            "tp2": tp2, "rsi": rr, "score": 4, "reasons": reasons,
            "candle_time": candle["time"]}

def format_signal(s):
    emoji = "🟢" if s["direction"] == "BUY" else "🔴"
    reasons = "\n".join("• " + x for x in s["reasons"])
    return (f"🚨 XAUUSD LIVE SIGNAL\n\n{emoji} الاتجاه: {s['direction']}\n\n"
            f"📍 Entry: {s['entry']:.2f}\n🛑 SL: {s['sl']:.2f}\n"
            f"🎯 TP1: {s['tp1']:.2f}\n🎯 TP2: {s['tp2']:.2f}\n\n"
            f"📊 Timeframe: M15\n📈 RSI: {s['rsi']:.1f}\n"
            f"💪 Signal Score: 4/4\n\n🔎 أسباب الإشارة:\n{reasons}\n\n"
            "⚠️ إشارة آلية مبنية على بيانات السوق، وليست ضمانًا للربح.")

def check_market():
    global last_signal_candle
    try:
        signal = analyze(get_candles())
        if not signal:
            print("No 4/4 signal.")
            return
        if signal["candle_time"] == last_signal_candle:
            print("Signal already sent for this candle.")
            return
        message = format_signal(signal)
        print(message)
        if TELEGRAM_CHAT_ID:
            send_message(TELEGRAM_CHAT_ID, message)
            last_signal_candle = signal["candle_time"]
            print("4/4 signal sent.")
    except Exception as e:
        print("Market check error:", e)

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()
    if text == "/start":
        send_message(chat_id, "👋 XAU Gold Signals\n\n/start - تشغيل\n/test - اختبار\n/signal - تحليل الآن")
    elif text == "/test":
        send_message(chat_id, "✅ Telegram bot is working.\n\nهذا اختبار فقط وليس إشارة حقيقية.")
    elif text == "/signal":
        try:
            signal = analyze(get_candles())
            send_message(chat_id, format_signal(signal) if signal else
                         "⏳ لا توجد إشارة 4/4 حاليًا.\nلن نرسل صفقة إجبارية.")
        except Exception:
            send_message(chat_id, "❌ تعذر تحليل XAUUSD حاليًا.")
    else:
        send_message(chat_id, "/start - تشغيل\n/test - اختبار\n/signal - تحليل الآن")

def process_updates(offset):
    try:
        result = telegram("getUpdates", {"timeout": 1, "offset": offset})
        for update in result.get("result", []):
            offset = update["update_id"] + 1
            if update.get("message"):
                handle_message(update["message"])
    except Exception as e:
        print("Telegram error:", e)
    return offset

def main():
    print("XAU Gold Signals Bot - 4/4 ONLY")
    offset, last_check = None, 0
    while True:
        offset = process_updates(offset)
        now = time.time()
        if now - last_check >= CHECK_SECONDS:
            check_market()
            last_check = now
        time.sleep(1)

if __name__ == "__main__":
    main()
