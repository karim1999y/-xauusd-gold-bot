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

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"
BIQUOTE_BASE = "https://biquote.io/api"

CHECK_SECONDS = 30 

SYMBOLS = [
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "AUDUSD",
    "USDCAD",
]

ENTRY_INTERVAL = "5m"
TREND_INTERVAL = "15m"

ENTRY_BARS = 200
TREND_BARS = 200

MIN_SCORE = 5
MIN_RR = 1.3
TP1_R_MULTIPLIER = 1.3
TP2_R_MULTIPLIER = 2.0
SL_ATR_MULTIPLIER = 1.1

COOLDOWN_MINUTES = 20

BUY_RSI_MIN = 45
BUY_RSI_MAX = 65
SELL_RSI_MIN = 35
SELL_RSI_MAX = 55

# =========================================================
# GLOBAL TRACKING STATE
# =========================================================

last_signal_candle = {}
last_signal_time = {}

# قائمة الصفقات المفتوحة للمراقبة
active_trades = []

# سجل النتائج الإحصائية
stats = {
    "wins": 0,
    "losses": 0,
    "total": 0
}

# =========================================================
# HTTP & TELEGRAM
# =========================================================

def get_json(url, timeout=15):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Scalper-Bot/1.0", "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))
    except Exception as e:
        raise RuntimeError(f"Fetch error: {e}")

def telegram(method, data=None, timeout=5):
    url = f"{TELEGRAM_API}/{method}"
    encoded = urllib.parse.urlencode({k: v for k, v in data.items() if v is not None}).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=encoded, headers={"User-Agent": "Scalper-Bot/1.0"}, method="POST" if encoded else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))

def send_message(chat_id, text):
    return telegram("sendMessage", {"chat_id": str(chat_id), "text": text})

# =========================================================
# MARKET DATA
# =========================================================

def get_ohlc(symbol, interval, limit):
    url = f"{BIQUOTE_BASE}/{symbol}/ohlc?" + urllib.parse.urlencode({"interval": interval, "limit": limit})
    data = get_json(url)
    bars = data.get("bars", [])
    if len(bars) < 50:
        raise RuntimeError(f"Not enough bars for {symbol}")
    
    candles = []
    for bar in bars:
        candles.append({
            "time": bar.get("openTime"),
            "open": float(bar["open"]),
            "high": float(bar["high"]),
            "low": float(bar["low"]),
            "close": float(bar["close"]),
            "is_open": bool(bar.get("isOpen", False)),
        })
    candles.sort(key=lambda x: x["time"])
    return [c for c in candles if not c["is_open"]]

def get_tick(symbol):
    return get_json(f"{BIQUOTE_BASE}/{symbol}")

# =========================================================
# INDICATORS
# =========================================================

def ema_series(values, period):
    if len(values) < period:
        return []
    mult = 2 / (period + 1)
    val = sum(values[:period]) / period
    out = [None] * (period - 1) + [val]
    for p in values[period:]:
        val = ((p - val) * mult) + val
        out.append(val)
    return out

def ema(values, period):
    s = ema_series(values, period)
    return s[-1] if s else None

def rsi(values, period=14):
    if len(values) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(values)):
        chg = values[i] - values[i - 1]
        gains.append(max(chg, 0))
        losses.append(max(-chg, 0))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for i in range(period, len(gains)):
        avg_g = ((avg_g * (period - 1)) + gains[i]) / period
        avg_l = ((avg_l * (period - 1)) + losses[i]) / period
    if avg_l == 0: return 100
    return 100 - (100 / (1 + (avg_g / avg_l)))

def atr(candles, period=14):
    if len(candles) < period + 1: return None
    trs = []
    for i in range(1, len(candles)):
        h, l, pc = candles[i]["high"], candles[i]["low"], candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs[-period:]) / period

# =========================================================
# ANALYSIS & TRACKING LOGIC
# =========================================================

def get_m15_trend(candles):
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    if not e20 or not e50: return "NEUTRAL"
    if e20 > e50: return "BULL"
    if e20 < e50: return "BEAR"
    return "NEUTRAL"

def analyze_scalp(symbol, m5, m15):
    m15_trend = get_m15_trend(m15)
    if m15_trend == "NEUTRAL":
        return None

    closes_m5 = [c["close"] for c in m5]
    e9_series = ema_series(closes_m5, 9)
    e21_series = ema_series(closes_m5, 21)

    if not e9_series or not e21_series:
        return None

    e9 = e9_series[-1]
    e21 = e21_series[-1]
    curr_rsi = rsi(closes_m5, 14)
    curr_atr = atr(m5, 14)

    if None in (curr_rsi, curr_atr) or curr_atr <= 0:
        return None

    candle = m5[-1]
    prev = m5[-2]
    price = candle["close"]

    buy_score, sell_score = 0, 0
    buy_reasons, sell_reasons = [], []

    if m15_trend == "BULL":
        buy_score += 2
        buy_reasons.append("M15 Trend: Bullish")

    if e9 > e21:
        buy_score += 1
        buy_reasons.append("M5 EMA9 > EMA21")

    if BUY_RSI_MIN <= curr_rsi <= BUY_RSI_MAX:
        buy_score += 1
        buy_reasons.append(f"M5 RSI Momentum ({curr_rsi:.1f})")

    if candle["close"] > prev["high"]:
        buy_score += 1
        buy_reasons.append("M5 Candle Breakout")

    if m15_trend == "BEAR":
        sell_score += 2
        sell_reasons.append("M15 Trend: Bearish")

    if e9 < e21:
        sell_score += 1
        sell_reasons.append("M5 EMA9 < EMA21")

    if SELL_RSI_MIN <= curr_rsi <= SELL_RSI_MAX:
        sell_score += 1
        sell_reasons.append(f"M5 RSI Momentum ({curr_rsi:.1f})")

    if candle["close"] < prev["low"]:
        sell_score += 1
        sell_reasons.append("M5 Candle Breakdown")

    if buy_score >= MIN_SCORE and buy_score > sell_score:
        direction = "BUY"
        score = buy_score
        reasons = buy_reasons
    elif sell_score >= MIN_SCORE and sell_score > buy_score:
        direction = "SELL"
        score = sell_score
        reasons = sell_reasons
    else:
        return None

    risk = curr_atr * SL_ATR_MULTIPLIER
    if direction == "BUY":
        sl = price - risk
        tp1 = price + (risk * TP1_R_MULTIPLIER)
        tp2 = price + (risk * TP2_R_MULTIPLIER)
    else:
        sl = price + risk
        tp1 = price - (risk * TP1_R_MULTIPLIER)
        tp2 = price - (risk * TP2_R_MULTIPLIER)

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": price,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "rsi": curr_rsi,
        "atr": curr_atr,
        "score": score,
        "reasons": reasons,
        "candle_time": candle["time"]
    }

# =========================================================
# TRADE TRACKER SYSTEM
# =========================================================

def monitor_active_trades():
    """مراقبة الصفقات المفتوحة ومعرفة هل حققت الهدف أو ضربت الستوب"""
    global active_trades, stats

    to_remove = []
    for trade in active_trades:
        symbol = trade["symbol"]
        tick = get_tick(symbol)
        if not tick:
            continue

        price = float(tick.get("mid", tick.get("bid", 0)))
        if price == 0:
            continue

        direction = trade["direction"]
        sl = trade["sl"]
        tp1 = trade["tp1"]

        digits = 3 if symbol.endswith("JPY") else 5

        # فحص حركة الشراء
        if direction == "BUY":
            if price >= tp1:
                stats["wins"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                msg = (
                    f"🎯 **صفقة رابحة! (TP Hit)**\n\n"
                    f"💱 الرمز: {symbol}\n"
                    f"🟢 الاتجاه: شراء (BUY)\n"
                    f"📍 سعر الدخول: {trade['entry']:.{digits}f}\n"
                    f"✅ سعر الهدف: {tp1:.{digits}f}\n\n"
                    f"📊 **إحصائيات الإشارات:**\n"
                    f"• الناجحة: {stats['wins']} 🟢\n"
                    f"• الخاسرة: {stats['losses']} 🔴\n"
                    f"• نسبة النجاح: {win_rate:.1f}%"
                )
                if TELEGRAM_CHAT_ID:
                    send_message(TELEGRAM_CHAT_ID, msg)
                to_remove.append(trade)

            elif price <= sl:
                stats["losses"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                msg = (
                    f"🔴 **صفقة خاسرة! (SL Hit)**\n\n"
                    f"💱 الرمز: {symbol}\n"
                    f"🔴 الاتجاه: شراء (BUY)\n"
                    f"📍 سعر الدخول: {trade['entry']:.{digits}f}\n"
                    f"🛑 سعر الستوب: {sl:.{digits}f}\n\n"
                    f"📊 **إحصائيات الإشارات:**\n"
                    f"• الناجحة: {stats['wins']} 🟢\n"
                    f"• الخاسرة: {stats['losses']} 🔴\n"
                    f"• نسبة النجاح: {win_rate:.1f}%"
                )
                if TELEGRAM_CHAT_ID:
                    send_message(TELEGRAM_CHAT_ID, msg)
                to_remove.append(trade)

        # فحص حركة البيع
        elif direction == "SELL":
            if price <= tp1:
                stats["wins"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                msg = (
                    f"🎯 **صفقة رابحة! (TP Hit)**\n\n"
                    f"💱 الرمز: {symbol}\n"
                    f"🔴 الاتجاه: بيع (SELL)\n"
                    f"📍 سعر الدخول: {trade['entry']:.{digits}f}\n"
                    f"✅ سعر الهدف: {tp1:.{digits}f}\n\n"
                    f"📊 **إحصائيات الإشارات:**\n"
                    f"• الناجحة: {stats['wins']} 🟢\n"
                    f"• الخاسرة: {stats['losses']} 🔴\n"
                    f"• نسبة النجاح: {win_rate:.1f}%"
                )
                if TELEGRAM_CHAT_ID:
                    send_message(TELEGRAM_CHAT_ID, msg)
                to_remove.append(trade)

            elif price >= sl:
                stats["losses"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                msg = (
                    f"🔴 **صفقة خاسرة! (SL Hit)**\n\n"
                    f"💱 الرمز: {symbol}\n"
                    f"🔴 الاتجاه: بيع (SELL)\n"
                    f"📍 سعر الدخول: {trade['entry']:.{digits}f}\n"
                    f"🛑 سعر الستوب: {sl:.{digits}f}\n\n"
                    f"📊 **إحصائيات الإشارات:**\n"
                    f"• الناجحة: {stats['wins']} 🟢\n"
                    f"• الخاسرة: {stats['losses']} 🔴\n"
                    f"• نسبة النجاح: {win_rate:.1f}%"
                )
                if TELEGRAM_CHAT_ID:
                    send_message(TELEGRAM_CHAT_ID, msg)
                to_remove.append(trade)

    for item in to_remove:
        if item in active_trades:
            active_trades.remove(item)

# =========================================================
# TELEGRAM COMMANDS
# =========================================================

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip().lower()

    if text == "/stats":
        win_rate = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
        send_message(
            chat_id,
            f"📊 **تقرير أداء الإشارات الحالية:**\n\n"
            f"✅ الصفقات الناجحة: {stats['wins']}\n"
            f"❌ الصفقات الخاسرة: {stats['losses']}\n"
            f"📈 إجمالي الصفقات: {stats['total']}\n"
            f"🎯 نسبة النجاح: {win_rate:.1f}%\n"
            f"⏳ صفقات قيد التتبع: {len(active_trades)}"
        )
    elif text == "/start":
        send_message(chat_id, "👋 البوت يعمل بنظام السكالبينج وتتبع الصفقات التلقائي.\n\nاستخدم /stats لمشاهدة النتائج.")

def process_telegram_updates(offset):
    try:
        result = telegram("getUpdates", {"timeout": 1, "offset": offset, "allowed_updates": json.dumps(["message"])}, timeout=5)
        if not result.get("ok"): return offset
        for update in result.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message")
            if msg: handle_message(msg)
        return offset
    except:
        return offset

# =========================================================
# SCANNER & MAIN LOOP
# =========================================================

def format_scalp_signal(sig):
    sym = sig["symbol"]
    side = "⚡ SCALP BUY 🟢" if sig["direction"] == "BUY" else "⚡ SCALP SELL 🔴"
    digits = 3 if sym.endswith("JPY") else 5
    reasons = "\n".join([f"• {r}" for r in sig["reasons"]])

    return (
        f"{side}\n"
        f"💱 الرمز: {sym} (فريم 5 دقائق)\n\n"
        f"📍 الدخول: {sig['entry']:.{digits}f}\n"
        f"🛑 SL: {sig['sl']:.{digits}f}\n"
        f"🎯 TP1: {sig['tp1']:.{digits}f}\n"
        f"🎯 TP2: {sig['tp2']:.{digits}f}\n\n"
        f"📊 RSI: {sig['rsi']:.1f} | ATR: {sig['atr']:.{digits}f}\n"
        f"⭐ Score: {sig['score']}/5\n\n"
        f"🔎 الأسباب:\n{reasons}\n\n"
        f"⚠️ تذكر: لُوت 0.01 فقط للحفاظ على رأس مالك!"
    )

def check_scalp():
    for symbol in SYMBOLS:
        try:
            tick = get_tick(symbol)
            if not tick or tick.get("marketState") != "open":
                continue

            last_t = last_signal_time.get(symbol)
            if last_t and (time.time() - last_t) < (COOLDOWN_MINUTES * 60):
                continue

            m5 = get_ohlc(symbol, ENTRY_INTERVAL, ENTRY_BARS)
            m15 = get_ohlc(symbol, TREND_INTERVAL, TREND_BARS)

            sig = analyze_scalp(symbol, m5, m15)
            if sig:
                if last_signal_candle.get(symbol) == sig["candle_time"]:
                    continue

                if TELEGRAM_CHAT_ID:
                    send_message(TELEGRAM_CHAT_ID, format_scalp_signal(sig))
                    
                    # إضافة الصفقة لقائمة التتبع المباشر
                    active_trades.append({
                        "symbol": sig["symbol"],
                        "direction": sig["direction"],
                        "entry": sig["entry"],
                        "sl": sig["sl"],
                        "tp1": sig["tp1"],
                    })

                    last_signal_candle[symbol] = sig["candle_time"]
                    last_signal_time[symbol] = time.time()
                    print(f"⚡ SCALPING SIGNAL SENT: {symbol}")

        except Exception as e:
            print(f"Error {symbol}:", e)

if __name__ == "__main__":
    print("🚀 SCALPING BOT WITH TRADE TRACKING STARTED...")
    last_check = 0
    offset = None
    while True:
        try:
            offset = process_telegram_updates(offset)
            now = time.time()
            if now - last_check >= CHECK_SECONDS:
                last_check = now
                check_scalp()
                monitor_active_trades()  # مراقبة أداء الصفقات الحالية

            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("Main error:", e)
            time.sleep(5)
