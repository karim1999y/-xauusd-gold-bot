# XAU Gold Signals Bot v3
# انسخ هذا الملف مكان bot.py في مشروعك.

import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")
if not TWELVE_DATA_API_KEY:
    raise RuntimeError("TWELVE_DATA_API_KEY is not set")
if not TELEGRAM_CHAT_ID:
    print("WARNING: TELEGRAM_CHAT_ID is not set")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
SYMBOL = "XAU/USD"
INTERVAL = "15min"
CHECK_SECONDS = 60

last_signal_candle = None


def get_json(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Gold-Signals/3.0",
            "Accept": "application/json",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code} from market API: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Market connection error: {e.reason}") from e


def telegram(method, data=None, timeout=35):
    url = f"{TELEGRAM_API}/{method}"
    encoded = None
    if data:
        encoded = urllib.parse.urlencode(
            {k: v for k, v in data.items() if v is not None}
        ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"User-Agent": "XAU-Gold-Signals/3.0"},
        method="POST" if encoded is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {e.code}: {body[:500]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"Telegram connection error: {e.reason}") from e


def send_message(chat_id, text):
    result = telegram("sendMessage", {"chat_id": str(chat_id), "text": text})
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected message: {result}")
    print("OK: Telegram accepted the message.")
    return result


def check_telegram():
    result = telegram("getMe")
    if not result.get("ok"):
        raise RuntimeError(f"Telegram getMe failed: {result}")
    print("OK: Telegram connection:", result.get("result", {}).get("username", "unknown"))


def clear_webhook():
    try:
        result = telegram("deleteWebhook", {"drop_pending_updates": "false"})
        print("Webhook cleared." if result.get("ok") else f"Webhook warning: {result}")
    except Exception as e:
        print("Webhook warning:", e)


def get_candles():
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": 120,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY,
    }
    url = "https://api.twelvedata.com/time_series?" + urllib.parse.urlencode(params)
    data = get_json(url)

    if data.get("status") == "error":
        raise RuntimeError("Twelve Data error: " + str(data.get("message", data)))

    values = data.get("values", [])
    if len(values) < 60:
        raise RuntimeError(f"Not enough XAUUSD candles: {len(values)}")

    values = list(reversed(values))
    return [
        {
            "time": c["datetime"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        }
        for c in values
    ]


def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    for price in values[period:]:
        result = (price - result) * multiplier + result
    return result


def rsi(values, period=14):
    if len(values) < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        return 100

    for i in range(period, len(gains)):
        avg_gain = ((avg_gain * (period - 1)) + gains[i]) / period
        avg_loss = ((avg_loss * (period - 1)) + losses[i]) / period

    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period


def analyze(candles):
    closed = candles[:-1]
    if len(closed) < 60:
        return None

    closes = [c["close"] for c in closed]
    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    current_rsi = rsi(closes, 14)
    current_atr = atr(closed, 14)
    previous_ema20 = ema(closes[:-1], 20)

    if None in (ema20, ema50, current_rsi, current_atr, previous_ema20):
        return None

    candle = closed[-1]
    previous = closed[-2]
    price = candle["close"]

    buy_score, buy_reasons = 0, []
    if ema20 > ema50:
        buy_score += 1
        buy_reasons.append("EMA20 > EMA50")
    if price > ema20:
        buy_score += 1
        buy_reasons.append("Price > EMA20")
    if 52 <= current_rsi <= 70:
        buy_score += 1
        buy_reasons.append("RSI bullish")
    if ema20 > previous_ema20:
        buy_score += 1
        buy_reasons.append("EMA20 rising")

    sell_score, sell_reasons = 0, []
    if ema20 < ema50:
        sell_score += 1
        sell_reasons.append("EMA20 < EMA50")
    if price < ema20:
        sell_score += 1
        sell_reasons.append("Price < EMA20")
    if 30 <= current_rsi <= 48:
        sell_score += 1
        sell_reasons.append("RSI bearish")
    if ema20 < previous_ema20:
        sell_score += 1
        sell_reasons.append("EMA20 falling")

    breakout = candle["close"] > previous["high"]
    breakdown = candle["close"] < previous["low"]

    if buy_score >= 3 and buy_score > sell_score:
        direction, reasons, score = "BUY", list(buy_reasons), buy_score
        if breakout and score < 4:
            reasons.append("Breakout")
    elif sell_score >= 3 and sell_score > buy_score:
        direction, reasons, score = "SELL", list(sell_reasons), sell_score
        if breakdown and score < 4:
            reasons.append("Breakdown")
    else:
        return None

    entry = price
    risk = current_atr * 1.2

    if direction == "BUY":
        sl = entry - risk
        tp1 = entry + risk * 1.5
        tp2 = entry + risk * 2.2
    else:
        sl = entry + risk
        tp1 = entry - risk * 1.5
        tp2 = entry - risk * 2.2

    return {
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": current_rsi,
        "atr": current_atr,
        "score": score,
        "reasons": reasons,
        "candle_time": candle["time"],
    }


def format_signal(s):
    emoji = "🟢" if s["direction"] == "BUY" else "🔴"
    reasons = "\n".join(f"• {x}" for x in s["reasons"])
    return (
        "🚨 XAUUSD LIVE SIGNAL\n\n"
        f"{emoji} الاتجاه: {s['direction']}\n\n"
        f"📍 Entry: {s['entry']:.2f}\n"
        f"🛑 SL: {s['sl']:.2f}\n"
        f"🎯 TP1: {s['tp1']:.2f}\n"
        f"🎯 TP2: {s['tp2']:.2f}\n\n"
        "📊 Timeframe: M15\n"
        f"📈 RSI: {s['rsi']:.1f}\n"
        f"📏 ATR: {s['atr']:.2f}\n"
        f"💪 Signal Score: {s['score']}/4\n\n"
        "🔎 أسباب الإشارة:\n"
        f"{reasons}\n\n"
        "⚠️ إشارة آلية مبنية على بيانات السوق، وليست ضمانًا للربح."
    )


def check_market():
    global last_signal_candle

    try:
        print(f"[{datetime.now(timezone.utc).isoformat()}] Checking XAUUSD...")
        candles = get_candles()
        closed = candles[:-1]

        if not closed:
            print("No closed candles.")
            return

        print("Latest closed M15 candle:", closed[-1]["time"])

        signal = analyze(candles)
        if not signal:
            print("No valid signal.")
            return

        candle_time = signal["candle_time"]
        print(f"Signal candidate: {signal['direction']} on {candle_time}")

        if candle_time == last_signal_candle:
            print("Already handled:", candle_time)
            return

        if not TELEGRAM_CHAT_ID:
            print("TELEGRAM_CHAT_ID missing. Signal NOT sent.")
            return

        message = format_signal(signal)

        # لا نحفظ الشمعة إلا بعد نجاح Telegram
        result = send_message(TELEGRAM_CHAT_ID, message)
        if result.get("ok"):
            last_signal_candle = candle_time
            print("✅ AUTOMATIC SIGNAL SENT SUCCESSFULLY.")
            print("Saved candle:", last_signal_candle)

    except Exception as e:
        print("❌ Market check error:", repr(e))


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text == "/start":
        send_message(
            chat_id,
            "👋 أهلاً بك في XAU Gold Signals\n\n"
            "📡 المراقبة التلقائية مفعلة.\n\n"
            "/test - اختبار Telegram\n"
            "/signal - تحليل XAUUSD الآن\n"
            "/status - حالة المراقبة",
        )

    elif text == "/test":
        send_message(
            chat_id,
            "✅ TEST SUCCESS\n\n"
            "Telegram connection is working.\n"
            "📡 Automatic market monitor is enabled.\n\n"
            "⚠️ هذا اختبار فقط وليس إشارة حقيقية.",
        )

    elif text == "/signal":
        try:
            signal = analyze(get_candles())
            if signal:
                send_message(chat_id, format_signal(signal))
            else:
                send_message(chat_id, "⏳ لا توجد إشارة قوية حاليًا.\n\nلن نرسل صفقة إجبارية.")
        except Exception as e:
            print("Signal command error:", repr(e))
            try:
                send_message(chat_id, f"❌ تعذر تحليل XAUUSD حاليًا.\n\nالسبب التقني: {e}")
            except Exception as te:
                print("Telegram error:", repr(te))

    elif text == "/status":
        send_message(
            chat_id,
            "🟢 البوت يعمل\n\n"
            "📡 مراقبة XAUUSD: ON\n"
            "⏱ الفحص: كل 60 ثانية\n"
            "📊 Timeframe: M15\n\n"
            f"🕯 آخر شمعة مرسلة بنجاح:\n{last_signal_candle or 'لا يوجد'}",
        )

    else:
        send_message(
            chat_id,
            "الأوامر المتاحة:\n\n"
            "/start - تشغيل البوت\n"
            "/test - اختبار الاتصال\n"
            "/signal - تحليل XAUUSD الآن\n"
            "/status - حالة البوت",
        )


def process_telegram_updates(offset):
    try:
        result = telegram(
            "getUpdates",
            {
                "timeout": 5,
                "offset": offset,
                "allowed_updates": json.dumps(["message"]),
            },
            timeout=15,
        )

        if not result.get("ok"):
            print("Telegram getUpdates failed:", result)
            return offset

        for update in result.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if message:
                try:
                    handle_message(message)
                except Exception as e:
                    print("Message handling error:", repr(e))

        return offset

    except Exception as e:
        text = str(e)
        if "409" in text or "Conflict" in text:
            print("❌ TELEGRAM 409 CONFLICT: another bot instance is using getUpdates.")
            print("⚠️ Keep only ONE running Railway deployment.")
            time.sleep(10)
            return offset

        print("Telegram polling error:", repr(e))
        time.sleep(3)
        return offset


def main():
    global last_signal_candle

    print("================================")
    print("XAU Gold Signals Bot v3")
    print("Automatic market engine started")
    print("Symbol:", SYMBOL)
    print("Timeframe:", INTERVAL)
    print("Check every:", CHECK_SECONDS, "seconds")
    print("================================")

    try:
        check_telegram()
        clear_webhook()
    except Exception as e:
        print("❌ Telegram startup error:", repr(e))

    try:
        candles = get_candles()
        closed = candles[:-1]
        if closed:
            last_signal_candle = closed[-1]["time"]
            print("Startup candle:", last_signal_candle)
            print("ℹ️ Existing startup candle will not be sent.")
    except Exception as e:
        print("❌ Startup market error:", repr(e))

    offset = None
    last_market_check = 0

    while True:
        try:
            offset = process_telegram_updates(offset)

            now = time.time()
            if now - last_market_check >= CHECK_SECONDS:
                last_market_check = now
                check_market()

            time.sleep(1)

        except KeyboardInterrupt:
            print("Bot stopped.")
            break
        except Exception as e:
            print("❌ Main loop error:", repr(e))
            time.sleep(5)


if __name__ == "__main__":
    main()
