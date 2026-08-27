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

# فحص السوق كل دقيقة
CHECK_SECONDS = 60

# آخر شمعة تم إرسال إشارة عليها
last_signal_candle = None


# =========================================================
# HTTP HELPERS
# =========================================================

def get_json(url):

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Gold-Signals/2.0"
        }
    )

    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(
            response.read().decode()
        )


def telegram(method, data=None):

    url = f"{TELEGRAM_API}/{method}"

    encoded = None

    if data:
        clean_data = {
            k: v for k, v in data.items()
            if v is not None
        }

        encoded = urllib.parse.urlencode(
            clean_data
        ).encode()

    request = urllib.request.Request(
        url,
        data=encoded
    )

    with urllib.request.urlopen(
        request,
        timeout=35
    ) as response:

        return json.loads(
            response.read().decode()
        )


def send_message(chat_id, text):

    result = telegram(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text
        }
    )

    if not result.get("ok"):
        print("Telegram send error:", result)

    return result


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
            data.get(
                "message",
                "Twelve Data error"
            )
        )

    values = data.get("values", [])

    if len(values) < 60:
        raise RuntimeError(
            "Not enough XAUUSD candles"
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
# TECHNICAL INDICATORS
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(
        values[:period]
    ) / period

    for price in values[period:]:

        result = (
            (price - result) * multiplier
            + result
        )

    return result


def rsi(values, period=14):

    if len(values) < period + 1:
        return None

    gains = []
    losses = []

    for i in range(1, len(values)):

        change = (
            values[i] - values[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains[:period]) / period
    )

    avg_loss = (
        sum(losses[:period]) / period
    )

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    result = 100 - (
        100 / (1 + rs)
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss * (period - 1)
            )
            + losses[i]
        ) / period

        if avg_loss == 0:

            result = 100

        else:

            rs = avg_gain / avg_loss

            result = 100 - (
                100 / (1 + rs)
            )

    return result


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]

        previous_close = (
            candles[i - 1]["close"]
        )

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close)
        )

        trs.append(tr)

    return sum(
        trs[-period:]
    ) / period


# =========================================================
# SIGNAL ENGINE
# =========================================================

def analyze(candles):

    # آخر شمعة قد تكون غير مكتملة
    closed = candles[:-1]

    if len(closed) < 60:
        return None

    closes = [
        c["close"]
        for c in closed
    ]

    # -------------------------
    # Indicators
    # -------------------------

    ema20 = ema(
        closes,
        20
    )

    ema50 = ema(
        closes,
        50
    )

    current_rsi = rsi(
        closes,
        14
    )

    current_atr = atr(
        closed,
        14
    )

    # EMA20 السابقة
    previous_closes = closes[:-1]

    previous_ema20 = ema(
        previous_closes,
        20
    )

    if (
        ema20 is None
        or ema50 is None
        or current_rsi is None
        or current_atr is None
        or previous_ema20 is None
    ):
        return None

    candle = closed[-1]
    previous = closed[-2]

    price = candle["close"]

    # =====================================================
    # BUY CONDITIONS
    # =====================================================

    buy_score = 0
    buy_reasons = []

    # 1
    if ema20 > ema50:

        buy_score += 1

        buy_reasons.append(
            "EMA20 > EMA50"
        )

    # 2
    if price > ema20:

        buy_score += 1

        buy_reasons.append(
            "Price > EMA20"
        )

    # 3
    if 52 <= current_rsi <= 70:

        buy_score += 1

        buy_reasons.append(
            "RSI bullish"
        )

    # 4
    if ema20 > previous_ema20:

        buy_score += 1

        buy_reasons.append(
            "EMA20 rising"
        )

    # اختراق قوي لقمة الشمعة السابقة
    breakout = (
        candle["close"]
        > previous["high"]
    )

    # =====================================================
    # SELL CONDITIONS
    # =====================================================

    sell_score = 0
    sell_reasons = []

    # 1
    if ema20 < ema50:

        sell_score += 1

        sell_reasons.append(
            "EMA20 < EMA50"
        )

    # 2
    if price < ema20:

        sell_score += 1

        sell_reasons.append(
            "Price < EMA20"
        )

    # 3
    if 30 <= current_rsi <= 48:

        sell_score += 1

        sell_reasons.append(
            "RSI bearish"
        )

    # 4
    if ema20 < previous_ema20:

        sell_score += 1

        sell_reasons.append(
            "EMA20 falling"
        )

    # كسر قوي لقاع الشمعة السابقة
    breakdown = (
        candle["close"]
        < previous["low"]
    )

    # =====================================================
    # STRONG SIGNAL
    # =====================================================

    direction = None
    reasons = []
    score = 0

    # BUY
    if buy_score >= 3 and buy_score > sell_score:

        direction = "BUY"

        reasons = buy_reasons

        score = buy_score

        # إذا كان الاختراق موجود نضيفه كسبب
        if breakout and score < 4:

            reasons = list(reasons)

            reasons.append(
                "Breakout"
            )

    # SELL
    elif sell_score >= 3 and sell_score > buy_score:

        direction = "SELL"

        reasons = sell_reasons

        score = sell_score

        if breakdown and score < 4:

            reasons = list(reasons)

            reasons.append(
                "Breakdown"
            )

    else:

        return None

    # =====================================================
    # ENTRY / SL / TP
    # =====================================================

    entry = price

    # مخاطرة مبنية على ATR
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

        f"📍 Entry: {signal['entry']:.2f}\n"

        f"🛑 SL: {signal['sl']:.2f}\n"

        f"🎯 TP1: {signal['tp1']:.2f}\n"

        f"🎯 TP2: {signal['tp2']:.2f}\n\n"

        "📊 Timeframe: M15\n"

        f"📈 RSI: {signal['rsi']:.1f}\n"

        f"📏 ATR: {signal['atr']:.2f}\n"

        f"💪 Signal Score: {signal['score']}/4\n\n"

        "🔎 أسباب الإشارة:\n"

        f"{reasons}\n\n"

        "⚠️ إشارة آلية مبنية على بيانات السوق، "
        "وليست ضمانًا للربح."
    )


# =========================================================
# AUTOMATIC MARKET CHECK
# =========================================================

def check_market():

    global last_signal_candle

    try:

        print(
            f"[{datetime.now(timezone.utc)}] "
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

        # منع تكرار نفس الشمعة
        if candle_time == last_signal_candle:

            print(
                "Signal already sent for candle:",
                candle_time
            )

            return

        # حفظ الشمعة
        last_signal_candle = candle_time

        message = format_signal(
            signal
        )

        print("\n" + message + "\n")

        if TELEGRAM_CHAT_ID:

            send_message(
                TELEGRAM_CHAT_ID,
                message
            )

            print(
                "✅ Automatic signal sent to Telegram."
            )

        else:

            print(
                "⚠️ TELEGRAM_CHAT_ID missing."
            )

    except Exception as e:

        print(
            "❌ Market check error:",
            e
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
    # START
    # =====================================================

    if text == "/start":

        send_message(

            chat_id,

            "👋 أهلاً بك في XAU Gold Signals\n\n"

            "🟡 بوت إشارات الذهب XAUUSD\n\n"

            "📡 المراقبة التلقائية مفعلة.\n\n"

            "الأوامر:\n"

            "/test - اختبار البوت\n"

            "/signal - تحليل XAUUSD الآن\n"

            "/status - حالة المراقبة"
        )

    # =====================================================
    # TEST
    # =====================================================

    elif text == "/test":

        send_message(

            chat_id,

            "✅ Telegram bot is working.\n\n"

            "📡 Automatic market monitor is enabled.\n\n"

            "⚠️ هذا اختبار فقط وليس إشارة حقيقية."
        )

    # =====================================================
    # SIGNAL
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

                    "لن نرسل صفقة إجبارية."
                )

        except Exception as e:

            print(
                "Signal error:",
                e
            )

            send_message(

                chat_id,

                "❌ تعذر تحليل XAUUSD حاليًا.\n"

                "حاول مرة أخرى لاحقًا."
            )

    # =====================================================
    # STATUS
    # =====================================================

    elif text == "/status":

        candle = (
            last_signal_candle
            if last_signal_candle
            else "لا يوجد"
        )

        send_message(

            chat_id,

            "🟢 البوت يعمل\n\n"

            "📡 مراقبة XAUUSD: ON\n"

            "⏱ الفحص: كل 60 ثانية\n"

            "📊 Timeframe: M15\n\n"

            f"🕯 آخر شمعة مرسلة:\n{candle}"
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
# TELEGRAM POLLING
# =========================================================

def process_telegram_updates(offset):

    try:

        result = telegram(

            "getUpdates",

            {
                "timeout": 5,
                "offset": offset
            }
        )

        updates = result.get(
            "result",
            []
        )

        for update in updates:

            offset = (
                update["update_id"]
                + 1
            )

            message = update.get(
                "message"
            )

            if message:

                try:

                    handle_message(
                        message
                    )

                except Exception as e:

                    print(
                        "Message handling error:",
                        e
                    )

        return offset

    except Exception as e:

        print(
            "Telegram polling error:",
            e
        )

        time.sleep(3)

        return offset


# =========================================================
# MAIN
# =========================================================

def main():

    global last_signal_candle

    print(
        "================================"
    )

    print(
        "XAU Gold Signals Bot"
    )

    print(
        "Automatic market engine started"
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
        "Check every:",
        CHECK_SECONDS,
        "seconds"
    )

    print(
        "================================"
    )

    # =====================================================
    # INITIAL MARKET CHECK
    # =====================================================

    try:

        candles = get_candles()

        # لا نرسل الإشارة القديمة عند تشغيل البوت.
        # نبدأ من الشمعة الحالية وننتظر شمعة جديدة.
        closed = candles[:-1]

        if closed:

            last_signal_candle = closed[-1][
                "time"
            ]

            print(
                "Startup candle:",
                last_signal_candle
            )

    except Exception as e:

        print(
            "Startup market error:",
            e
        )

    # =====================================================
    # TELEGRAM OFFSET
    # =====================================================

    offset = None

    last_market_check = 0

    # =====================================================
    # MAIN LOOP
    # =====================================================

    while True:

        try:

            # ---------------------------------------------
            # Telegram commands
            # ---------------------------------------------

            offset = process_telegram_updates(
                offset
            )

            # ---------------------------------------------
            # Automatic market monitoring
            # ---------------------------------------------

            now = time.time()

            if (
                now - last_market_check
                >= CHECK_SECONDS
            ):

                last_market_check = now

                check_market()

            # ---------------------------------------------
            # Small sleep
            # ---------------------------------------------

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            break

        except Exception as e:

            print(
                "Main loop error:",
                e
            )

            time.sleep(5)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
