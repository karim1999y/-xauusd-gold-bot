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
# الإعدادات الصارمة
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
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:500]}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"Connection error: {e.reason}")


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, data=None, timeout=10):
    url = f"{TELEGRAM_API}/{method}"
    encoded = None

    if data:
        encoded = urllib.parse.urlencode(
            {k: v for k, v in data.items() if v is not None}
        ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=encoded,
        headers={"User-Agent": "XAU-Forex-Signals-Final/1.0"},
        method="POST" if encoded else "GET",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Telegram HTTP {e.code}: {body[:500]}")


def send_message(chat_id, text):
    result = telegram(
        "sendMessage",
        {
            "chat_id": str(chat_id),
            "text": text,
        }
    )
    if not result.get("ok"):
        raise RuntimeError(f"Telegram rejected message: {result}")

    print("✅ Telegram message sent.")
    return result


def check_telegram():
    result = telegram("getMe")
    if not result.get("ok"):
        raise RuntimeError(f"Telegram getMe failed: {result}")

    username = result.get("result", {}).get("username", "unknown")
    print("✅ Telegram:", username)


def clear_webhook():
    try:
        result = telegram("deleteWebhook", {"drop_pending_updates": "false"})
        if result.get("ok"):
            print("✅ Telegram webhook cleared.")
    except Exception as e:
        print("Webhook warning:", e)


# =========================================================
# BIQUOTE MARKET DATA
# =========================================================

def get_ohlc(symbol, interval, limit):
    url = (
        f"{BIQUOTE_BASE}/{symbol}/ohlc?"
        + urllib.parse.urlencode({"interval": interval, "limit": limit})
    )

    data = get_json(url)

    if not isinstance(data, dict):
        raise RuntimeError(f"Invalid BiQuote response for {symbol}")

    bars = data.get("bars", [])

    if len(bars) < 60:
        raise RuntimeError(f"Not enough {symbol} {interval} bars: {len(bars)}")

    candles = []
    for bar in bars:
        candles.append(
            {
                "time": bar.get("openTime"),
                "open": float(bar["open"]),
                "high": float(bar["high"]),
                "low": float(bar["low"]),
                "close": float(bar["close"]),
                "tick_volume": float(bar.get("tickVolume", 0)),
                "is_open": bool(bar.get("isOpen", False)),
            }
        )

    candles.sort(key=lambda x: x["time"])
    candles = [c for c in candles if not c["is_open"]]

    return candles


def get_tick(symbol):
    url = f"{BIQUOTE_BASE}/{symbol}"
    data = get_json(url)
    if not isinstance(data, dict):
        return None
    return data


# =========================================================
# INDICATORS
# =========================================================

def ema_series(values, period):
    if len(values) < period:
        return []

    multiplier = 2 / (period + 1)
    result = sum(values[:period]) / period
    output = [None] * (period - 1)
    output.append(result)

    for price in values[period:]:
        result = ((price - result) * multiplier) + result
        output.append(result)

    return output


def ema(values, period):
    series = ema_series(values, period)
    return series[-1] if series else None


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
        high = candles[i]["high"]
        low = candles[i]["low"]
        previous_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )
        trs.append(tr)

    return sum(trs[-period:]) / period


def adx(candles, period=14):
    if len(candles) < (period * 2 + 5):
        return None

    trs, plus_dm, minus_dm = [], [], []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_high = candles[i - 1]["high"]
        prev_low = candles[i - 1]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        up_move = high - prev_high
        down_move = prev_low - low

        pdm = up_move if (up_move > down_move and up_move > 0) else 0
        mdm = down_move if (down_move > up_move and down_move > 0) else 0

        trs.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    if len(trs) < period:
        return None

    smooth_tr = sum(trs[:period])
    smooth_plus = sum(plus_dm[:period])
    smooth_minus = sum(minus_dm[:period])

    dx_values = []

    for i in range(period, len(trs)):
        smooth_tr = smooth_tr - (smooth_tr / period) + trs[i]
        smooth_plus = smooth_plus - (smooth_plus / period) + plus_dm[i]
        smooth_minus = smooth_minus - (smooth_minus / period) + minus_dm[i]

        if smooth_tr == 0:
            continue

        plus_di = 100 * (smooth_plus / smooth_tr)
        minus_di = 100 * (smooth_minus / smooth_tr)

        denom = plus_di + minus_di
        if denom == 0:
            continue

        dx = 100 * abs(plus_di - minus_di) / denom
        dx_values.append(dx)

    if len(dx_values) < period:
        return None

    adx_val = sum(dx_values[:period]) / period

    for dx_val in dx_values[period:]:
        adx_val = ((adx_val * (period - 1)) + dx_val) / period

    return adx_val


# =========================================================
# CANDLE PATTERNS
# =========================================================

def bullish_candle(candle):
    body = abs(candle["close"] - candle["open"])
    total = candle["high"] - candle["low"]
    if total <= 0:
        return False
    return candle["close"] > candle["open"] and (body / total >= 0.45)


def bearish_candle(candle):
    body = abs(candle["close"] - candle["open"])
    total = candle["high"] - candle["low"]
    if total <= 0:
        return False
    return candle["close"] < candle["open"] and (body / total >= 0.45)


# =========================================================
# H1 TREND
# =========================================================

def get_h1_trend(candles):
    closes = [c["close"] for c in candles]

    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    e200 = ema(closes, 200)

    if None in (e20, e50, e200):
        return "NEUTRAL", e20, e50, e200

    if e20 > e50 and e50 > e200:
        return "BULL", e20, e50, e200

    if e20 < e50 and e50 < e200:
        return "BEAR", e20, e50, e200

    return "NEUTRAL", e20, e50, e200


# =========================================================
# SIGNAL ANALYSIS
# =========================================================

def analyze_symbol(symbol, m15, h1):
    if len(m15) < 210 or len(h1) < 210:
        return None

    h1_trend, _, _, _ = get_h1_trend(h1)
    if h1_trend == "NEUTRAL":
        return None

    closes = [c["close"] for c in m15]

    e20_series = ema_series(closes, 20)
    e50_series = ema_series(closes, 50)
    e200_series = ema_series(closes, 200)

    if not e20_series or not e50_series or not e200_series:
        return None

    e20 = e20_series[-1]
    previous_e20 = e20_series[-2]
    e50 = e50_series[-1]
    e200 = e200_series[-1]

    current_rsi = rsi(closes, 14)
    current_atr = atr(m15, 14)
    current_adx = adx(m15, 14)

    if None in (current_rsi, current_atr, current_adx):
        return None

    candle = m15[-1]
    previous = m15[-2]
    price = candle["close"]

    if current_atr <= 0:
        return None

    distance_from_ema = abs(price - e20)
    distance_atr = distance_from_ema / current_atr

    if distance_atr > MAX_DISTANCE_ATR:
        return None

    # BUY SCORE
    buy_score = 0
    buy_reasons = []

    if h1_trend == "BULL":
        buy_score += 2
        buy_reasons.append("H1 bullish trend")

    if e20 > e50 and e50 > e200:
        buy_score += 2
        buy_reasons.append("M15 EMA20 > EMA50 > EMA200")

    if price > e20:
        buy_score += 1
        buy_reasons.append("Price > EMA20")

    if e20 > previous_e20:
        buy_score += 1
        buy_reasons.append("EMA20 rising")

    if BUY_RSI_MIN <= current_rsi <= BUY_RSI_MAX:
        buy_score += 1
        buy_reasons.append("RSI bullish zone")

    if current_adx >= 20:
        buy_score += 1
        buy_reasons.append(f"ADX strong ({current_adx:.1f})")

    if bullish_candle(candle):
        buy_score += 1
        buy_reasons.append("Bullish confirmation candle")

    if candle["close"] > previous["high"]:
        buy_score += 1
        buy_reasons.append("M15 breakout")

    # SELL SCORE
    sell_score = 0
    sell_reasons = []

    if h1_trend == "BEAR":
        sell_score += 2
        sell_reasons.append("H1 bearish trend")

    if e20 < e50 and e50 < e200:
        sell_score += 2
        sell_reasons.append("M15 EMA20 < EMA50 < EMA200")

    if price < e20:
        sell_score += 1
        sell_reasons.append("Price < EMA20")

    if e20 < previous_e20:
        sell_score += 1
        sell_reasons.append("EMA20 falling")

    if SELL_RSI_MIN <= current_rsi <= SELL_RSI_MAX:
        sell_score += 1
        sell_reasons.append("RSI bearish zone")

    if current_adx >= 20:
        sell_score += 1
        sell_reasons.append(f"ADX strong ({current_adx:.1f})")

    if bearish_candle(candle):
        sell_score += 1
        sell_reasons.append("Bearish confirmation candle")

    if candle["close"] < previous["low"]:
        sell_score += 1
        sell_reasons.append("M15 breakdown")

    if buy_score >= MIN_SCORE and buy_score > sell_score and h1_trend == "BULL":
        direction = "BUY"
        score = buy_score
        reasons = buy_reasons
    elif sell_score >= MIN_SCORE and sell_score > buy_score and h1_trend == "BEAR":
        direction = "SELL"
        score = sell_score
        reasons = sell_reasons
    else:
        return None

    entry = price
    risk = current_atr * SL_ATR_MULTIPLIER

    if risk <= 0:
        return None

    if direction == "BUY":
        sl = entry - risk
        tp1 = entry + (risk * TP1_R_MULTIPLIER)
        tp2 = entry + (risk * TP2_R_MULTIPLIER)
    else:
        sl = entry + risk
        tp1 = entry - (risk * TP1_R_MULTIPLIER)
        tp2 = entry - (risk * TP2_R_MULTIPLIER)

    rr1 = abs(tp1 - entry) / abs(entry - sl)
    rr2 = abs(tp2 - entry) / abs(entry - sl)

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
# PRICE FORMATTING
# =========================================================

def price_decimals(symbol):
    if symbol.endswith("JPY"):
        return 3
    if symbol == "XAUUSD":
        return 2
    return 5


def format_price(symbol, price):
    digits = price_decimals(symbol)
    return f"{price:.{digits}f}"


# =========================================================
# SIGNAL MESSAGE FORMATTING
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

    reasons = "\n".join(f"• {x}" for x in signal["reasons"])

    return (
        "🚨 STRONG MARKET SIGNAL\n\n"
        f"💱 الرمز: {symbol}\n"
        f"{emoji} الاتجاه: {arabic} ({direction})\n\n"
        f"📍 Entry: {format_price(symbol, signal['entry'])}\n"
        f"🛑 SL: {format_price(symbol, signal['sl'])}\n"
        f"🎯 TP1: {format_price(symbol, signal['tp1'])}\n"
        f"🎯 TP2: {format_price(symbol, signal['tp2'])}\n\n"
        "📊 M15 + H1\n"
        f"📈 RSI: {signal['rsi']:.1f}\n"
        f"📏 ATR: {signal['atr']:.5f}\n"
        f"💪 ADX: {signal['adx']:.1f}\n"
        f"⭐ Score: {signal['score']}/10+\n"
        f"📐 RR TP1: {signal['rr1']:.2f}\n"
        f"📐 RR TP2: {signal['rr2']:.2f}\n\n"
        f"📈 H1 Trend: {signal['h1_trend']}\n\n"
        "🔎 أسباب الإشارة:\n"
        f"{reasons}\n\n"
        "⚠️ هذه إشارة آلية للتحليل فقط، ولا يوجد نظام يضمن الربح."
    )


# =========================================================
# COOLDOWN
# =========================================================

def is_in_cooldown(symbol):
    last = last_signal_time.get(symbol)
    if last is None:
        return False
    return (time.time() - last) < (COOLDOWN_MINUTES * 60)


# =========================================================
# CHECK SYMBOL
# =========================================================

def check_symbol(symbol):
    try:
        print(f"🔎 Checking {symbol}...")

        tick = get_tick(symbol)
        if not tick or tick.get("marketState") != "open" or tick.get("stale"):
            return

        if is_in_cooldown(symbol):
            return

        m15 = get_ohlc(symbol, M15_INTERVAL, M15_BARS)
        h1 = get_ohlc(symbol, H1_INTERVAL, H1_BARS)

        signal = analyze_symbol(symbol, m15, h1)
        if not signal:
            return

        candle_time = signal["candle_time"]
        if last_signal_candle.get(symbol) == candle_time:
            return

        if not TELEGRAM_CHAT_ID:
            return

        message = format_signal(signal)
        result = send_message(TELEGRAM_CHAT_ID, message)

        if result.get("ok"):
            last_signal_candle[symbol] = candle_time
            last_signal_time[symbol] = time.time()
            print(f"🚨 {symbol} SIGNAL SENT")

    except Exception as e:
        print(f"❌ {symbol} error:", repr(e))


# =========================================================
# MARKET CHECK
# =========================================================

def check_market():
    print("\n================================")
    print("📡 MARKET SCAN")
    print(datetime.now(timezone.utc).isoformat())
    print("================================")

    for symbol in SYMBOLS:
        check_symbol(symbol)
        time.sleep(0.3)


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip().lower()

    if text == "/start":
        send_message(
            chat_id,
            "👋 XAU Forex Signals\n\n"
            "🟢 البوت يعمل بالنظام الصارم.\n\n"
            "/test\n"
            "/signal\n"
            "/debug\n"
            "/status"
        )
    elif text == "/test":
        send_message(
            chat_id,
            "✅ TEST SUCCESS\n\n"
            "Telegram يعمل بشكل صحيح.\n"
            "BiQuote هو مصدر البيانات."
        )
    elif text == "/signal":
        send_message(chat_id, "🔎 جاري فحص XAUUSD وForex...")
        found = 0

        for symbol in SYMBOLS:
            try:
                tick = get_tick(symbol)
                if not tick or tick.get("marketState") != "open":
                    continue

                m15 = get_ohlc(symbol, M15_INTERVAL, M15_BARS)
                h1 = get_ohlc(symbol, H1_INTERVAL, H1_BARS)

                signal = analyze_symbol(symbol, m15, h1)
                if signal:
                    send_message(chat_id, format_signal(signal))
                    found += 1
            except Exception as e:
                print(f"/signal {symbol}:", repr(e))

        if found == 0:
            send_message(
                chat_id,
                "⏳ لا توجد حاليًا إشارة قوية تستوفي كل الفلاتر.\n\n"
                "وهذا مقصود. لن نرسل صفقة إجبارية."
            )

    elif text == "/debug":
        send_message(chat_id, "🔍 جاري فحص الأسباب التفصيلية لاستبعاد الأزواج...")
        report = []

        for symbol in SYMBOLS:
            try:
                tick = get_tick(symbol)
                if not tick:
                    report.append(f"❌ {symbol}: لا توجد أسعار")
                    continue

                if tick.get("marketState") != "open":
                    report.append(f"❌ {symbol}: السوق مغلق")
                    continue

                m15 = get_ohlc(symbol, M15_INTERVAL, M15_BARS)
                h1 = get_ohlc(symbol, H1_INTERVAL, H1_BARS)

                h1_trend, _, _, _ = get_h1_trend(h1)
                closes = [c["close"] for c in m15]
                c_adx = adx(m15, 14)

                if h1_trend == "NEUTRAL":
                    report.append(f"⚠️ {symbol}: الاتجاه محايد على H1")
                elif c_adx and c_adx < 20:
                    report.append(f"⚠️ {symbol}: الزخم ضعيف (ADX: {c_adx:.1f})")
                else:
                    signal = analyze_symbol(symbol, m15, h1)
                    if not signal:
                        report.append(f"⚠️ {symbol}: النقاط أقل من {MIN_SCORE}")
                    else:
                        report.append(f"✅ {symbol}: صفقة متاحة الآن!")

            except Exception as e:
                report.append(f"❌ {symbol}: خطأ في البيانات")

        send_message(chat_id, "📊 **تقرير تشخيص النظام الصارم:**\n\n" + "\n".join(report))

    elif text == "/status":
        active = []
        for symbol in SYMBOLS:
            try:
                tick = get_tick(symbol)
                if tick:
                    state = tick.get("marketState", "unknown")
                    active.append(f"{symbol}: {state}")
            except:
                active.append(f"{symbol}: error")

        send_message(
            chat_id,
            "🟢 BOT STATUS\n\n"
            "📡 BiQuote: ON\n"
            "⏱ Scan: 60 seconds\n"
            "📊 M15 + H1\n"
            "🛡 Strict filtering: ON\n\n"
            + "\n".join(active)
        )
    else:
        send_message(
            chat_id,
            "الأوامر:\n\n"
            "/start\n"
            "/test\n"
            "/signal\n"
            "/debug\n"
            "/status"
        )


# =========================================================
# TELEGRAM POLLING
# =========================================================

def process_telegram_updates(offset):
    try:
        result = telegram(
            "getUpdates",
            {
                "timeout": 1,
                "offset": offset,
                "allowed_updates": json.dumps(["message"]),
            },
            timeout=5
        )

        if not result.get("ok"):
            return offset

        for update in result.get("result", []):
            offset = update["update_id"] + 1
            message = update.get("message")
            if message:
                try:
                    handle_message(message)
                except Exception as e:
                    print("Message error:", repr(e))

        return offset

    except Exception as e:
        text = str(e)
        if "409" in text or "Conflict" in text:
            print("❌ TELEGRAM 409: instance conflict")
            time.sleep(10)
            return offset

        print("Telegram polling error:", repr(e))
        time.sleep(3)
        return offset


# =========================================================
# MAIN LOOP
# =========================================================

def main():
    print("================================")
    print("XAU Forex Signals - STRICT EDITION")
    print("BiQuote FREE DATA | M15 + H1")
    print("Scan:", CHECK_SECONDS, "seconds")
    print("Symbols:", len(SYMBOLS))
    print("================================")

    try:
        check_telegram()
        clear_webhook()
    except Exception as e:
        print("❌ Telegram startup:", repr(e))

    try:
        tick = get_tick("XAUUSD")
        if tick:
            print("✅ BiQuote XAUUSD:", tick.get("mid"))
            print("Market:", tick.get("marketState"))
    except Exception as e:
        print("❌ BiQuote startup:", repr(e))

    offset = None
    last_market_check = 0

    while True:
        try:
            offset = process_telegram_updates(offset)

            now = time.time()
            if now - last_market_check >= CHECK_SECONDS:
                last_market_check = now
                check_market()

            time.sleep(0.5)

        except KeyboardInterrupt:
            print("Bot stopped.")
            break
        except Exception as e:
            print("❌ Main loop:", repr(e))
            time.sleep(5)


if __name__ == "__main__":
    main()
