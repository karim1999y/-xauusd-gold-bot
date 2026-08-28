import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone


# =========================================================
# XAU GOLD SIGNALS BOT v5
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

# فحص السوق كل 60 ثانية
CHECK_SECONDS = 60

# أقل عدد شموع بين إشارتين من نفس الاتجاه
COOLDOWN_CANDLES = 3

# الحد الأدنى لقوة الاتجاه بين EMA20 و EMA50
EMA_DISTANCE_ATR = 0.15

# حالة الإشارات
last_signal_candle = None
last_signal_direction = None


# =========================================================
# HTTP / JSON
# =========================================================

def get_json(url, timeout=30):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Gold-Signals/5.0",
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
            f"HTTP {e.code} from market API: "
            f"{body[:500]}"
        ) from e

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"Market connection error: "
            f"{e.reason}"
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
            "User-Agent": "XAU-Gold-Signals/5.0"
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
            f"Telegram HTTP {e.code}: "
            f"{body[:500]}"
        ) from e

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"Telegram connection error: "
            f"{e.reason}"
        ) from e


# =========================================================
# TELEGRAM
# =========================================================

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
            f"Telegram rejected message: "
            f"{result}"
        )

    print(
        "✅ Telegram message sent successfully."
    )

    return result


def check_telegram():

    result = telegram("getMe")

    if not result.get("ok"):

        raise RuntimeError(
            f"Telegram getMe failed: "
            f"{result}"
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

            print(
                "✅ Webhook cleared."
            )

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
# MARKET DATA
# =========================================================

def get_candles():

    params = {
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "outputsize": 150,
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
            + str(
                data.get(
                    "message",
                    data
                )
            )
        )

    values = data.get(
        "values",
        []
    )

    if len(values) < 70:

        raise RuntimeError(
            f"Not enough XAUUSD candles: "
            f"{len(values)}"
        )

    # Twelve Data يرجع الأحدث أولًا
    values = list(
        reversed(values)
    )

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


# =========================================================
# INDICATORS
# =========================================================

def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (
        period + 1
    )

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

    for i in range(
        1,
        len(values)
    ):

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
        100
        / (1 + rs)
    )


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

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

    return sum(
        trs[-period:]
    ) / period


# =========================================================
# CANDLE CONFIRMATION
# =========================================================

def bearish_candle(candle):

    candle_range = (
        candle["high"]
        - candle["low"]
    )

    body = abs(
        candle["close"]
        - candle["open"]
    )

    if candle_range <= 0:
        return False

    # شمعة حمراء
    if candle["close"] >= candle["open"]:
        return False

    # الجسم لازم يكون واضح
    if body / candle_range < 0.35:
        return False

    # الإغلاق ضمن الجزء السفلي من الشمعة
    close_position = (
        candle["close"]
        - candle["low"]
    ) / candle_range

    return close_position <= 0.45


def bullish_candle(candle):

    candle_range = (
        candle["high"]
        - candle["low"]
    )

    body = abs(
        candle["close"]
        - candle["open"]
    )

    if candle_range <= 0:
        return False

    # شمعة خضراء
    if candle["close"] <= candle["open"]:
        return False

    # الجسم لازم يكون واضح
    if body / candle_range < 0.35:
        return False

    # الإغلاق ضمن الجزء العلوي
    close_position = (
        candle["close"]
        - candle["low"]
    ) / candle_range

    return close_position >= 0.55


# =========================================================
# CANDLE INDEX
# =========================================================

def candle_index(
    candles,
    candle_time
):

    for i, candle in enumerate(candles):

        if candle["time"] == candle_time:
            return i

    return None


# =========================================================
# ANALYSIS
# =========================================================

def analyze(candles):

    if len(candles) < 70:
        return None

    # آخر شمعة قد تكون غير مغلقة
    closed = candles[:-1]

    if len(closed) < 65:
        return None

    closes = [
        c["close"]
        for c in closed
    ]

    # -----------------------------------------------------
    # Current indicators
    # -----------------------------------------------------

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

    # -----------------------------------------------------
    # Previous indicators
    # -----------------------------------------------------

    previous_closes = closes[:-1]

    previous_ema20 = ema(
        previous_closes,
        20
    )

    previous_ema50 = ema(
        previous_closes,
        50
    )

    previous_rsi = rsi(
        previous_closes,
        14
    )

    if None in (
        ema20,
        ema50,
        current_rsi,
        current_atr,
        previous_ema20,
        previous_ema50,
        previous_rsi,
    ):
        return None

    # -----------------------------------------------------
    # Candles
    # -----------------------------------------------------

    candle = closed[-1]
    previous = closed[-2]

    price = candle["close"]

    # -----------------------------------------------------
    # EMA distance filter
    # -----------------------------------------------------

    ema_distance = abs(
        ema20 - ema50
    )

    strong_trend = (
        ema_distance
        >= current_atr
        * EMA_DISTANCE_ATR
    )

    # -----------------------------------------------------
    # BUY CONDITIONS
    # -----------------------------------------------------

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

    # RSI لازم يكون صاعد
    if (
        50 <= current_rsi <= 68
        and current_rsi > previous_rsi
    ):

        buy_score += 1

        buy_reasons.append(
            "RSI bullish + rising"
        )

    if ema20 > previous_ema20:

        buy_score += 1

        buy_reasons.append(
            "EMA20 rising"
        )

    if (
        strong_trend
        and ema20 > ema50
    ):

        buy_score += 1

        buy_reasons.append(
            "Strong EMA trend"
        )

    if bullish_candle(candle):

        buy_score += 1

        buy_reasons.append(
            "Bullish candle confirmation"
        )

    # -----------------------------------------------------
    # SELL CONDITIONS
    # -----------------------------------------------------

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

    # RSI لازم يكون هابط
    # ونتجنب البيع عندما RSI منخفض جدًا
    if (
        40 <= current_rsi <= 50
        and current_rsi < previous_rsi
    ):

        sell_score += 1

        sell_reasons.append(
            "RSI bearish + falling"
        )

    if ema20 < previous_ema20:

        sell_score += 1

        sell_reasons.append(
            "EMA20 falling"
        )

    if (
        strong_trend
        and ema20 < ema50
    ):

        sell_score += 1

        sell_reasons.append(
            "Strong EMA trend"
        )

    if bearish_candle(candle):

        sell_score += 1

        sell_reasons.append(
            "Bearish candle confirmation"
        )

    # -----------------------------------------------------
    # Breakout / Breakdown
    # -----------------------------------------------------

    breakout = (
        candle["close"]
        > previous["high"]
    )

    breakdown = (
        candle["close"]
        < previous["low"]
    )

    # -----------------------------------------------------
    # FINAL DIRECTION
    # -----------------------------------------------------

    # نطلب 5 من 6 على الأقل
    # حتى لا يدخل البوت بسبب 3 شروط ضعيفة
    if (
        buy_score >= 5
        and buy_score > sell_score
    ):

        direction = "BUY"
        reasons = list(
            buy_reasons
        )
        score = buy_score

        if breakout:

            reasons.append(
                "Breakout"
            )

    elif (
        sell_score >= 5
        and sell_score > buy_score
    ):

        direction = "SELL"
        reasons = list(
            sell_reasons
        )
        score = sell_score

        if breakdown:

            reasons.append(
                "Breakdown"
            )

    else:

        return None

    # -----------------------------------------------------
    # RISK MANAGEMENT
    # -----------------------------------------------------

    entry = price

    # SL أوسع قليلًا لتجنب ضربه من الضجيج
    risk = current_atr * 1.30

    if direction == "BUY":

        sl = entry - risk

        tp1 = entry + (
            risk * 1.50
        )

        tp2 = entry + (
            risk * 2.20
        )

    else:

        sl = entry + risk

        tp1 = entry - (
            risk * 1.50
        )

        tp2 = entry - (
            risk * 2.20
        )

    return {
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": current_rsi,
        "previous_rsi": previous_rsi,
        "atr": current_atr,
        "score": score,
        "max_score": 6,
        "reasons": reasons,
        "candle_time": candle["time"],
    }


# =========================================================
# SIGNAL FORMAT
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
        "🚨 XAUUSD LIVE SIGNAL v5\n\n"

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

        f"📉 Previous RSI: "
        f"{signal['previous_rsi']:.1f}\n"

        f"📏 ATR: "
        f"{signal['atr']:.2f}\n"

        f"💪 Signal Score: "
        f"{signal['score']}/6\n\n"

        "🔎 أسباب الإشارة:\n"
        f"{reasons}\n\n"

        "🛡️ فلتر v5:\n"
        "• تأكيد شمعة M15\n"
        "• اتجاه RSI\n"
        "• قوة اتجاه EMA\n"
        "• منع الإشارات المتكررة\n\n"

        "⚠️ إشارة آلية مبنية على بيانات السوق، "
        "وليست ضمانًا للربح."
    )


# =========================================================
# COOLDOWN
# =========================================================

def allowed_by_cooldown(
    candles,
    signal
):

    global last_signal_candle
    global last_signal_direction

    if (
        last_signal_candle is None
        or last_signal_direction is None
    ):

        return True

    # إذا الاتجاه تغير
    # نسمح بالإشارة الجديدة
    if (
        signal["direction"]
        != last_signal_direction
    ):

        return True

    current_index = candle_index(
        candles,
        signal["candle_time"]
    )

    last_index = candle_index(
        candles,
        last_signal_candle
    )

    if (
        current_index is None
        or last_index is None
    ):

        return True

    candles_passed = (
        current_index
        - last_index
    )

    if (
        candles_passed
        < COOLDOWN_CANDLES
    ):

        print(
            "⏳ Same direction cooldown:",
            candles_passed,
            "/",
            COOLDOWN_CANDLES
        )

        return False

    return True


# =========================================================
# MARKET CHECK
# =========================================================

def check_market():

    global last_signal_candle
    global last_signal_direction

    try:

        now = datetime.now(
            timezone.utc
        ).isoformat()

        print(
            f"[{now}] "
            "Checking XAUUSD..."
        )

        candles = get_candles()

        if len(candles) < 2:

            print(
                "No sufficient candles."
            )

            return

        closed = candles[:-1]

        if not closed:
            return

        latest_closed_time = (
            closed[-1]["time"]
        )

        print(
            "Latest closed M15 candle:",
            latest_closed_time
        )

        signal = analyze(
            candles
        )

        if not signal:

            print(
                "⏳ No strong signal."
            )

            return

        print(
            "Signal candidate:",
            signal["direction"],
            "Score:",
            signal["score"],
            "/6",
            signal["candle_time"]
        )

        candle_time = (
            signal["candle_time"]
        )

        # -------------------------------------------------
        # نفس الشمعة
        # -------------------------------------------------

        if (
            candle_time
            == last_signal_candle
        ):

            print(
                "Already handled candle:",
                candle_time
            )

            return

        # -------------------------------------------------
        # Cooldown
        # -------------------------------------------------

        if not allowed_by_cooldown(
            candles,
            signal
        ):

            return

        # -------------------------------------------------
        # Telegram
        # -------------------------------------------------

        if not TELEGRAM_CHAT_ID:

            print(
                "❌ TELEGRAM_CHAT_ID missing."
            )

            return

        message = format_signal(
            signal
        )

        result = send_message(
            TELEGRAM_CHAT_ID,
            message
        )

        if result.get("ok"):

            last_signal_candle = (
                candle_time
            )

            last_signal_direction = (
                signal["direction"]
            )

            print(
                "🚨 AUTOMATIC SIGNAL SENT!"
            )

            print(
                "Direction:",
                last_signal_direction
            )

            print(
                "Saved candle:",
                last_signal_candle
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
        message
        .get("text", "")
        .strip()
    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text == "/start":

        send_message(
            chat_id,

            "👋 أهلاً بك في "
            "XAU Gold Signals v5\n\n"

            "📡 المراقبة التلقائية مفعلة.\n\n"

            "🛡️ نظام الدخول الجديد:\n"
            "• M15\n"
            "• EMA20 / EMA50\n"
            "• RSI اتجاهي\n"
            "• تأكيد شمعة\n"
            "• فلتر قوة الاتجاه\n"
            "• Cooldown للإشارات\n\n"

            "/test - اختبار Telegram\n"
            "/signal - تحليل XAUUSD الآن\n"
            "/status - حالة المراقبة"
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

            "🛡️ Strategy: XAU Gold Signals v5\n\n"

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

                    "v5 ينتظر اجتماع "
                    "شروط الاتجاه + RSI + EMA "
                    "+ شمعة التأكيد.\n\n"

                    "🛡️ لن نرسل صفقة إجبارية."
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

            "⏱ الفحص: كل 60 ثانية\n"

            "📊 Timeframe: M15\n"

            "🛡️ Strategy: v5\n"

            "💪 Minimum Score: 5/6\n"

            f"⏳ Cooldown: "
            f"{COOLDOWN_CANDLES} candles\n\n"

            "🕯 آخر شمعة تم إرسال إشارة عليها:\n"
            f"{last_signal_candle or 'لا يوجد'}\n\n"

            "↔️ آخر اتجاه:\n"
            f"{last_signal_direction or 'لا يوجد'}"
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
                "⚠️ تأكد من تشغيل نسخة Railway واحدة فقط."
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
    global last_signal_direction

    print(
        "================================"
    )

    print(
        "XAU Gold Signals Bot v5"
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
        "Minimum score: 5/6"
    )

    print(
        "Cooldown:",
        COOLDOWN_CANDLES,
        "candles"
    )

    print(
        "================================"
    )

    # -----------------------------------------------------
    # Telegram startup
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
    # Market startup
    # -----------------------------------------------------

    try:

        candles = get_candles()

        closed = candles[:-1]

        if closed:

            current_candle = (
                closed[-1]["time"]
            )

            print(
                "Startup candle:",
                current_candle
            )

            # مهم:
            # لا نرسل إشارة قديمة عند إعادة تشغيل Railway
            last_signal_candle = (
                current_candle
            )

            last_signal_direction = None

            print(
                "ℹ️ Startup completed."
            )

            print(
                "ℹ️ Waiting for a new "
                "confirmed M15 signal."
            )

    except Exception as e:

        print(
            "❌ Startup market error:",
            repr(e)
        )

    # -----------------------------------------------------
    # Main loop
    # -----------------------------------------------------

    offset = None

    last_market_check = 0

    while True:

        try:

            # Telegram
            offset = (
                process_telegram_updates(
                    offset
                )
            )

            # Market
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
