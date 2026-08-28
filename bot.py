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

SYMBOL = "XAUUSD"

CHECK_SECONDS = 60

STATE_FILE = "bot_state.json"

# نأخذ الإشارات القوية فقط
MIN_SCORE = 8

# أقصى عدد إشارات نحتفظ بها للمتابعة
MAX_OPEN_TRACKED = 3

# منع تكرار نفس الاتجاه بسرعة
COOLDOWN_CANDLES = 2


# =========================================================
# HTTP
# =========================================================

def get_json(url, timeout=20):

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
            f"HTTP {e.code} from market API: {body[:400]}"
        )

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"Market connection error: {e.reason}"
        )


def telegram(
    method,
    data=None,
    timeout=25
):

    url = f"{TELEGRAM_API}/{method}"

    encoded = None

    if data is not None:

        encoded = urllib.parse.urlencode(
            data
        ).encode()

    req = urllib.request.Request(
        url,
        data=encoded,
        headers={
            "User-Agent":
                "XAU-Gold-Signals/6.0"
        },
        method=(
            "POST"
            if encoded
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
            f"Telegram HTTP {e.code}: {body[:400]}"
        )

    except urllib.error.URLError as e:

        raise RuntimeError(
            f"Telegram connection error: {e.reason}"
        )


# =========================================================
# TELEGRAM
# =========================================================

def send_message(
    chat_id,
    text
):

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

    print(
        "✅ Telegram message sent."
    )

    return result


def check_telegram():

    result = telegram("getMe")

    if not result.get("ok"):

        raise RuntimeError(
            f"Telegram getMe failed: {result}"
        )

    print(
        "Telegram:",
        result.get(
            "result",
            {}
        ).get(
            "username",
            "unknown"
        )
    )


def delete_webhook():

    try:

        telegram(
            "deleteWebhook",
            {
                "drop_pending_updates":
                    "false"
            }
        )

        print(
            "✅ Webhook cleared."
        )

    except Exception as e:

        print(
            "Webhook warning:",
            e
        )


# =========================================================
# STATE
# =========================================================

def load_state():

    default = {

        "last_signal_candle":
            None,

        "last_signal_direction":
            None,

        "signals":
            [],

        "stats": {

            "closed": 0,

            "wins": 0,

            "losses": 0,

            "tp1": 0,

            "tp2": 0,

            "sl": 0,
        }
    }

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        for key, value in default.items():

            if key not in state:

                state[key] = value

        return state

    except Exception:

        return default


def save_state(state):

    tmp = STATE_FILE + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        tmp,
        STATE_FILE
    )


state = load_state()


# =========================================================
# BIQUOTE DATA
# =========================================================

def get_bars(
    interval,
    limit
):

    url = (
        f"https://biquote.io/api/"
        f"{SYMBOL}/ohlc?"
        +
        urllib.parse.urlencode(
            {
                "interval":
                    interval,

                "limit":
                    limit,
            }
        )
    )

    data = get_json(url)

    bars = data.get(
        "bars",
        []
    )

    if not bars:

        raise RuntimeError(
            f"BiQuote returned no "
            f"{interval} bars"
        )

    # الأحدث أولاً
    bars = list(
        reversed(bars)
    )

    result = []

    for b in bars:

        result.append(
            {
                "time":
                    b["openTime"],

                "open":
                    float(b["open"]),

                "high":
                    float(b["high"]),

                "low":
                    float(b["low"]),

                "close":
                    float(b["close"]),

                "isOpen":
                    bool(
                        b.get(
                            "isOpen",
                            False
                        )
                    ),
            }
        )

    return result


def get_quote():

    data = get_json(
        "https://biquote.io/api/"
        f"{SYMBOL}?allowStale=false"
    )

    if data.get(
        "marketState"
    ) != "open":

        return None

    if data.get(
        "stale"
    ):

        return None

    return data


# =========================================================
# INDICATORS
# =========================================================

def ema(
    values,
    period
):

    if len(values) < period:

        return None

    multiplier = (
        2 /
        (period + 1)
    )

    result = (
        sum(
            values[:period]
        )
        /
        period
    )

    for price in values[period:]:

        result = (
            (
                price - result
            )
            *
            multiplier
            +
            result
        )

    return result


def rsi(
    values,
    period=14
):

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
            -
            values[i - 1]
        )

        gains.append(
            max(
                change,
                0
            )
        )

        losses.append(
            max(
                -change,
                0
            )
        )

    avg_gain = (
        sum(
            gains[:period]
        )
        /
        period
    )

    avg_loss = (
        sum(
            losses[:period]
        )
        /
        period
    )

    for i in range(
        period,
        len(gains)
    ):

        avg_gain = (
            (
                avg_gain
                *
                (period - 1)
            )
            +
            gains[i]
        ) / period

        avg_loss = (
            (
                avg_loss
                *
                (period - 1)
            )
            +
            losses[i]
        ) / period

    if avg_loss == 0:

        return 100

    rs = (
        avg_gain
        /
        avg_loss
    )

    return (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )


def atr(
    candles,
    period=14
):

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
                -
                previous_close
            ),

            abs(
                low
                -
                previous_close
            ),
        )

        trs.append(tr)

    return (
        sum(
            trs[-period:]
        )
        /
        period
    )


def adx(
    candles,
    period=14
):

    if len(candles) < (
        2 * period + 1
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

        previous_high = (
            candles[i - 1]["high"]
        )

        previous_low = (
            candles[i - 1]["low"]
        )

        previous_close = (
            candles[i - 1]["close"]
        )

        tr = max(
            high - low,

            abs(
                high
                -
                previous_close
            ),

            abs(
                low
                -
                previous_close
            ),
        )

        trs.append(tr)

        up = (
            high
            -
            previous_high
        )

        down = (
            previous_low
            -
            low
        )

        plus_dm.append(
            up
            if (
                up > down
                and
                up > 0
            )
            else 0
        )

        minus_dm.append(
            down
            if (
                down > up
                and
                down > 0
            )
            else 0
        )

    atr_value = (
        sum(
            trs[:period]
        )
        /
        period
    )

    dx_values = []

    for i in range(
        period,
        len(trs)
    ):

        atr_value = (
            (
                atr_value
                *
                (period - 1)
            )
            +
            trs[i]
        ) / period

        start = max(
            0,
            i - period + 1
        )

        plus_avg = (
            sum(
                plus_dm[start:i + 1]
            )
            /
            period
        )

        minus_avg = (
            sum(
                minus_dm[start:i + 1]
            )
            /
            period
        )

        plus_di = (
            100
            *
            plus_avg
            /
            atr_value
            if atr_value
            else 0
        )

        minus_di = (
            100
            *
            minus_avg
            /
            atr_value
            if atr_value
            else 0
        )

        if (
            plus_di
            +
            minus_di
        ):

            dx = (
                100
                *
                abs(
                    plus_di
                    -
                    minus_di
                )
                /
                (
                    plus_di
                    +
                    minus_di
                )
            )

        else:

            dx = 0

        dx_values.append(dx)

    if len(dx_values) < period:

        return None

    return (
        sum(
            dx_values[-period:]
        )
        /
        period
    )


def body_ratio(candle):

    candle_range = (
        candle["high"]
        -
        candle["low"]
    )

    if candle_range == 0:

        return 0

    return (
        abs(
            candle["close"]
            -
            candle["open"]
        )
        /
        candle_range
    )


# =========================================================
# V6 ANALYSIS
# =========================================================

def analyze(
    m15,
    h1
):

    m15_closed = [
        c for c in m15
        if not c["isOpen"]
    ]

    h1_closed = [
        c for c in h1
        if not c["isOpen"]
    ]

    if len(m15_closed) < 80:
        return None

    if len(h1_closed) < 80:
        return None

    m15_closes = [
        c["close"]
        for c in m15_closed
    ]

    h1_closes = [
        c["close"]
        for c in h1_closed
    ]

    ema20 = ema(
        m15_closes,
        20
    )

    ema50 = ema(
        m15_closes,
        50
    )

    h1_ema20 = ema(
        h1_closes,
        20
    )

    h1_ema50 = ema(
        h1_closes,
        50
    )

    current_rsi = rsi(
        m15_closes,
        14
    )

    current_atr = atr(
        m15_closed,
        14
    )

    current_adx = adx(
        m15_closed,
        14
    )

    h1_adx = adx(
        h1_closed,
        14
    )

    if None in (
        ema20,
        ema50,
        h1_ema20,
        h1_ema50,
        current_rsi,
        current_atr,
        current_adx,
        h1_adx,
    ):

        return None

    candle = m15_closed[-1]
    previous = m15_closed[-2]
    previous2 = m15_closed[-3]

    price = candle["close"]

    # =====================================================
    # AVOID OVEREXTENDED MOVES
    # =====================================================

    recent_high = max(
        x["high"]
        for x in m15_closed[-6:]
    )

    recent_low = min(
        x["low"]
        for x in m15_closed[-6:]
    )

    recent_range = (
        recent_high
        -
        recent_low
    )

    # إذا تحرك الذهب بقوة كبيرة مؤخرًا،
    # لا نطارد الحركة.
    if recent_range > (
        current_atr * 3.2
    ):

        return None

    # =====================================================
    # TREND
    # =====================================================

    bullish_h1 = (
        h1_ema20
        >
        h1_ema50
    )

    bearish_h1 = (
        h1_ema20
        <
        h1_ema50
    )

    bullish_m15 = (
        ema20
        >
        ema50
        and
        price
        >
        ema20
    )

    bearish_m15 = (
        ema20
        <
        ema50
        and
        price
        <
        ema20
    )

    # =====================================================
    # PULLBACK
    # =====================================================

    bullish_pullback = (
        previous["low"]
        <=
        ema20
        or
        previous2["low"]
        <=
        ema20
    ) and (
        price
        >
        ema20
    )

    bearish_pullback = (
        previous["high"]
        >=
        ema20
        or
        previous2["high"]
        >=
        ema20
    ) and (
        price
        <
        ema20
    )

    # =====================================================
    # CONFIRMATION CANDLE
    # =====================================================

    bullish_confirmation = (
        candle["close"]
        >
        candle["open"]
        and
        candle["close"]
        >
        previous["high"]
        and
        body_ratio(candle)
        >=
        0.45
    )

    bearish_confirmation = (
        candle["close"]
        <
        candle["open"]
        and
        candle["close"]
        <
        previous["low"]
        and
        body_ratio(candle)
        >=
        0.45
    )

    # =====================================================
    # SCORE
    # =====================================================

    buy_score = 0
    buy_reasons = []

    sell_score = 0
    sell_reasons = []

    # H1 trend
    if bullish_h1:

        buy_score += 2

        buy_reasons.append(
            "H1 bullish trend"
        )

    if bearish_h1:

        sell_score += 2

        sell_reasons.append(
            "H1 bearish trend"
        )

    # M15 trend
    if ema20 > ema50:

        buy_score += 2

        buy_reasons.append(
            "M15 EMA trend bullish"
        )

    if ema20 < ema50:

        sell_score += 2

        sell_reasons.append(
            "M15 EMA trend bearish"
        )

    # Pullback
    if bullish_pullback:

        buy_score += 2

        buy_reasons.append(
            "M15 pullback to EMA20"
        )

    if bearish_pullback:

        sell_score += 2

        sell_reasons.append(
            "M15 pullback to EMA20"
        )

    # RSI
    if 50 <= current_rsi <= 64:

        buy_score += 1

        buy_reasons.append(
            "RSI healthy for BUY"
        )

    if 36 <= current_rsi <= 50:

        sell_score += 1

        sell_reasons.append(
            "RSI healthy for SELL"
        )

    # ADX
    if current_adx >= 18:

        buy_score += 1
        sell_score += 1

        buy_reasons.append(
            "ADX trend strength"
        )

        sell_reasons.append(
            "ADX trend strength"
        )

    if h1_adx >= 18:

        buy_score += 1
        sell_score += 1

        buy_reasons.append(
            "H1 ADX strength"
        )

        sell_reasons.append(
            "H1 ADX strength"
        )

    # Confirmation
    if bullish_confirmation:

        buy_score += 1

        buy_reasons.append(
            "Bullish confirmation candle"
        )

    if bearish_confirmation:

        sell_score += 1

        sell_reasons.append(
            "Bearish confirmation candle"
        )

    # =====================================================
    # FINAL SIGNAL
    # =====================================================

    direction = None

    if (
        bullish_m15
        and
        bullish_h1
        and
        bullish_pullback
        and
        bullish_confirmation
        and
        buy_score >= MIN_SCORE
        and
        buy_score > sell_score
    ):

        direction = "BUY"

        score = buy_score

        reasons = buy_reasons

    elif (
        bearish_m15
        and
        bearish_h1
        and
        bearish_pullback
        and
        bearish_confirmation
        and
        sell_score >= MIN_SCORE
        and
        sell_score > buy_score
    ):

        direction = "SELL"

        score = sell_score

        reasons = sell_reasons

    else:

        return None

    # =====================================================
    # STOP LOSS / TAKE PROFIT
    # =====================================================

    swing_low = min(
        x["low"]
        for x in m15_closed[-5:]
    )

    swing_high = max(
        x["high"]
        for x in m15_closed[-5:]
    )

    if direction == "BUY":

        sl = min(
            swing_low,
            price
            -
            current_atr * 1.15
        )

        risk = (
            price
            -
            sl
        )

        if (
            risk
            <
            current_atr * 0.85
            or
            risk
            >
            current_atr * 2.0
        ):

            return None

        tp1 = (
            price
            +
            risk * 1.8
        )

        tp2 = (
            price
            +
            risk * 2.5
        )

    else:

        sl = max(
            swing_high,
            price
            +
            current_atr * 1.15
        )

        risk = (
            sl
            -
            price
        )

        if (
            risk
            <
            current_atr * 0.85
            or
            risk
            >
            current_atr * 2.0
        ):

            return None

        tp1 = (
            price
            -
            risk * 1.8
        )

        tp2 = (
            price
            -
            risk * 2.5
        )

    return {

        "direction":
            direction,

        "entry":
            price,

        "sl":
            sl,

        "tp1":
            tp1,

        "tp2":
            tp2,

        "rsi":
            current_rsi,

        "atr":
            current_atr,

        "adx":
            current_adx,

        "h1_adx":
            h1_adx,

        "score":
            score,

        "reasons":
            reasons,

        "candle_time":
            candle["time"],

        "risk":
            risk,
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

        "🚨 XAUUSD V6 SIGNAL\n\n"

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

        "📊 Timeframe: M15 + H1\n"

        f"📈 RSI: "
        f"{signal['rsi']:.1f}\n"

        f"📏 ATR: "
        f"{signal['atr']:.2f}\n"

        f"💪 ADX M15/H1: "
        f"{signal['adx']:.1f}/"
        f"{signal['h1_adx']:.1f}\n"

        f"⭐ Score: "
        f"{signal['score']}\n\n"

        "🔎 الأسباب:\n"

        f"{reasons}\n\n"

        "🛡️ فلترة محافظة:\n"
        "H1 Trend + M15 Trend + "
        "Pullback + Confirmation\n\n"

        "⚠️ إشارة آلية. "
        "لا توجد استراتيجية تضمن عدم الخسارة."
    )


# =========================================================
# TRACK RESULTS
# =========================================================

def update_tracked(
    m15
):

    closed = [
        x for x in m15
        if not x["isOpen"]
    ]

    changed = False

    for signal in state["signals"]:

        if signal.get("result"):

            continue

        for candle in closed:

            if candle["time"] <= signal["candle_time"]:

                continue

            if signal["direction"] == "BUY":

                hit_sl = (
                    candle["low"]
                    <=
                    signal["sl"]
                )

                hit_tp2 = (
                    candle["high"]
                    >=
                    signal["tp2"]
                )

                hit_tp1 = (
                    candle["high"]
                    >=
                    signal["tp1"]
                )

            else:

                hit_sl = (
                    candle["high"]
                    >=
                    signal["sl"]
                )

                hit_tp2 = (
                    candle["low"]
                    <=
                    signal["tp2"]
                )

                hit_tp1 = (
                    candle["low"]
                    <=
                    signal["tp1"]
                )

            # -------------------------------------------------
            # إذا ضرب SL وTP بنفس الشمعة
            # نحسب SL أولًا — بشكل محافظ
            # -------------------------------------------------

            if hit_sl:

                signal["result"] = "SL"

                signal["result_time"] = (
                    candle["time"]
                )

                state["stats"]["losses"] += 1
                state["stats"]["sl"] += 1
                state["stats"]["closed"] += 1

                changed = True

                break

            if hit_tp2:

                signal["result"] = "TP2"

                signal["result_time"] = (
                    candle["time"]
                )

                state["stats"]["wins"] += 1
                state["stats"]["tp2"] += 1
                state["stats"]["closed"] += 1

                changed = True

                break

            if hit_tp1:

                signal["result"] = "TP1"

                signal["result_time"] = (
                    candle["time"]
                )

                state["stats"]["wins"] += 1
                state["stats"]["tp1"] += 1
                state["stats"]["closed"] += 1

                changed = True

                break

    state["signals"] = (
        state["signals"]
        [-MAX_OPEN_TRACKED:]
    )

    if changed:

        save_state(state)


# =========================================================
# STATS
# =========================================================

def stats_text():

    stats = state["stats"]

    total = stats["closed"]

    if total:

        win_rate = (
            stats["wins"]
            /
            total
            *
            100
        )

    else:

        win_rate = 0

    return (

        "📊 XAU V6 Statistics\n\n"

        f"Closed: {total}\n"

        f"Wins: "
        f"{stats['wins']}\n"

        f"Losses: "
        f"{stats['losses']}\n"

        f"Win rate: "
        f"{win_rate:.1f}%\n\n"

        f"🎯 TP1: "
        f"{stats['tp1']}\n"

        f"🎯 TP2: "
        f"{stats['tp2']}\n"

        f"🛑 SL: "
        f"{stats['sl']}"
    )


# =========================================================
# MARKET CHECK
# =========================================================

def check_market():

    try:

        print(
            f"[{datetime.now(timezone.utc).isoformat()}]"
            " Checking XAUUSD..."
        )

        m15 = get_bars(
            "15m",
            300
        )

        h1 = get_bars(
            "1h",
            250
        )

        update_tracked(
            m15
        )

        quote = get_quote()

        if not quote:

            print(
                "Market closed or stale."
            )

            return

        signal = analyze(
            m15,
            h1
        )

        if not signal:

            print(
                "⏳ No V6 signal."
            )

            return

        candle_time = (
            signal["candle_time"]
        )

        if (
            candle_time
            ==
            state.get(
                "last_signal_candle"
            )
        ):

            return

        # -----------------------------------------------------
        # COOLDOWN
        # -----------------------------------------------------

        if (
            state.get(
                "last_signal_candle"
            )
            and
            state.get(
                "last_signal_direction"
            )
            ==
            signal["direction"]
        ):

            closed = [
                x for x in m15
                if not x["isOpen"]
            ]

            times = [
                x["time"]
                for x in closed
            ]

            try:

                a = times.index(
                    state[
                        "last_signal_candle"
                    ]
                )

                b = times.index(
                    candle_time
                )

                if (
                    b - a
                    <
                    COOLDOWN_CANDLES
                ):

                    print(
                        "Cooldown active."
                    )

                    return

            except ValueError:

                pass

        # -----------------------------------------------------
        # SEND
        # -----------------------------------------------------

        message = format_signal(
            signal
        )

        send_message(
            TELEGRAM_CHAT_ID,
            message
        )

        state[
            "last_signal_candle"
        ] = candle_time

        state[
            "last_signal_direction"
        ] = signal["direction"]

        state["signals"].append(
            signal
        )

        save_state(
            state
        )

        print(
            "🚨 V6 SIGNAL SENT:",
            signal["direction"]
        )

    except Exception as e:

        print(
            "❌ Market error:",
            repr(e)
        )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_message(
    message
):

    chat_id = (
        message["chat"]["id"]
    )

    text = (
        message
        .get("text", "")
        .strip()
    )

    if text == "/start":

        send_message(
            chat_id,

            "👋 XAU Gold Signals V6\n\n"

            "🛡️ استراتيجية محافظة\n"

            "📡 Data Source: BiQuote\n"

            "🥇 XAUUSD\n"

            "📊 M15 + H1\n\n"

            "/test - اختبار\n"
            "/signal - تحليل الآن\n"
            "/status - الحالة\n"
            "/stats - النتائج"
        )

    elif text == "/test":

        send_message(
            chat_id,

            "✅ TEST SUCCESS\n\n"

            "🟢 Data Source: BiQuote\n"

            "🥇 Symbol: XAUUSD\n"

            "📊 Timeframe: M15 + H1\n"

            "🛡️ Strategy: V6 Conservative\n\n"

            "⚠️ هذا اختبار فقط "
            "وليس إشارة حقيقية."
        )

    elif text == "/signal":

        try:

            m15 = get_bars(
                "15m",
                300
            )

            h1 = get_bars(
                "1h",
                250
            )

            quote = get_quote()

            signal = (
                analyze(m15, h1)
                if quote
                else None
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
                    "V6 قوية الآن.\n\n"

                    "🛡️ لن نفتح صفقة إجبارية."
                )

        except Exception as e:

            send_message(
                chat_id,

                "❌ تعذر التحليل:\n"
                f"{e}"
            )

    elif text == "/status":

        send_message(
            chat_id,

            "🟢 البوت يعمل\n\n"

            "📡 BiQuote: ON\n"

            "🥇 XAUUSD\n"

            "📊 M15 + H1\n"

            "⏱ الفحص: كل 60 ثانية\n\n"

            "🕯 آخر إشارة:\n"

            f"{state.get('last_signal_candle') or 'لا يوجد'}\n\n"

            + stats_text()
        )

    elif text == "/stats":

        send_message(
            chat_id,
            stats_text()
        )

    else:

        send_message(
            chat_id,

            "/start\n"
            "/test\n"
            "/signal\n"
            "/status\n"
            "/stats"
        )


# =========================================================
# TELEGRAM POLLING
# =========================================================

def process_updates(
    offset
):

    try:

        result = telegram(
            "getUpdates",
            {
                "timeout":
                    5,

                "offset":
                    offset,

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
                        "Handler error:",
                        repr(e)
                    )

        return offset

    except Exception as e:

        text = str(e)

        if (
            "409"
            in text
            or
            "Conflict"
            in text
        ):

            print(
                "❌ Telegram 409 Conflict"
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
        "XAU Gold Signals Bot V6"
    )

    print(
        "BiQuote + M15 + H1"
    )

    print(
        "Conservative Strategy"
    )

    print(
        "Check every:",
        CHECK_SECONDS,
        "seconds"
    )

    print(
        "================================"
    )

    check_telegram()

    delete_webhook()

    offset = None

    last_market_check = 0

    while True:

        try:

            offset = process_updates(
                offset
            )

            now = time.time()

            if (
                now
                -
                last_market_check
                >=
                CHECK_SECONDS
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
