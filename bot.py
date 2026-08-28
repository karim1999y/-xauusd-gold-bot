import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone

# =========================================================
# XAU GOLD SIGNALS v6
# DATA SOURCE: BiQuote
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")

if not TELEGRAM_CHAT_ID:
    print("WARNING: TELEGRAM_CHAT_ID is not set")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# =========================================================
# CONFIG
# =========================================================

SYMBOL = "XAUUSD"
INTERVAL = "15m"

# يفحص كل 60 ثانية،
# لكن لا يحلل إلا عند ظهور شمعة M15 مغلقة جديدة.
CHECK_SECONDS = 60

# عدد الشموع المطلوبة للتحليل
CANDLE_LIMIT = 120

# لمنع تكرار نفس الإشارة
last_signal_candle = None


# =========================================================
# HTTP
# =========================================================

def get_json(url, timeout=30):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Gold-Signals/6.0",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:

        with urllib.request.urlopen(
            req,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(raw)

    except urllib.error.HTTPError as e:

        body = e.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"HTTP {e.code}: {body[:500]}"
        ) from e

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"Connection error: {e.reason}"
        ) from e


# =========================================================
# TELEGRAM
# =========================================================

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
            "User-Agent": "XAU-Gold-Signals/6.0"
        },
        method=(
            "POST"
            if encoded is not None
            else "GET"
        ),
    )

    try:

        with urllib.request.urlopen(
            req,
            timeout=timeout
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

            return json.loads(raw)

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

    print("✅ Telegram message sent.")

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


# =========================================================
# BIQUOTE MARKET DATA
# =========================================================

def get_candles():

    params = {
        "interval": INTERVAL,
        "limit": CANDLE_LIMIT,
    }

    url = (
        "https://biquote.io/api/"
        + SYMBOL
        + "/ohlc?"
        + urllib.parse.urlencode(params)
    )

    data = get_json(url)

    if not isinstance(data, dict):

        raise RuntimeError(
            "Invalid BiQuote response."
        )

    bars = data.get("bars", [])

    if len(bars) < 60:

        raise RuntimeError(
            f"Not enough XAUUSD candles: {len(bars)}"
        )

    candles = []

    for bar in bars:

        candles.append(
            {
                "time": bar["openTime"],
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "is_open": bool(
                    bar.get("isOpen", False)
                ),
            }
        )

    # BiQuote يرجع الأحدث أولًا.
    # نقلبها حتى تصبح الأقدم -> الأحدث.

    candles.reverse()

    return candles


# =========================================================
# INDICATORS
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
            (price - result)
            * multiplier
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

    rs = (
        avg_gain
        / avg_loss
    )

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

        previous_close = (
            candles[i - 1]["close"]
        )

        tr = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            ),
        )

        trs.append(tr)

    return (
        sum(trs[-period:])
        / period
    )


# =========================================================
# ANALYSIS
# =========================================================

def analyze(candles):

    # نستبعد أي شمعة ما زالت مفتوحة.
    closed = [
        c for c in candles
        if not c["is_open"]
    ]

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

    # =====================================================
    # BUY SCORE
    # =====================================================

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

    # =====================================================
    # SELL SCORE
    # =====================================================

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

    # =====================================================
    # BREAKOUT / BREAKDOWN
    # =====================================================

    breakout = (
        candle["close"]
        > previous["high"]
    )

    breakdown = (
        candle["close"]
        < previous["low"]
    )

    # =====================================================
    # FINAL SIGNAL
    # =====================================================

    if (
        buy_score >= 3
        and buy_score > sell_score
    ):

        direction = "BUY"

        reasons = list(
            buy_reasons
        )

        score = buy_score

        if (
            breakout
            and score < 4
        ):

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

        if (
            breakdown
            and score < 4
        ):

            reasons.append(
                "Breakdown"
            )

    else:

        return None

    # =====================================================
    # RISK
    # =====================================================

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


# =========================================================
# FORMAT SIGNAL
# =========================================================

def format_signal(signal):

    if signal["direction"] == "BUY":

        emoji = "🟢"
        direction_ar = "شراء"

    else:

        emoji = "🔴"
        direction_ar = "بيع"

    reasons = "\n".join(
        f"• {x}"
        for x in signal["reasons"]
    )

    return (

        "🚨 XAUUSD LIVE SIGNAL\n\n"

        f"{emoji} الاتجاه: "
        f"{direction_ar} "
        f"({signal['direction']})\n\n"

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

        "⚠️ إشارة آلية مبنية على بيانات السوق، "
        "وليست ضمانًا للربح."
    )


# =========================================================
# MARKET CHECK
# =========================================================

def check_market():

    global last_signal_candle

    try:

        now = datetime.now(
            timezone.utc
        ).isoformat()

        print(
            f"[{now}] "
            "Checking XAUUSD..."
        )

        candles = get_candles()

        closed = [
            c for c in candles
            if not c["is_open"]
        ]

        if not closed:

            print(
                "No closed candles."
            )

            return

        latest_candle = closed[-1]

        latest_time = (
            latest_candle["time"]
        )

        print(
            "Latest closed M15:",
            latest_time
        )

        # -------------------------------------------------
        # لا نحلل نفس الشمعة أكثر من مرة
        # -------------------------------------------------

        if (
            latest_time
            == last_signal_candle
        ):

            print(
                "Same candle already checked."
            )

            return

        # -------------------------------------------------
        # نسجل الشمعة التي تم فحصها
        # حتى لا نكررها كل دقيقة.
        # -------------------------------------------------

        last_signal_candle = (
            latest_time
        )

        signal = analyze(candles)

        if not signal:

            print(
                "⏳ No valid signal."
            )

            return

        print(
            "🚨 Signal:",
            signal["direction"],
            signal["score"],
            "/4"
        )

        if not TELEGRAM_CHAT_ID:

            print(
                "❌ TELEGRAM_CHAT_ID missing."
            )

            return

        message = format_signal(
            signal
        )

        send_message(
            TELEGRAM_CHAT_ID,
            message
        )

        print(
            "✅ SIGNAL SENT."
        )

    except Exception as e:

        print(
            "❌ Market check error:",
            repr(e)
        )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_message(message):

    chat_id = message["chat"]["id"]

    text = (
        message.get("text", "")
        .strip()
    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text == "/start":

        send_message(
            chat_id,

            "👋 أهلاً بك في "
            "XAU Gold Signals\n\n"

            "📡 المراقبة التلقائية مفعلة.\n\n"

            "🟢 Data Source: BiQuote\n"
            "🟡 XAUUSD M15\n\n"

            "/test - اختبار Telegram\n"
            "/signal - تحليل XAUUSD الآن\n"
            "/status - حالة البوت"
        )

    # -----------------------------------------------------
    # TEST
    # -----------------------------------------------------

    elif text == "/test":

        send_message(
            chat_id,

            "✅ TEST SUCCESS\n\n"

            "Telegram connection is working.\n"

            "📡 Automatic market monitor "
            "is enabled.\n\n"

            "🟢 Data Source: BiQuote\n"
            "🥇 Symbol: XAUUSD\n"
            "📊 Timeframe: M15\n\n"

            "⚠️ هذا اختبار فقط وليس إشارة حقيقية."
        )

    # -----------------------------------------------------
    # SIGNAL
    # -----------------------------------------------------

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

                    "شروط BUY/SELL لم تجتمع "
                    "بدرجة كافية.\n\n"

                    "لن نرسل صفقة إجبارية."
                )

        except Exception as e:

            print(
                "Signal command error:",
                repr(e)
            )

            try:

                send_message(
                    chat_id,

                    "❌ تعذر تحليل XAUUSD حاليًا.\n\n"
                    f"السبب التقني:\n{e}"
                )

            except Exception as telegram_error:

                print(
                    "Telegram error:",
                    repr(telegram_error)
                )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    elif text == "/status":

        send_message(
            chat_id,

            "🟢 البوت يعمل\n\n"

            "📡 مراقبة XAUUSD: ON\n"

            "🟢 Data Source: BiQuote\n"

            "⏱ الفحص: كل 60 ثانية\n"

            "📊 Timeframe: M15\n\n"

            "🕯 آخر شمعة تم فحصها:\n"
            f"{last_signal_candle or 'لا يوجد'}"
        )

    # -----------------------------------------------------
    # UNKNOWN
    # -----------------------------------------------------

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
                "offset": offset,
                "allowed_updates": json.dumps(
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
                "⚠️ يوجد Bot instance آخر "
                "يستخدم getUpdates."
            )

            print(
                "⚠️ شغّل نسخة Railway واحدة فقط."
            )

            time.sleep(10)

            return offset

        print(
            "Telegram polling error:",
            repr(e)
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
        "XAU Gold Signals Bot v6"
    )

    print(
        "Data Source: BiQuote"
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

    # -----------------------------------------------------
    # TELEGRAM
    # -----------------------------------------------------

    try:

        check_telegram()
        clear_webhook()

    except Exception as e:

        print(
            "❌ Telegram startup error:",
            repr(e)
        )

    # -----------------------------------------------------
    # MARKET STARTUP
    # -----------------------------------------------------

    try:

        candles = get_candles()

        closed = [
            c for c in candles
            if not c["is_open"]
        ]

        if closed:

            print(
                "Latest closed candle:",
                closed[-1]["time"]
            )

            print(
                "✅ BiQuote market data OK."
            )

    except Exception as e:

        print(
            "❌ BiQuote startup error:",
            repr(e)
        )

    # -----------------------------------------------------
    # MAIN LOOP
    # -----------------------------------------------------

    offset = None
    last_market_check = 0

    while True:

        try:

            offset = (
                process_telegram_updates(
                    offset
                )
            )

            now = time.time()

            if (
                now - last_market_check
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


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":
    main()
