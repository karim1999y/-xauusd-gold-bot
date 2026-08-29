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

CHECK_SECONDS = 20  # فحص كل 20 ثانية لتتبع حركة البتكوين اللحظية

# الاعتماد حصراً على البتكوين
SYMBOL = "BTCUSDT"

ENTRY_INTERVAL = "5m"
TREND_INTERVAL = "15m"

ENTRY_BARS = 200
TREND_BARS = 200

MIN_SCORE = 5

# إدارة المخاطر مخصصة للبتكوين
TP1_R_MULTIPLIER = 1.4
TP2_R_MULTIPLIER = 2.2
SL_ATR_MULTIPLIER = 1.4

COOLDOWN_MINUTES = 15  # مهلة 15 دقيقة بين كل صفقة بتكوين وأخرى

BUY_RSI_MIN = 48
BUY_RSI_MAX = 68
SELL_RSI_MIN = 32
SELL_RSI_MAX = 52

# =========================================================
# TRACKING STATE
# =========================================================

last_signal_candle = None
last_signal_time = 0

active_trades = []

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
        headers={"User-Agent": "BTC-Scalper/1.0", "Accept": "application/json"},
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
    req = urllib.request.Request(url, data=encoded, headers={"User-Agent": "BTC-Scalper/1.0"}, method="POST" if encoded else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))

def send_message(chat_id, text):
    return telegram("sendMessage", {"chat_id": str(chat_id), "text": text})

# =========================================================
# MARKET DATA & INDICATORS
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

def ema_series(values, period):
    if len(values) < period: return []
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
    if len(values) < period + 1: return None
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
# BTC ANALYSIS
# =========================================================

def get_m15_trend(candles):
    closes = [c["close"] for c in candles]
    e20 = ema(closes, 20)
    e50 = ema(closes, 50)
    if not e20 or not e50: return "NEUTRAL"
    if e20 > e50: return "BULL"
    if e20 < e50: return "BEAR"
    return "NEUTRAL"

def analyze_btc(m5, m15):
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

    # BUY (LONG)
    if m15_trend == "BULL":
        buy_score += 2
        buy_reasons.append("M15 Trend: Bullish 🚀")

    if e9 > e21:
        buy_score += 1
        buy_reasons.append("M5 EMA9 > EMA21 Cross")

    if BUY_RSI_MIN <= curr_rsi <= BUY_RSI_MAX:
        buy_score += 1
        buy_reasons.append(f"M5 RSI Bullish Momentum ({curr_rsi:.1f})")

    if candle["close"] > prev["high"]:
        buy_score += 1
        buy_reasons.append("M5 Candle Breakout")

    # SELL (SHORT)
    if m15_trend == "BEAR":
        sell_score += 2
        sell_reasons.append("M15 Trend: Bearish 📉")

    if e9 < e21:
        sell_score += 1
        sell_reasons.append("M5 EMA9 < EMA21 Cross")

    if SELL_RSI_MIN <= curr_rsi <= SELL_RSI_MAX:
        sell_score += 1
        sell_reasons.append(f"M5 RSI Bearish Momentum ({curr_rsi:.1f})")

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
        "symbol": SYMBOL,
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
# MONITORING & COMMANDS
# =========================================================

def monitor_btc_trade():
    global active_trades, stats
    to_remove = []
    for trade in active_trades:
        tick = get_tick(SYMBOL)
        if not tick: continue

        price = float(tick.get("mid", tick.get("bid", 0)))
        if price == 0: continue

        direction = trade["direction"]
        sl = trade["sl"]
        tp1 = trade["tp1"]

        if direction == "BUY":
            if price >= tp1:
                stats["wins"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                send_message(TELEGRAM_CHAT_ID, f"🎯 **BITCOIN TP HIT!**\n\n🪙 BTCUSDT (LONG)\n📍 Entry: {trade['entry']:.2f}\n✅ Target: {tp1:.2f}\n\n📊 Win Rate: {win_rate:.1f}% ({stats['wins']}/{stats['total']})")
                to_remove.append(trade)
            elif price <= sl:
                stats["losses"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                send_message(TELEGRAM_CHAT_ID, f"🔴 **BITCOIN SL HIT!**\n\n🪙 BTCUSDT (LONG)\n📍 Entry: {trade['entry']:.2f}\n🛑 Stop: {sl:.2f}\n\n📊 Win Rate: {win_rate:.1f}% ({stats['wins']}/{stats['total']})")
                to_remove.append(trade)

        elif direction == "SELL":
            if price <= tp1:
                stats["wins"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                send_message(TELEGRAM_CHAT_ID, f"🎯 **BITCOIN TP HIT!**\n\n🪙 BTCUSDT (SHORT)\n📍 Entry: {trade['entry']:.2f}\n✅ Target: {tp1:.2f}\n\n📊 Win Rate: {win_rate:.1f}% ({stats['wins']}/{stats['total']})")
                to_remove.append(trade)
            elif price >= sl:
                stats["losses"] += 1
                stats["total"] += 1
                win_rate = (stats["wins"] / stats["total"]) * 100
                send_message(TELEGRAM_CHAT_ID, f"🔴 **BITCOIN SL HIT!**\n\n🪙 BTCUSDT (SHORT)\n📍 Entry: {trade['entry']:.2f}\n🛑 Stop: {sl:.2f}\n\n📊 Win Rate: {win_rate:.1f}% ({stats['wins']}/{stats['total']})")
                to_remove.append(trade)

    for item in to_remove:
        if item in active_trades:
            active_trades.remove(item)

def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip().lower()

    if text == "/stats":
        win_rate = (stats["wins"] / stats["total"] * 100) if stats["total"] > 0 else 0
        send_message(
            chat_id,
            f"⚡ **تقرير أداء إشارات البتكوين (BTC):**\n\n"
            f"🟢 الناجحة: {stats['wins']}\n"
            f"🔴 الخاسرة: {stats['losses']}\n"
            f"📊 الإجمالي: {stats['total']}\n"
            f"🎯 نسبة النجاح: {win_rate:.1f}%\n"
            f"⏳ صفقات قيد التتبع: {len(active_trades)}"
        )

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
# MAIN LOOP
# =========================================================

def format_btc_signal(sig):
    side = "⚡ BTC LONG 🟢" if sig["direction"] == "BUY" else "⚡ BTC SHORT 🔴"
    reasons = "\n".join([f"• {r}" for r in sig["reasons"]])

    return (
        f"{side}\n"
        f"💱 الرمز: BTCUSDT (فريم 5 دقائق)\n\n"
        f"📍 سعر الدخول: {sig['entry']:.2f}\n"
        f"🛑 SL: {sig['sl']:.2f}\n"
        f"🎯 TP1: {sig['tp1']:.2f}\n"
        f"🎯 TP2: {sig['tp2']:.2f}\n\n"
        f"📊 RSI: {sig['rsi']:.1f} | ATR: {sig['atr']:.2f}\n"
        f"⭐ Score: {sig['score']}/5\n\n"
        f"🔎 الأسباب:\n{reasons}\n\n"
        f"⚠️ تذكرة لحساب 43€: الرافعة المالية 3X - 5X كحد أقصى والدخول بـ 4€ إلى 5€ للصفقة."
    )

def check_btc():
    global last_signal_candle, last_signal_time
    try:
        tick = get_tick(SYMBOL)
        if not tick: return

        if last_signal_time and (time.time() - last_signal_time) < (COOLDOWN_MINUTES * 60):
            return

        m5 = get_ohlc(SYMBOL, ENTRY_INTERVAL, ENTRY_BARS)
        m15 = get_ohlc(SYMBOL, TREND_INTERVAL, TREND_BARS)

        sig = analyze_btc(m5, m15)
        if sig:
            if last_signal_candle == sig["candle_time"]:
                return

            if TELEGRAM_CHAT_ID:
                send_message(TELEGRAM_CHAT_ID, format_btc_signal(sig))
                active_trades.append({
                    "direction": sig["direction"],
                    "entry": sig["entry"],
                    "sl": sig["sl"],
                    "tp1": sig["tp1"],
                })
                last_signal_candle = sig["candle_time"]
                last_signal_time = time.time()
                print("⚡ BTC SIGNAL SENT!")

    except Exception as e:
        print("Error checking BTC:", e)

if __name__ == "__main__":
    print("🚀 BTC ONLY SCALPER STARTED...")
    last_check = 0
    offset = None
    while True:
        try:
            offset = process_telegram_updates(offset)
            now = time.time()
            if now - last_check >= CHECK_SECONDS:
                last_check = now
                check_btc()
                monitor_btc_trade()

            time.sleep(1)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("Main error:", e)
            time.sleep(5)
