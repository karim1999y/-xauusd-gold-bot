# XAU Gold Signals Bot v4
# Telegram + Twelve Data
# Automatic XAUUSD M15 monitoring

import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TWELVE_DATA_API_KEY = os.getenv("TWELVE_DATA_API_KEY")

# Optional:
# إذا كان موجودًا سيستخدمه البوت،
# وإذا لم يكن موجودًا سيتعلم Chat ID تلقائيًا من /start
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

SYMBOL = "XAU/USD"
INTERVAL = "15min"

# فحص السوق كل 60 ثانية
CHECK_SECONDS = 60


# ============================================================
# GLOBAL STATE
# ============================================================

target_chat_id = str(TELEGRAM_CHAT_ID) if TELEGRAM_CHAT_ID else None

# آخر شمعة تم إرسال إشارة عنها
last_signal_candle = None

# آخر شمعة مغلقة تمت رؤيتها
last_closed_candle = None


# ============================================================
# VALIDATE ENVIRONMENT
# ============================================================

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")

if not TWELVE_DATA_API_KEY:
    raise RuntimeError("TWELVE_DATA_API_KEY is not set")

if not target_chat_id:
    print("ℹ️ TELEGRAM_CHAT_ID not set.")
    print("ℹ️ Bot will learn Chat ID automatically from /start.")
else:
    print("✅ TELEGRAM_CHAT_ID loaded.")


# ============================================================
# HTTP / JSON
# ============================================================

def get_json(url, timeout=30):
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Gold-Signals/4.0",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return json.loads(body)

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"HTTP {e.code} from market API: {body[:500]}"
        ) from e

    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Market connection error: {e.reason}"
        ) from e


def telegram(method, data=None, timeout=35):
    url = f"{TELEGRAM_API}/{method}"

    encoded = None

    if data:
        encoded = urllib.parse.urlencode(
            {
                k: v
                for k, v in data.items()
                if v is not None
            }
        ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "User-Agent": "XAU-Gold-Signals/4.0"
        },
        method="POST" if encoded is not None else "GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode(
                "utf-8",
                errors="replace"
            )
            return json.loads(body)

    except urllib.error.HTTPError as e:
        body = e.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"Telegram HTTP {e.code}: {body[:500]}"
        ) from e

    except urllib.error.URLError as e:
        raise RuntimeError(
            f"Telegram connection error: {e.reason}"
        ) from e


# ============================================================
# TELEGRAM
# ============================================================

def send_message(chat_id, text):
    result = telegram(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
        }
    )

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram rejected message: {result}"
        )

    print("✅ Telegram accepted the message.")

    return result


def check_telegram():
    result = telegram("getMe")

    if not result.get("ok"):
        raise RuntimeError(
            f"Telegram getMe failed: {result}"
        )

    username = (
        result
        .get("result", {})
        .get("username", "unknown")
    )

    print(
        "✅ Telegram connection:",
        username
    )


def clear_webhook():
    try:
        result = telegram(
            "deleteWebhook",
            {
                "drop_pending_updates": "false"
            }
        )

        if result.get("ok"):
            print("✅ Webhook cleared.")
        else:
            print(
                "⚠️ Webhook warning:",
                result
            )

    except Exception as e:
        print(
            "⚠️ Webhook warning:",
            e
        )


# ============================================================
# MARKET DATA
# ============================================================

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
        raise RuntimeError(
            "Twelve Data error: "
            + str(data.get("message", data))
        )

    values = data.get("values", [])

    if len(values) < 60:
        raise RuntimeError(
            f"Not enough XAUUSD candles: {len(values)}"
        )

    # Twelve Data يرجع الأحدث أولًا
    # نعكسها لتصبح الأقدم -> الأحدث
    values = list(reversed(values))

    candles = []

    for c in values:
        candles.append(
            {
                "time": c["datetime"],
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
            }
        )

    return candles


# ============================================================
# INDICATORS
# ============================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(values[:period]) / period

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
            values[i]
            - values[i - 1]
        )

        gains.append(
            max(change, 0)
        )

        losses.append(
            max(-change, 0)
        )

    avg_gain = (
        sum(gains[:period])
        / period
    )

    avg_loss = (
        sum(losses[:period])
        / period
    )

    if avg_loss == 0:
        return 100

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i]
        ) / period

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (
        100 / (1 + rs)
    )


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        trs.append(true_range)

    return (
        sum(trs[-period:])
        / period
    )


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze(candles):

    # آخر شمعة قد تكون ما زالت مفتوحة
    # لذلك نحلل الشموع المغلقة فقط
    closed = candles[:-1]

    if len(closed) < 60:
        return None

    closes = [
        c["close"]
        for c in closed
    ]

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

    previous_ema20 = ema(
        closes[:-1],
        20
    )

    if None in (
        ema20,
        ema50,
        current_rsi,
        current_atr,
        previous_ema20,
    ):
        return None

    candle = closed[-1]
    previous = closed[-2]

    price = candle["close"]

    # ========================================================
    # BUY SCORE
    # ========================================================

    buy_score = 0
    buy_reasons = []

    if ema20 > ema50:
        buy_score += 1
        buy_reasons.append(
            "EMA20 > EMA50"
        )

    if price > ema20:
        buy_score += 1
        buy_reasons.append(
            "Price > EMA20"
        )

    if 52 <= current_rsi <= 70:
        buy_score += 1
        buy_reasons.append(
            "RSI bullish"
        )

    if ema20 > previous_ema20:
        buy_score += 1
        buy_reasons.append(
            "EMA20 rising"
        )

    # ========================================================
    # SELL SCORE
    # ========================================================

    sell_score = 0
    sell_reasons = []

    if ema20 < ema50:
        sell_score += 1
        sell_reasons.append(
            "EMA20 < EMA50"
        )

    if price < ema20:
        sell_score += 1
        sell_reasons.append(
            "Price < EMA20"
        )

    if 30 <= current_rsi <= 48:
        sell_score += 1
        sell_reasons.append(
            "RSI bearish"
        )

    if ema20 < previous_ema20:
        sell_score += 1
        sell_reasons.append(
            "EMA20 falling"
        )

    # ========================================================
    # BREAKOUT / BREAKDOWN
    # ========================================================

    breakout = (
        candle["close"]
        > previous["high"]
    )

    breakdown = (
        candle["close"]
        < previous["low"]
    )

    # ========================================================
    # DIRECTION
    # ========================================================

    if (
        buy_score >= 3
        and buy_score > sell_score
    ):

        direction = "BUY"
        reasons = list(
            buy_reasons
        )
        score = buy_score

        if breakout and score < 4:
            reasons.append(
                "Breakout"
            )

    elif (
        sell_score >= 3
        and sell_score > buy_score
    ):

        direction = "SELL"
        reasons = list(
            sell_reasons
        )
        score = sell_score

        if breakdown and score < 4:
            reasons.append(
                "Breakdown"
            )

    else:
        return None

    # ========================================================
    # ENTRY / SL / TP
    # ========================================================

    entry = price

    risk = (
        current_atr * 1.2
    )

    if direction == "BUY":

        sl = entry - risk

        tp1 = (
            entry
            + risk * 1.5
        )

        tp2 = (
            entry
            + risk * 2.2
        )

    else:

        sl = entry + risk

        tp1 = (
            entry
            - risk * 1.5
        )

        tp2 = (
            entry
            - risk * 2.2
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
        "candle_time": candle["time"],
    }


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    if signal["direction"] == "BUY":
        emoji = "🟢"
    else:
        emoji = "🔴"

    reasons = "\n".join(
        f"• {reason}"
        for reason in signal["reasons"]
    )

    return (
        "🚨 XAUUSD LIVE SIGNAL\n\n"

        f"{emoji} الاتجاه: "
        f"{signal['direction']}\n\n"

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

        "⚠️ إشارة آلية مبنية "
        "على بيانات السوق، "
        "وليست ضمانًا للربح."
    )


# ============================================================
# AUTOMATIC MARKET CHECK
# ============================================================

def check_market():

    global last_signal_candle
    global last_closed_candle

    try:

        now_utc = datetime.now(
            timezone.utc
        ).isoformat()

        print(
            f"[{now_utc}] "
            "Checking XAUUSD..."
        )

        candles = get_candles()

        closed = candles[:-1]

        if not closed:
            print(
                "No closed candles."
            )
            return

        current_closed_time = (
            closed[-1]["time"]
        )

        print(
            "Latest closed M15 candle:",
            current_closed_time
        )

        # ====================================================
        # NEW CANDLE DETECTION
        # ====================================================

        if (
            last_closed_candle
            == current_closed_time
        ):

            print(
                "Same closed candle. "
                "Waiting for next M15 candle."
            )

            return

        # حفظ آخر شمعة تمت رؤيتها
        last_closed_candle = (
            current_closed_time
        )

        # ====================================================
        # ANALYZE
        # ====================================================

        signal = analyze(candles)

        if not signal:

            print(
                "⏳ No valid signal "
                "on this M15 candle."
            )

            return

        candle_time = (
            signal["candle_time"]
        )

        print(
            f"Signal candidate: "
            f"{signal['direction']} "
            f"on {candle_time}"
        )

        # ====================================================
        # DUPLICATE PROTECTION
        # ====================================================

        if (
            candle_time
            == last_signal_candle
        ):

            print(
                "Already sent:",
                candle_time
            )

            return

        # ====================================================
        # CHAT ID
        # ====================================================

        if not target_chat_id:

            print(
                "❌ No Telegram Chat ID."
            )

            print(
                "Send /start to the bot "
                "once so it can learn "
                "your Chat ID."
            )

            return

        # ====================================================
        # SEND
        # ====================================================

        message = format_signal(
            signal
        )

        result = send_message(
            target_chat_id,
            message
        )

        if result.get("ok"):

            last_signal_candle = (
                candle_time
            )

            print(
                "================================"
            )

            print(
                "✅ AUTOMATIC SIGNAL "
                "SENT SUCCESSFULLY"
            )

            print(
                "Direction:",
                signal["direction"]
            )

            print(
                "Candle:",
                candle_time
            )

            print(
                "Entry:",
                f"{signal['entry']:.2f}"
            )

            print(
                "Score:",
                f"{signal['score']}/4"
            )

            print(
                "================================"
            )

    except Exception as e:

        print(
            "❌ Market check error:",
            repr(e)
        )


# ============================================================
# TELEGRAM COMMAND HANDLER
# ============================================================

def handle_message(message):

    global target_chat_id

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get("id")

    if not chat_id:
        print(
            "⚠️ Message has no chat ID."
        )
        return

    # ========================================================
    # AUTO LEARN CHAT ID
    # ========================================================

    target_chat_id = str(
        chat_id
    )

    print(
        "✅ Telegram Chat ID learned:",
        target_chat_id
    )

    text = (
        message
        .get("text", "")
        .strip()
    )

    # ========================================================
    # /START
    # ========================================================

    if text == "/start":

        send_message(
            chat_id,

            "👋 أهلاً بك في "
            "XAU Gold Signals\n\n"

            "🟢 البوت يعمل الآن.\n"
            "📡 المراقبة التلقائية مفعلة.\n\n"

            "سيتم فحص XAUUSD "
            "كل 60 ثانية.\n"

            "📊 Timeframe: M15\n\n"

            "/test - اختبار Telegram\n"
            "/signal - تحليل XAUUSD الآن\n"
            "/status - حالة المراقبة"
        )

    # ========================================================
    # /TEST
    # ========================================================

    elif text == "/test":

        send_message(
            chat_id,

            "✅ TEST SUCCESS\n\n"

            "Telegram connection "
            "is working.\n\n"

            "📡 Automatic market "
            "monitor is enabled.\n\n"

            "⚠️ هذا اختبار فقط "
            "وليس إشارة حقيقية."
        )

    # ========================================================
    # /SIGNAL
    # ========================================================

    elif text == "/signal":

        try:

            candles = get_candles()

            signal = analyze(
                candles
            )

            if signal:

                send_message(
                    chat_id,
                    format_signal(
                        signal
                    )
                )

            else:

                send_message(
                    chat_id,

                    "⏳ لا توجد إشارة "
                    "قوية حاليًا.\n\n"

                    "لن نرسل صفقة "
                    "إجبارية."
                )

        except Exception as e:

            print(
                "Signal command error:",
                repr(e)
            )

            try:

                send_message(
                    chat_id,

                    "❌ تعذر تحليل "
                    "XAUUSD حاليًا.\n\n"

                    f"السبب التقني:\n{e}"
                )

            except Exception as te:

                print(
                    "Telegram error:",
                    repr(te)
                )

    # ========================================================
    # /STATUS
    # ========================================================

    elif text == "/status":

        send_message(
            chat_id,

            "🟢 البوت يعمل\n\n"

            "📡 مراقبة XAUUSD: ON\n"

            "⏱ الفحص: كل 60 ثانية\n"

            "📊 Timeframe: M15\n"

            f"💬 Chat ID: "
            f"{target_chat_id}\n\n"

            f"🕯 آخر شمعة تم إرسال "
            f"إشارة عنها:\n"
            f"{last_signal_candle or 'لا يوجد'}\n\n"

            f"🕯 آخر شمعة مغلقة تمت "
            f"معالجتها:\n"
            f"{last_closed_candle or 'لا يوجد'}"
        )

    # ========================================================
    # UNKNOWN COMMAND
    # ========================================================

    else:

        send_message(
            chat_id,

            "الأوامر المتاحة:\n\n"

            "/start - تشغيل البوت\n"
            "/test - اختبار الاتصال\n"
            "/signal - تحليل XAUUSD الآن\n"
            "/status - حالة البوت"
        )


# ============================================================
# TELEGRAM UPDATES
# ============================================================

def process_telegram_updates(
    offset
):

    try:

        result = telegram(
            "getUpdates",
            {
                "timeout": 5,
                "offset": offset,

                "allowed_updates":
                    json.dumps(
                        ["message"]
                    ),
            },

            timeout=15
        )

        if not result.get("ok"):

            print(
                "Telegram getUpdates failed:",
                result
            )

            return offset

        updates = (
            result.get(
                "result",
                []
            )
        )

        for update in updates:

            offset = (
                update["update_id"]
                + 1
            )

            message = (
                update.get(
                    "message"
                )
            )

            if message:

                try:

                    handle_message(
                        message
                    )

                except Exception as e:

                    print(
                        "Message handling error:",
                        repr(e)
                    )

        return offset

    except Exception as e:

        text = str(e)

        if (
            "409" in text
            or "Conflict" in text
        ):

            print(
                "❌ TELEGRAM 409 CONFLICT"
            )

            print(
                "⚠️ يوجد أكثر من نسخة "
                "من البوت تستخدم getUpdates."
            )

            print(
                "⚠️ يجب تشغيل نسخة واحدة "
                "فقط على Railway."
            )

            time.sleep(10)

            return offset

        print(
            "Telegram polling error:",
            repr(e)
        )

        time.sleep(3)

        return offset


# ============================================================
# MAIN
# ============================================================

def main():

    global last_signal_candle
    global last_closed_candle

    print(
        "================================"
    )

    print(
        "XAU Gold Signals Bot v4"
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

    # ========================================================
    # TELEGRAM STARTUP
    # ========================================================

    try:

        check_telegram()

        clear_webhook()

    except Exception as e:

        print(
            "❌ Telegram startup error:",
            repr(e)
        )

    # ========================================================
    # MARKET STARTUP
    # ========================================================

    try:

        candles = get_candles()

        closed = candles[:-1]

        if closed:

            # لا نرسل إشارة قديمة عند إعادة التشغيل.
            # ننتظر شمعة M15 جديدة.
            last_closed_candle = (
                closed[-1]["time"]
            )

            print(
                "Startup candle:",
                last_closed_candle
            )

            print(
                "ℹ️ Waiting for next "
                "new closed M15 candle."
            )

    except Exception as e:

        print(
            "❌ Startup market error:",
            repr(e)
        )

    # ========================================================
    # MAIN LOOP
    # ========================================================

    offset = None

    last_market_check = 0

    while True:

        try:

            # أولًا نستقبل أوامر Telegram
            offset = (
                process_telegram_updates(
                    offset
                )
            )

            # ثم نفحص السوق
            now = time.time()

            if (
                now
                - last_market_check
                >= CHECK_SECONDS
            ):

                last_market_check = now

                check_market()

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "Bot stopped."
            )

            break

        except Exception as e:

            print(
                "❌ Main loop error:",
                repr(e)
            )

            time.sleep(5)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
