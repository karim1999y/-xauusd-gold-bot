import os
import time
import json
import urllib.request
import urllib.parse
import threading


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


# =========================================================
# SETTINGS
# =========================================================

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYMBOL = "XAU/USD"
INTERVAL = "15min"

# فحص السوق كل دقيقة
CHECK_SECONDS = 60

# آخر شمعة تم إرسال إشارة عليها
last_signal_candle = None

# حماية من تشغيل أكثر من فحص بنفس الوقت
market_lock = threading.Lock()


# =========================================================
# HTTP
# =========================================================

def get_json(url):
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Gold-Signals/2.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def telegram(method, data=None):
    url = f"{TELEGRAM_API}/{method}"

    encoded = None

    if data:
        encoded = urllib.parse.urlencode(data).encode()

    request = urllib.request.Request(
        url,
        data=encoded
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode())


def send_message(chat_id, text):
    return telegram(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text
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
        "apikey": TWELVE_DATA_API_KEY
    }

    url = (
        "https://api.twelvedata.com/time_series?"
        + urllib.parse.urlencode(params)
    )

    data = get_json(url)

    if data.get("status") == "error":
        raise RuntimeError(
            data.get("message", "Twelve Data error")
        )

    values = data.get("values", [])

    if len(values) < 60:
        raise RuntimeError(
            f"Not enough candles: {len(values)}"
        )

    # Twelve Data يرجع الأحدث أولاً
    values = list(reversed(values))

    candles = []

    for c in values:

        candles.append({
            "time": c["datetime"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"])
        })

    return candles


# =========================================================
# EMA
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

    for price in values[period:]:
        result = (
            (price - result) * multiplier
        ) + result

    return result


# =========================================================
# RSI - Wilder
# =========================================================

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
        return 100.0

    rs = avg_gain / avg_loss

    result = 100 - (
        100 / (1 + rs)
    )

    for i in range(period, len(gains)):

        avg_gain = (
            (avg_gain * (period - 1))
            + gains[i]
        ) / period

        avg_loss = (
            (avg_loss * (period - 1))
            + losses[i]
        ) / period

        if avg_loss == 0:
            result = 100.0
        else:

            rs = avg_gain / avg_loss

            result = 100 - (
                100 / (1 + rs)
            )

    return result


# =========================================================
# ATR
# =========================================================

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

    # =====================================================
    # USE CLOSED CANDLES ONLY
    # =====================================================

    if len(candles) < 65:
        return None

    closed = candles[:-1]

    if len(closed) < 60:
        return None

    closes = [
        c["close"]
        for c in closed
    ]

    # =====================================================
    # INDICATORS
    # =====================================================

    ema20 = ema(closes, 20)
    ema50 = ema(closes, 50)

    current_rsi = rsi(
        closes,
        14
    )

    current_atr = atr(
        closed,
        14
    )

    if (
        ema20 is None
        or ema50 is None
        or current_rsi is None
        or current_atr is None
    ):
        return None

    # =====================================================
    # CURRENT CLOSED CANDLE
    # =====================================================

    candle = closed[-1]
    previous = closed[-2]

    price = candle["close"]

    # EMA20 من شمعة سابقة
    previous_ema20 = ema(
        closes[:-1],
        20
    )

    if previous_ema20 is None:
        return None

    # =====================================================
    # BUY
    # =====================================================

    buy_score = 0
    buy_reasons = []

    # 1 - Trend
    if ema20 > ema50:

        buy_score += 1

        buy_reasons.append(
            "EMA20 > EMA50"
        )

    # 2 - Price above EMA20
    if price > ema20:

        buy_score += 1

        buy_reasons.append(
            "Price > EMA20"
        )

    # 3 - RSI bullish
    if 52 <= current_rsi <= 68:

        buy_score += 1

        buy_reasons.append(
            "RSI bullish"
        )

    # 4 - EMA20 rising OR breakout
    breakout = (
        candle["close"]
        > previous["high"]
    )

    ema_rising = (
        ema20 > previous_ema20
    )

    if breakout:

        buy_score += 1

        buy_reasons.append(
            "Breakout"
        )

    elif ema_rising:

        buy_score += 1

        buy_reasons.append(
            "EMA20 rising"
        )


    # =====================================================
    # SELL
    # =====================================================

    sell_score = 0
    sell_reasons = []

    # 1 - Trend
    if ema20 < ema50:

        sell_score += 1

        sell_reasons.append(
            "EMA20 < EMA50"
        )

    # 2 - Price below EMA20
    if price < ema20:

        sell_score += 1

        sell_reasons.append(
            "Price < EMA20"
        )

    # 3 - RSI bearish
    if 32 <= current_rsi <= 48:

        sell_score += 1

        sell_reasons.append(
            "RSI bearish"
        )

    # 4 - Breakdown OR EMA20 falling
    breakdown = (
        candle["close"]
        < previous["low"]
    )

    ema_falling = (
        ema20 < previous_ema20
    )

    if breakdown:

        sell_score += 1

        sell_reasons.append(
            "Breakdown"
        )

    elif ema_falling:

        sell_score += 1

        sell_reasons.append(
            "EMA20 falling"
        )


    # =====================================================
    # STRONG SIGNAL ONLY
    # =====================================================

    if (
        buy_score >= 3
        and buy_score > sell_score
    ):

        direction = "BUY"
        score = buy_score
        reasons = buy_reasons

    elif (
        sell_score >= 3
        and sell_score > buy_score
    ):

        direction = "SELL"
        score = sell_score
        reasons = sell_reasons

    else:

        return None


    # =====================================================
    # RISK MANAGEMENT
    # =====================================================

    entry = price

    # ATR risk
    risk = current_atr * 1.2

    if direction == "BUY":

        sl = entry - risk

        tp1 = entry + (
            risk * 1.5
        )

        tp2 = entry + (
            risk * 2.2
        )

    else:

        sl = entry + risk

        tp1 = entry - (
            risk * 1.5
        )

        tp2 = entry - (
            risk * 2.2
        )


    # =====================================================
    # RETURN
    # =====================================================

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

        "candle_time": candle["time"]
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

        "🚨 XAUUSD LIVE SIGNAL\n\n"

        f"{emoji} الاتجاه: {direction}\n\n"

        f"📍 Entry: "
        f"{signal['entry']:.2f}\n"

        f"🛑 SL: "
        f"{signal['sl']:.2f}\n"

        f"🎯 TP1: "
        f"{signal['tp1']:.2f}\n"

        f"🎯 TP2: "
        f"{signal['tp2']:.2f}\n\n"

        "📊 Timeframe: M15\n"

        f"📈 RSI: "
        f"{signal['rsi']:.1f}\n"

        f"📏 ATR: "
        f"{signal['atr']:.2f}\n"

        f"💪 Signal Score: "
        f"{signal['score']}/4\n\n"

        "🔎 أسباب الإشارة:\n"

        f"{reasons}\n\n"

        "⚠️ إشارة آلية مبنية على "
        "بيانات السوق، وليست ضمانًا للربح."
    )


# =========================================================
# MARKET CHECK
# =========================================================

def check_market():

    global last_signal_candle

    if not market_lock.acquire(
        blocking=False
    ):

        print(
            "Market check already running."
        )

        return

    try:

        print(
            "Checking XAUUSD..."
        )

        candles = get_candles()

        signal = analyze(candles)

        if not signal:

            print(
                "No valid signal."
            )

            return

        candle_time = signal[
            "candle_time"
        ]

        # منع تكرار نفس شمعة M15
        if candle_time == last_signal_candle:

            print(
                "Signal already sent for candle:",
                candle_time
            )

            return

        # تسجيل الشمعة
        last_signal_candle = candle_time

        message = format_signal(
            signal
        )

        print(
            "\n" + message + "\n"
        )

        if TELEGRAM_CHAT_ID:

            send_message(
                TELEGRAM_CHAT_ID,
                message
            )

            print(
                "✅ Automatic signal sent."
            )

        else:

            print(
                "TELEGRAM_CHAT_ID missing."
            )

    except Exception as e:

        print(
            "❌ Market error:",
            e
        )

    finally:

        market_lock.release()


# =========================================================
# AUTOMATIC MARKET LOOP
# =========================================================

def market_loop():

    print(
        "📡 Automatic market monitor started."
    )

    while True:

        try:

            check_market()

        except Exception as e:

            print(
                "Market loop error:",
                e
            )

        time.sleep(
            CHECK_SECONDS
        )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_message(message):

    chat_id = message[
        "chat"
    ]["id"]

    text = message.get(
        "text",
        ""
    ).strip()


    # =====================================================
    # /START
    # =====================================================

    if text == "/start":

        send_message(

            chat_id,

            "👋 أهلاً بك في XAU Gold Signals\n\n"

            "🟡 بوت إشارات الذهب XAUUSD\n\n"

            "📡 المراقبة التلقائية: تعمل\n"

            "⏱ الفحص: كل 60 ثانية\n"

            "🕯 الفريم: M15\n\n"

            "الأوامر:\n"

            "/test - اختبار البوت\n"

            "/signal - تحليل XAUUSD الآن\n"

            "/status - حالة البوت"
        )


    # =====================================================
    # /TEST
    # =====================================================

    elif text == "/test":

        send_message(

            chat_id,

            "✅ Telegram bot is working.\n\n"

            "📡 Automatic market monitor is enabled.\n"

            "⚠️ هذا اختبار فقط وليس إشارة حقيقية."
        )


    # =====================================================
    # /SIGNAL
    # =====================================================

    elif text == "/signal":

        try:

            candles = get_candles()

            signal = analyze(
                candles
            )

            if signal:

                send_message(
                    chat_id,
                    format_signal(signal)
                )

            else:

                send_message(

                    chat_id,

                    "⏳ لا توجد إشارة قوية حاليًا.\n\n"

                    "لا يوجد BUY أو SELL "
                    "بدرجة 3/4 على الأقل.\n\n"

                    "لن نرسل صفقة إجبارية."
                )

        except Exception as e:

            print(
                "Signal command error:",
                e
            )

            send_message(

                chat_id,

                "❌ تعذر تحليل XAUUSD حاليًا.\n"

                "حاول مرة أخرى لاحقًا."
            )


    # =====================================================
    # /STATUS
    # =====================================================

    elif text == "/status":

        try:

            candles = get_candles()

            signal = analyze(
                candles
            )

            if signal:

                status = (

                    "🟢 البوت يعمل\n\n"

                    "📡 Automatic Monitor: ON\n"

                    "📊 Symbol: XAU/USD\n"

                    "🕯 Timeframe: M15\n"

                    "⏱ Check: 60 seconds\n\n"

                    f"آخر تحليل قوي: "
                    f"{signal['direction']}\n"

                    f"Score: "
                    f"{signal['score']}/4\n"

                    f"RSI: "
                    f"{signal['rsi']:.1f}"
                )

            else:

                status = (

                    "🟢 البوت يعمل\n\n"

                    "📡 Automatic Monitor: ON\n"

                    "📊 Symbol: XAU/USD\n"

                    "🕯 Timeframe: M15\n"

                    "⏱ Check: 60 seconds\n\n"

                    "⏳ لا توجد إشارة قوية حاليًا."
                )

            send_message(
                chat_id,
                status
            )

        except Exception:

            send_message(

                chat_id,

                "🟢 البوت يعمل، "

                "لكن تعذر الحصول على بيانات السوق الآن."
            )


    # =====================================================
    # UNKNOWN COMMAND
    # =====================================================

    else:

        send_message(

            chat_id,

            "الأوامر المتاحة:\n\n"

            "/start - تشغيل البوت\n"

            "/test - اختبار الاتصال\n"

            "/signal - تحليل XAUUSD الآن\n"

            "/status - حالة البوت"
        )


# =========================================================
# TELEGRAM LOOP
# =========================================================

def telegram_loop():

    print(
        "📱 Telegram listener started."
    )

    offset = None

    while True:

        try:

            result = telegram(

                "getUpdates",

                {
                    "timeout": 25,
                    "offset": offset
                }
            )

            for update in result.get(
                "result",
                []
            ):

                offset = (
                    update["update_id"]
                    + 1
                )

                message = update.get(
                    "message"
                )

                if message:

                    handle_message(
                        message
                    )

        except Exception as e:

            print(
                "❌ Telegram error:",
                e
            )

            time.sleep(5)


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "======================================"
    )

    print(
        "      XAU GOLD SIGNALS BOT v2"
    )

    print(
        "======================================"
    )

    print(
        "Symbol:",
        SYMBOL
    )

    print(
        "Timeframe:",
        INTERVAL
    )

    print(
        "Automatic check:",
        CHECK_SECONDS,
        "seconds"
    )

    print(
        "======================================"
    )


    # =====================================================
    # TELEGRAM THREAD
    # =====================================================

    telegram_thread = threading.Thread(

        target=telegram_loop,

        daemon=True
    )

    telegram_thread.start()


    # =====================================================
    # MARKET THREAD
    # =====================================================

    market_thread = threading.Thread(

        target=market_loop,

        daemon=True
    )

    market_thread.start()


    # =====================================================
    # KEEP PROCESS ALIVE
    # =====================================================

    while True:

        time.sleep(60)


# =========================================================
# START
# =========================================================

if __name__ == "__main__":

    main()
