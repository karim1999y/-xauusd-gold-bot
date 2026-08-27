import os
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime, timezone


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

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

# فحص كل دقيقة
CHECK_SECONDS = 60

# آخر شمعة تمت معالجتها
last_signal_candle = None


# =========================================================
# HTTP HELPERS
# =========================================================

def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Gold-Signals/1.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def telegram(method, data=None):
    url = f"{TELEGRAM_API}/{method}"

    encoded = None

    if data:
        encoded = urllib.parse.urlencode(data).encode()

    with urllib.request.urlopen(
        urllib.request.Request(url, data=encoded),
        timeout=30
    ) as response:
        return json.loads(response.read().decode())


def send_message(chat_id, text):
    telegram(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
        }
    )


# =========================================================
# MARKET DATA
# =========================================================

def get_candles():
    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": 120,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY,
    }

    url = (
        "https://api.twelvedata.com/time_series?"
        + urllib.parse.urlencode(params)
    )

    data = get_json(url)

    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "Twelve Data error"))

    values = data.get("values", [])

    if len(values) < 60:
        raise RuntimeError("Not enough XAUUSD candles")

    # Twelve Data يرجع الأحدث أولاً
    values = list(reversed(values))

    candles = []

    for c in values:
        candles.append({
            "time": c["datetime"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
        })

    return candles


# =========================================================
# TECHNICAL INDICATORS
# =========================================================

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

    gains = []
    losses = []

    for i in range(1, len(values)):
        change = values[i] - values[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss
    result = 100 - (100 / (1 + rs))

    for i in range(period, len(gains)):
        avg_gain = (
            (avg_gain * (period - 1)) + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1)) + losses[i]
        ) / period

        if avg_loss == 0:
            result = 100
        else:
            rs = avg_gain / avg_loss
            result = 100 - (100 / (1 + rs))

    return result


def atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        trs.append(tr)

    return sum(trs[-period:]) / period


# =========================================================
# SIGNAL ENGINE
# =========================================================

def analyze(candles):
    # آخر شمعة قد تكون غير مكتملة.
    # نستخدم الشموع المغلقة فقط.
    closed = candles[:-1]

    if len(closed) < 60:
        return None

    closes = [c["close"] for c in closed]

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)
    current_rsi = rsi(closes, 14)
    current_atr = atr(closed, 14)

    if not ema20 or not ema50 or not current_rsi or not current_atr:
        return None

    candle = closed[-1]
    previous = closed[-2]

    price = candle["close"]

    # =====================================================
    # BUY CONDITIONS
    # =====================================================

    buy_score = 0
    buy_reasons = []

    if ema20 > ema50:
        buy_score += 1
        buy_reasons.append("EMA20 > EMA50")

    if price > ema20:
        buy_score += 1
        buy_reasons.append("Price > EMA20")

    if 52 <= current_rsi <= 70:
        buy_score += 1
        buy_reasons.append("RSI bullish")

    # اختراق قمة الشمعة السابقة
    if candle["close"] > previous["high"]:
        buy_score += 1
        buy_reasons.append("Breakout")

    # =====================================================
    # SELL CONDITIONS
    # =====================================================

    sell_score = 0
    sell_reasons = []

    if ema20 < ema50:
        sell_score += 1
        sell_reasons.append("EMA20 < EMA50")

    if price < ema20:
        sell_score += 1
        sell_reasons.append("Price < EMA20")

    if 30 <= current_rsi <= 48:
        sell_score += 1
        sell_reasons.append("RSI bearish")

    # كسر قاع الشمعة السابقة
    if candle["close"] < previous["low"]:
        sell_score += 1
        sell_reasons.append("Breakdown")

    # =====================================================
    # REQUIRE STRONG SIGNAL
    # =====================================================

    if buy_score >= 3 and buy_score > sell_score:
        direction = "BUY"
        reasons = buy_reasons

    elif sell_score >= 3 and sell_score > buy_score:
        direction = "SELL"
        reasons = sell_reasons

    else:
        return None

    # =====================================================
    # ENTRY / SL / TP
    # =====================================================

    entry = price

    # ATR based risk management
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
        "score": max(buy_score, sell_score),
        "reasons": reasons,
        "candle_time": candle["time"],
    }


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(signal):

    direction = signal["direction"]

    if direction == "BUY":
        emoji = "🟢"
    else:
        emoji = "🔴"

    reasons = "\n".join(
        f"• {reason}"
        for reason in signal["reasons"]
    )

    return (
        f"🚨 XAUUSD LIVE SIGNAL\n\n"
        f"{emoji} الاتجاه: {direction}\n\n"
        f"📍 Entry: {signal['entry']:.2f}\n"
        f"🛑 SL: {signal['sl']:.2f}\n"
        f"🎯 TP1: {signal['tp1']:.2f}\n"
        f"🎯 TP2: {signal['tp2']:.2f}\n\n"
        f"📊 Timeframe: M15\n"
        f"📈 RSI: {signal['rsi']:.1f}\n"
        f"💪 Signal Score: {signal['score']}/4\n\n"
        f"🔎 أسباب الإشارة:\n"
        f"{reasons}\n\n"
        f"⚠️ إشارة آلية مبنية على بيانات السوق، "
        f"وليست ضمانًا للربح."
    )


# =========================================================
# AUTOMATIC SIGNAL CHECK
# =========================================================

def check_market():

    global last_signal_candle

    candles = get_candles()

    signal = analyze(candles)

    if not signal:
        print("No valid signal.")
        return

    candle_time = signal["candle_time"]

    # منع تكرار نفس الإشارة
    if candle_time == last_signal_candle:
        print("Signal already sent.")
        return

    last_signal_candle = candle_time

    message = format_signal(signal)

    print(message)

    if TELEGRAM_CHAT_ID:
        send_message(
            TELEGRAM_CHAT_ID,
            message
        )

        print("Signal sent to Telegram.")
    else:
        print(
            "TELEGRAM_CHAT_ID is missing. "
            "Signal was NOT sent."
        )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_message(message):

    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text == "/start":

        send_message(
            chat_id,
            "👋 أهلاً بك في XAU Gold Signals\n\n"
            "🟡 بوت إشارات الذهب XAUUSD\n\n"
            "الأوامر:\n"
            "/test - اختبار البوت\n"
            "/signal - تحليل XAUUSD الآن"
        )

    elif text == "/test":

        send_message(
            chat_id,
            "✅ Telegram bot is working.\n\n"
            "هذا اختبار فقط وليس إشارة حقيقية."
        )

    elif text == "/signal":

        try:
            candles = get_candles()
            signal = analyze(candles)

            if signal:
                send_message(
                    chat_id,
                    format_signal(signal)
                )
            else:
                send_message(
                    chat_id,
                    "⏳ لا توجد إشارة قوية حاليًا.\n\n"
                    "لن نرسل صفقة إجبارية."
                )

        except Exception as e:

            print("Signal error:", e)

            send_message(
                chat_id,
                "❌ تعذر تحليل XAUUSD حاليًا.\n"
                "حاول مرة أخرى لاحقًا."
            )

    else:

        send_message(
            chat_id,
            "الأوامر المتاحة:\n\n"
            "/start - تشغيل البوت\n"
            "/test - اختبار الاتصال\n"
            "/signal - تحليل XAUUSD الآن"
        )


# =========================================================
# TELEGRAM POLLING
# =========================================================

def telegram_loop():

    offset = None

    while True:

        try:

            result = telegram(
                "getUpdates",
                {
                    "timeout": 25,
                    "offset": offset,
                }
            )

            for update in result.get("result", []):

                offset = update["update_id"] + 1

                message = update.get("message")

                if message:
                    handle_message(message)

        except Exception as e:

            print("Telegram error:", e)

            time.sleep(5)


# =========================================================
# MAIN
# =========================================================

def main():

    print("================================")
    print("XAU Gold Signals Bot")
    print("Live market engine started")
    print("Symbol:", SYMBOL)
    print("Timeframe:", INTERVAL)
    print("================================")

    last_market_check = 0

    while True:

        try:

            # Telegram commands
            telegram_loop()

        except Exception as e:

            print("Main error:", e)

            time.sleep(5)


if __name__ == "__main__":
    main()
