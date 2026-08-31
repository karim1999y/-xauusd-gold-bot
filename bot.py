import datetime
import os
from threading import Thread
from flask import Flask
import telebot
import yfinance as yf

# --- 1. سيرفر وهمي لإرضاء Render في الخطة المجانية Web Service ---
app = Flask(__name__)


@app.route("/")
def home():
  return "Bot is Alive Free 24/7!"


def run_web_server():
  port = int(os.environ.get("PORT", 8080))
  app.run(host="0.0.0.0", port=port)


# تشغيل السيرفر في خلفية الكود
t = Thread(target=run_web_server)
t.start()

# --- 2. إعدادات بوت التليجرام والسكالبينج ---
TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
bot = telebot.TeleBot(TOKEN)


def calculate_rsi(data, window=14):
  delta = data["Close"].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


def analyze_scalping(symbol_ticker, symbol_name):
  try:
    df = yf.download(tickers=symbol_ticker, period="1d", interval="5m")
    if df.empty or len(df) < 20:
      return f"❌ لا تتوفر سيولة كافية لـ {symbol_name} حالياً."

    df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["RSI"] = calculate_rsi(df, window=14)

    last_close = float(df["Close"].iloc[-1])
    ema_9 = float(df["EMA_9"].iloc[-1])
    ema_21 = float(df["EMA_21"].iloc[-1])
    rsi = float(df["RSI"].iloc[-1])

    action = None
    if ema_9 > ema_21 and rsi > 50:
      action = "BUY"
    elif ema_9 < ema_21 and rsi < 50:
      action = "SELL"

    if action:
      pip_unit = 0.1 if "GC=F" in symbol_ticker else 0.0001
      sl = (
          last_close - (15 * pip_unit)
          if action == "BUY"
          else last_close + (15 * pip_unit)
      )
      tp = (
          last_close + (30 * pip_unit)
          if action == "BUY"
          else last_close - (30 * pip_unit)
      )

      return (
          f"⚡ **إشارة سكالبينج جديدة ({symbol_name})** ⚡\n"
          f"----------------------------------\n"
          f"🎯 الأمر: **{action}**\n"
          f"💵 سعر الدخول: `{last_close:.2f}`\n"
          f"🛑 الستوب (SL): `{sl:.2f}` (15 pips)\n"
          f"🎯 الهدف (TP): `{tp:.2f}` (30 pips)\n"
          f"📊 RSI: `{rsi:.1f}` | EMA: `{ema_9:.2f}/{ema_21:.2f}`\n"
          f"----------------------------------\n"
          f"⏱ {datetime.datetime.now().strftime('%H:%M:%S')}"
      )
    else:
      return f"⏳ السوق في حالة تذبذب لـ {symbol_name} (RSI: {rsi:.1f})."

  except Exception as e:
    return f"❌ خطأ في التحليل: {str(e)}"


@bot.message_handler(commands=["start"])
def start(message):
  bot.reply_to(
      message,
      "🤖 بوت السكالبينج المجاني جاهز!\nأرسل /signal للحصول على إشارة.",
  )


@bot.message_handler(commands=["status"])
def status(message):
  bot.reply_to(message, "🟢 حالة البوت: شغال ومجاني 100% على Render!")


@bot.message_handler(commands=["signal"])
def signal(message):
  bot.reply_to(message, "🔍 جاري فحص مؤشرات السكالبينج (5m)...")
  gold_res = analyze_scalping("GC=F", "XAUUSD")
  bot.send_message(message.chat.id, gold_res, parse_mode="Markdown")


if __name__ == "__main__":
  bot.infinity_polling()
