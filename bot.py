import os
import time
import json
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone


# =========================================================
# CONFIG
# =========================================================

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not TELEGRAM_TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")

if not TELEGRAM_CHAT_ID:
    print("WARNING: TELEGRAM_CHAT_ID is not set")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"

# ---------------------------------------------------------
# BiQuote
# ---------------------------------------------------------

BIQUOTE_BASE = "https://biquote.io/api"

# نفحص كل دقيقة
CHECK_SECONDS = 60

# ---------------------------------------------------------
# العملات التي نراقبها
# ---------------------------------------------------------

SYMBOLS = [
    "XAUUSD",

    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "AUDUSD",
    "USDCAD",
    "NZDUSD",

    "EURJPY",
    "GBPJPY",
]

# ---------------------------------------------------------
# الإعدادات
# ---------------------------------------------------------

M15_INTERVAL = "15m"
H1_INTERVAL = "1h"

M15_BARS = 250
H1_BARS = 250

MIN_SCORE = 8

# أقل R:R مطلوب
MIN_RR = 1.5

# منع إرسال أكثر من إشارة لنفس الرمز خلال هذه المدة
COOLDOWN_MINUTES = 60

# ATR لاستخدامه في SL
SL_ATR_MULTIPLIER = 1.35

# TP الأساسي
TP1_R_MULTIPLIER = 1.5
TP2_R_MULTIPLIER = 2.2

# إذا كان السعر بعيدًا جدًا عن EMA20 لا ندخل
MAX_DISTANCE_ATR = 1.8

# RSI
BUY_RSI_MIN = 52
BUY_RSI_MAX = 68

SELL_RSI_MIN = 32
SELL_RSI_MAX = 48


# =========================================================
# STATE
# =========================================================

last_signal_candle = {}

last_signal_time = {}


# =========================================================
# HTTP
# =========================================================

def get_json(url, timeout=25):

    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "XAU-Forex-Signals-Final/1.0",
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
        )

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"Connection error: {e.reason}"
        )


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, data=None, timeout=30):

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
            "User-Agent": "XAU-Forex-Signals-Final/1.0"
        },
        method="POST" if encoded else "GET",
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
        )


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
        "✅ Telegram:",
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
                "✅ Telegram webhook cleared."
            )

    except Exception as e:

        print(
            "Webhook warning:",
            e
        )


# =========================================================
# BIQUOTE MARKET DATA
# =========================================================

def get_ohlc(symbol, interval, limit):

    url = (
        f"{BIQUOTE_BASE}/"
        f"{symbol}/ohlc?"
        + urllib.parse.urlencode(
            {
                "interval": interval,
                "limit": limit,
            }
        )
    )

    data = get_json(url)

    if not isinstance(data, dict):

        raise RuntimeError(
            f"Invalid BiQuote response for {symbol}"
        )

    bars = data.get("bars", [])

    if len(bars) < 60:

        raise RuntimeError(
            f"Not enough {symbol} {interval} bars: "
            f"{len(bars)}"
        )

    candles = []

    for bar in bars:

        candles.append(
            {
                "time": bar.get("openTime"),

                "open": float(bar["open"]),

                "high": float(bar["high"]),

                "low": float(bar["low"]),

                "close": float(bar["close"]),

                "tick_volume": float(
                    bar.get("tickVolume", 0)
                ),

                "is_open": bool(
                    bar.get("isOpen", False)
                ),
            }
        )

    # BiQuote عادة يرجع الأحدث أولاً
    candles.sort(
        key=lambda x: x["time"]
    )

    # نستخدم فقط الشموع المغلقة
    candles = [
        c for c in candles
        if not c["is_open"]
    ]

    return candles


def get_tick(symbol):

    url = (
        f"{BIQUOTE_BASE}/{symbol}"
    )

    data = get_json(url)

    if not isinstance(data, dict):

        return None

    return data


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


def ema_series(values, period):

    if len(values) < period:

        return []

    multiplier = 2 / (period + 1)

    result = (
        sum(values[:period])
        / period
    )

    output = [
        None
    ] * (period - 1)

    output.append(result)

    for price in values[period:]:

        result = (
            (price - result)
            * multiplier
            + result
        )

        output.append(result)

    return output


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

    return (
        sum(trs[-period:])
        / period
    )


def adx(candles, period=14):

    if len(candles) < (
        period * 2 + 5
    ):

        return None

    trs = []
    plus_dm = []
    minus_dm = []

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]

        prev_high = (
            candles[i - 1]["high"]
        )

        prev_low = (
            candles[i - 1]["low"]
        )

        prev_close = (
            candles[i - 1]["close"]
        )

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )

        up_move = (
            high - prev_high
        )

        down_move = (
            prev_low - low
        )

        if (
            up_move > down_move
            and up_move > 0
        ):

            pdm = up_move

        else:

            pdm = 0

        if (
            down_move > up_move
            and down_move > 0
        ):

            mdm = down_move

        else:

            mdm = 0

        trs.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    if len(trs) < period:

        return None

    atr_value = (
        sum(trs[:period])
        / period
    )

    plus_value = (
        sum(plus_dm[:period])
        / period
    )

    minus_value = (
        sum(minus_dm[:period])
        / period
    )

    dx_values = []

    for i in range(
        period,
        len(trs)
    ):

        atr_value = (
            (
                atr_value
                * (period - 1)
            )
            + trs[i]
        ) / period

        plus_value = (
            (
                plus_value
                * (period - 1)
            )
            + plus_dm[i]
        ) / period

        minus_value = (
            (
                minus_value
                * (period - 1)
            )
            + minus_dm[i]
        ) / period

        if atr_value == 0:

            continue

        plus_di = (
            100
            * plus_value
            / atr_value
        )

        minus_di = (
            100
            * minus_value
            / atr_value
        )

        denominator = (
            plus_di
            + minus_di
        )

        if denominator == 0:

            continue

        dx = (
            100
            * abs(
                plus_di
                - minus_di
            )
            / denominator
        )

        dx_values.append(dx)

    if len(dx_values) < period:

        return None

    return (
        sum(
            dx_values[-period:]
        )
        / period
    )


# =========================================================
# CANDLE PATTERNS
# =========================================================

def bullish_candle(candle):

    body = abs(
        candle["close"]
        - candle["open"]
    )

    total = (
        candle["high"]
        - candle["low"]
    )

    if total <= 0:

        return False

    return (
        candle["close"]
        > candle["open"]
        and body / total >= 0.45
    )


def bearish_candle(candle):

    body = abs(
        candle["close"]
        - candle["open"]
    )

    total = (
        candle["high"]
        - candle["low"]
    )

    if total <= 0:

        return False

    return (
        candle["close"]
        < candle["open"]
        and body / total >= 0.45
    )


# =========================================================
# H1 TREND
# =========================================================

def get_h1_trend(candles):

    closes = [
        c["close"]
        for c in candles
    ]

    e20 = ema(
        closes,
        20
    )

    e50 = ema(
        closes,
        50
    )

    e200 = ema(
        closes,
        200
    )

    if None in (
        e20,
        e50,
        e200,
    ):

        return "NEUTRAL", e20, e50, e200

    if (
        e20 > e50
        and e50 > e200
    ):

        return (
            "BULL",
            e20,
            e50,
            e200
        )

    if (
        e20 < e50
        and e50 < e200
    ):

        return (
            "BEAR",
            e20,
            e50,
            e200
        )

    return (
        "NEUTRAL",
        e20,
        e50,
        e200
    )


# =========================================================
# SIGNAL ANALYSIS
# =========================================================

def analyze_symbol(
    symbol,
    m15,
    h1
):

    if len(m15) < 210:

        return None

    if len(h1) < 210:

        return None

    # -----------------------------------------------------
    # H1 TREND
    # -----------------------------------------------------

    h1_trend, h1_e20, h1_e50, h1_e200 = (
        get_h1_trend(h1)
    )

    if h1_trend == "NEUTRAL":

        return None

    # -----------------------------------------------------
    # M15
    # -----------------------------------------------------

    closes = [
        c["close"]
        for c in m15
    ]

    e20 = ema(
        closes,
        20
    )

    e50 = ema(
        closes,
        50
    )

    e200 = ema(
        closes,
        200
    )

    previous_closes = closes[:-1]

    previous_e20 = ema(
        previous_closes,
        20
    )

    current_rsi = rsi(
        closes,
        14
    )

    current_atr = atr(
        m15,
        14
    )

    current_adx = adx(
        m15,
        14
    )

    if None in (
        e20,
        e50,
        e200,
        previous_e20,
        current_rsi,
        current_atr,
        current_adx,
    ):

        return None

    candle = m15[-1]
    previous = m15[-2]

    price = candle["close"]

    # -----------------------------------------------------
    # لا نتداول إذا ATR غير منطقي
    # -----------------------------------------------------

    if current_atr <= 0:

        return None

    # -----------------------------------------------------
    # المسافة عن EMA20
    # -----------------------------------------------------

    distance_from_ema = abs(
        price - e20
    )

    distance_atr = (
        distance_from_ema
        / current_atr
    )

    if (
        distance_atr
        > MAX_DISTANCE_ATR
    ):

        return None

    # -----------------------------------------------------
    # BUY SCORE
    # -----------------------------------------------------

    buy_score = 0
    buy_reasons = []

    # اتجاه H1
    if h1_trend == "BULL":

        buy_score += 2

        buy_reasons.append(
            "H1 bullish trend"
        )

    # ترتيب M15
    if (
        e20 > e50
        and e50 > e200
    ):

        buy_score += 2

        buy_reasons.append(
            "M15 EMA20 > EMA50 > EMA200"
        )

    # السعر فوق EMA20
    if price > e20:

        buy_score += 1

        buy_reasons.append(
            "Price > EMA20"
        )

    # EMA20 صاعد
    if e20 > previous_e20:

        buy_score += 1

        buy_reasons.append(
            "EMA20 rising"
        )

    # RSI
    if (
        BUY_RSI_MIN
        <= current_rsi
        <= BUY_RSI_MAX
    ):

        buy_score += 1

        buy_reasons.append(
            "RSI bullish zone"
        )

    # ADX
    if current_adx >= 20:

        buy_score += 1

        buy_reasons.append(
            f"ADX strong ({current_adx:.1f})"
        )

    # شمعة صاعدة
    if bullish_candle(candle):

        buy_score += 1

        buy_reasons.append(
            "Bullish confirmation candle"
        )

    # Breakout
    if (
        candle["close"]
        > previous["high"]
    ):

        buy_score += 1

        buy_reasons.append(
            "M15 breakout"
        )

    # -----------------------------------------------------
    # SELL SCORE
    # -----------------------------------------------------

    sell_score = 0
    sell_reasons = []

    # اتجاه H1
    if h1_trend == "BEAR":

        sell_score += 2

        sell_reasons.append(
            "H1 bearish trend"
        )

    # ترتيب M15
    if (
        e20 < e50
        and e50 < e200
    ):

        sell_score += 2

        sell_reasons.append(
            "M15 EMA20 < EMA50 < EMA200"
        )

    # السعر تحت EMA20
    if price < e20:

        sell_score += 1

        sell_reasons.append(
            "Price < EMA20"
        )

    # EMA20 هابط
    if e20 < previous_e20:

        sell_score += 1

        sell_reasons.append(
            "EMA20 falling"
        )

    # RSI
    if (
        SELL_RSI_MIN
        <= current_rsi
        <= SELL_RSI_MAX
    ):

        sell_score += 1

        sell_reasons.append(
            "RSI bearish zone"
        )

    # ADX
    if current_adx >= 20:

        sell_score += 1

        sell_reasons.append(
            f"ADX strong ({current_adx:.1f})"
        )

    # شمعة هابطة
    if bearish_candle(candle):

        sell_score += 1

        sell_reasons.append(
            "Bearish confirmation candle"
        )

    # Breakdown
    if (
        candle["close"]
        < previous["low"]
    ):

        sell_score += 1

        sell_reasons.append(
            "M15 breakdown"
        )

    # -----------------------------------------------------
    # اختيار الاتجاه
    # -----------------------------------------------------

    if (
        buy_score >= MIN_SCORE
        and buy_score > sell_score
        and h1_trend == "BULL"
    ):

        direction = "BUY"
        score = buy_score
        reasons = buy_reasons

    elif (
        sell_score >= MIN_SCORE
        and sell_score > buy_score
        and h1_trend == "BEAR"
    ):

        direction = "SELL"
        score = sell_score
        reasons = sell_reasons

    else:

        return None

    # -----------------------------------------------------
    # Risk Management
    # -----------------------------------------------------

    entry = price

    risk = (
        current_atr
        * SL_ATR_MULTIPLIER
    )

    if risk <= 0:

        return None

    if direction == "BUY":

        sl = entry - risk

        tp1 = (
            entry
            + risk * TP1_R_MULTIPLIER
        )

        tp2 = (
            entry
            + risk * TP2_R_MULTIPLIER
        )

    else:

        sl = entry + risk

        tp1 = (
            entry
            - risk * TP1_R_MULTIPLIER
        )

        tp2 = (
            entry
            - risk * TP2_R_MULTIPLIER
        )

    rr1 = abs(
        tp1 - entry
    ) / abs(
        entry - sl
    )

    rr2 = abs(
        tp2 - entry
    ) / abs(
        entry - sl
    )

    if rr1 < MIN_RR:

        return None

    return {
        "symbol": symbol,

        "direction": direction,

        "entry": entry,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "rsi": current_rsi,

        "atr": current_atr,

        "adx": current_adx,

        "score": score,

        "h1_trend": h1_trend,

        "rr1": rr1,

        "rr2": rr2,

        "reasons": reasons,

        "candle_time": candle["time"],
    }


# =========================================================
# PRICE DECIMALS
# =========================================================

def price_decimals(symbol):

    if symbol.endswith("JPY"):

        return 3

    if symbol == "XAUUSD":

        return 2

    return 5


def format_price(symbol, price):

    digits = price_decimals(
        symbol
    )

    return f"{price:.{digits}f}"


# =========================================================
# SIGNAL MESSAGE
# =========================================================

def format_signal(signal):

    symbol = signal["symbol"]

    direction = signal["direction"]

    if direction == "BUY":

        emoji = "🟢"
        arabic = "شراء"

    else:

        emoji = "🔴"
        arabic = "بيع"

    reasons = "\n".join(
        f"• {x}"
        for x in signal["reasons"]
    )

    return (
        "🚨 STRONG MARKET SIGNAL\n\n"

        f"💱 الرمز: {symbol}\n"

        f"{emoji} الاتجاه: "
        f"{arabic} ({direction})\n\n"

        f"📍 Entry: "
        f"{format_price(symbol, signal['entry'])}\n"

        f"🛑 SL: "
        f"{format_price(symbol, signal['sl'])}\n"

        f"🎯 TP1: "
        f"{format_price(symbol, signal['tp1'])}\n"

        f"🎯 TP2: "
        f"{format_price(symbol, signal['tp2'])}\n\n"

        "📊 M15 + H1\n"

        f"📈 RSI: "
        f"{signal['rsi']:.1f}\n"

        f"📏 ATR: "
        f"{signal['atr']:.5f}\n"

        f"💪 ADX: "
        f"{signal['adx']:.1f}\n"

        f"⭐ Score: "
        f"{signal['score']}/10+\n"

        f"📐 RR TP1: "
        f"{signal['rr1']:.2f}\n"

        f"📐 RR TP2: "
        f"{signal['rr2']:.2f}\n\n"

        f"📈 H1 Trend: "
        f"{signal['h1_trend']}\n\n"

        "🔎 أسباب الإشارة:\n"
        f"{reasons}\n\n"

        "⚠️ هذه إشارة آلية للتحليل فقط، "
        "ولا يوجد نظام يضمن الربح."
    )


# =========================================================
# COOLDOWN
# =========================================================

def is_in_cooldown(symbol):

    last = last_signal_time.get(
        symbol
    )

    if last is None:

        return False

    elapsed = (
        time.time()
        - last
    )

    return (
        elapsed
        < COOLDOWN_MINUTES * 60
    )


# =========================================================
# CHECK SYMBOL
# =========================================================

def check_symbol(symbol):

    try:

        print(
            f"🔎 Checking {symbol}..."
        )

        # -------------------------------------------------
        # السعر الحالي
        # -------------------------------------------------

        tick = get_tick(
            symbol
        )

        if not tick:

            print(
                f"{symbol}: no tick"
            )

            return

        market_state = (
            tick.get(
                "marketState"
            )
        )

        if market_state != "open":

            print(
                f"{symbol}: market "
                f"{market_state}"
            )

            return

        if tick.get("stale"):

            print(
                f"{symbol}: stale price"
            )

            return

        # -------------------------------------------------
        # Cooldown
        # -------------------------------------------------

        if is_in_cooldown(symbol):

            print(
                f"{symbol}: cooldown"
            )

            return

        # -------------------------------------------------
        # M15
        # -------------------------------------------------

        m15 = get_ohlc(
            symbol,
            M15_INTERVAL,
            M15_BARS
        )

        # -------------------------------------------------
        # H1
        # -------------------------------------------------

        h1 = get_ohlc(
            symbol,
            H1_INTERVAL,
            H1_BARS
        )

        # -------------------------------------------------
        # تحليل
        # -------------------------------------------------

        signal = analyze_symbol(
            symbol,
            m15,
            h1
        )

        if not signal:

            print(
                f"{symbol}: no strong setup"
            )

            return

        candle_time = (
            signal["candle_time"]
        )

        # -------------------------------------------------
        # منع تكرار نفس الشمعة
        # -------------------------------------------------

        if (
            last_signal_candle.get(symbol)
            == candle_time
        ):

            print(
                f"{symbol}: "
                "already sent"
            )

            return

        # -------------------------------------------------
        # إرسال Telegram
        # -------------------------------------------------

        if not TELEGRAM_CHAT_ID:

            print(
                "TELEGRAM_CHAT_ID missing"
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

            last_signal_candle[
                symbol
            ] = candle_time

            last_signal_time[
                symbol
            ] = time.time()

            print(
                f"🚨 {symbol} SIGNAL SENT"
            )

    except Exception as e:

        print(
            f"❌ {symbol} error:",
            repr(e)
        )


# =========================================================
# MARKET CHECK
# =========================================================

def check_market():

    print(
        "\n================================"
    )

    print(
        "📡 MARKET SCAN"
    )

    print(
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    print(
        "================================"
    )

    for symbol in SYMBOLS:

        check_symbol(
            symbol
        )

        # تأخير بسيط بين الرموز
        time.sleep(0.3)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_message(message):

    chat_id = message["chat"]["id"]

    text = (
        message
        .get("text", "")
        .strip()
        .lower()
    )

    # -----------------------------------------------------
    # START
    # -----------------------------------------------------

    if text == "/start":

        send_message(
            chat_id,

            "👋 XAU Forex Signals\n\n"

            "🟢 البوت يعمل.\n\n"

            "🥇 XAUUSD\n"
            "💱 Forex pairs\n\n"

            "📊 M15 + H1\n"
            "🧠 Multi-filter strategy\n"
            "🛡 Risk filtering\n\n"

            "/test\n"
            "/signal\n"
            "/status"
        )

    # -----------------------------------------------------
    # TEST
    # -----------------------------------------------------

    elif text == "/test":

        send_message(
            chat_id,

            "✅ TEST SUCCESS\n\n"
            "Telegram يعمل بشكل صحيح.\n"
            "BiQuote هو مصدر البيانات."
        )

    # -----------------------------------------------------
    # SIGNAL
    # -----------------------------------------------------

    elif text == "/signal":

        send_message(
            chat_id,

            "🔎 جاري فحص XAUUSD وForex..."
        )

        found = 0

        for symbol in SYMBOLS:

            try:

                tick = get_tick(
                    symbol
                )

                if not tick:

                    continue

                if (
                    tick.get(
                        "marketState"
                    )
                    != "open"
                ):

                    continue

                m15 = get_ohlc(
                    symbol,
                    M15_INTERVAL,
                    M15_BARS
                )

                h1 = get_ohlc(
                    symbol,
                    H1_INTERVAL,
                    H1_BARS
                )

                signal = analyze_symbol(
                    symbol,
                    m15,
                    h1
                )

                if signal:

                    send_message(
                        chat_id,
                        format_signal(
                            signal
                        )
                    )

                    found += 1

            except Exception as e:

                print(
                    f"/signal {symbol}:",
                    repr(e)
                )

        if found == 0:

            send_message(
                chat_id,

                "⏳ لا توجد حاليًا "
                "إشارة قوية تستوفي كل الفلاتر.\n\n"

                "وهذا مقصود.\n"
                "لن نرسل صفقة إجبارية."
            )

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    elif text == "/status":

        active = []

        for symbol in SYMBOLS:

            try:

                tick = get_tick(
                    symbol
                )

                if tick:

                    state = tick.get(
                        "marketState",
                        "unknown"
                    )

                    active.append(
                        f"{symbol}: {state}"
                    )

            except:

                active.append(
                    f"{symbol}: error"
                )

        send_message(
            chat_id,

            "🟢 BOT STATUS\n\n"

            "📡 BiQuote: ON\n"

            "⏱ Scan: 60 seconds\n"

            "📊 M15 + H1\n"

            "🛡 Strong filtering: ON\n\n"

            + "\n".join(active)
        )

    else:

        send_message(
            chat_id,

            "الأوامر:\n\n"

            "/start\n"
            "/test\n"
            "/signal\n"
            "/status"
        )


# =========================================================
# TELEGRAM POLLING
# =========================================================

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
                        "Message error:",
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
                "❌ TELEGRAM 409:"
                " another instance "
                "is using getUpdates."
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

    print(
        "================================"
    )

    print(
        "XAU Forex Signals - FINAL"
    )

    print(
        "BiQuote FREE DATA"
    )

    print(
        "M15 + H1"
    )

    print(
        "Multi-filter strategy"
    )

    print(
        "Scan:",
        CHECK_SECONDS,
        "seconds"
    )

    print(
        "Symbols:",
        len(SYMBOLS)
    )

    print(
        "================================"
    )

    # -----------------------------------------------------
    # Telegram
    # -----------------------------------------------------

    try:

        check_telegram()

        clear_webhook()

    except Exception as e:

        print(
            "❌ Telegram startup:",
            repr(e)
        )

    # -----------------------------------------------------
    # اختبار BiQuote
    # -----------------------------------------------------

    try:

        tick = get_tick(
            "XAUUSD"
        )

        if tick:

            print(
                "✅ BiQuote XAUUSD:",
                tick.get("mid")
            )

            print(
                "Market:",
                tick.get(
                    "marketState"
                )
            )

    except Exception as e:

        print(
            "❌ BiQuote startup:",
            repr(e)
        )

    # -----------------------------------------------------
    # Main loop
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
                "❌ Main loop:",
                repr(e)
            )

            time.sleep(5)


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
