import datetime
import os
from threading import Thread
from flask import Flask
import numpy as np
import pandas as pd
import telebot
import yfinance as yf

# --- 1. سيرفر وهمي لإرضاء Render في الخطة المجانية Web Service ---
app = Flask(__name__)


@app.route("/")
def home():
  return "Bot is Active 24/7!"


def run_web_server():
  port = int(os.environ.get("PORT", 8080))
  app.run(host="0.0.0.0", port=port)


t = Thread(target=run_web_server)
t.start()

# --- 2. إعدادات بوت التليجرام ---
TOKEN = os.environ.get("BOT_TOKEN", "8805523416:AAEVs6fAXXC51ZgMfPhnJN8kqOXgvfTUseA")
bot = telebot.TeleBot(TOKEN)


def calculate_rsi(data, window=14):
  delta = data["Close"].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


# --- 3. دالة التحليل الفني والسكالبينج ---
def analyze_scalping(symbol_ticker, symbol_name):
  try:
    # جلب بيانات 5 دقائق
    df = yf.download(
        tickers=symbol_ticker, period="1d", interval="5m", progress=False
    )

    if df.empty or len(df) < 20:
      return f"❌ لا تتوفر بيانات كافية لـ {symbol_name} حالياً."

    # معالجة الأعمدة في حال رجوع MultiIndex من yfinance
    if isinstance(df.columns, pd.MultiIndex):
      df.columns = df.columns.get_level_values(0)

    # حساب المؤشرات
    df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["RSI"] = calculate_rsi(df, window=14)

    # تحويل القيم إلى أرقام صريحة لتفادي خطأ Series
    last_close = float(df["Close"].to_numpy()[-1])
    ema_9 = float(df["EMA_9"].to_numpy()[-1])
    ema_21 = float(df["EMA_21"].to_numpy()[-1])
    rsi = float(df["RSI"].to_numpy()[-1])

    action = "BUY" if (ema_9 > ema_21 and rsi > 50) else "SELL"

    # إعدادات المخاطرة والستوب لحساب 43€
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

    msg = (
        f"⚡ **إشارة سكالبينج حية ({symbol_name})** ⚡\n"
        f"----------------------------------\n"
        f"🎯 الأمر: **{action}**\n"
        f"💵 سعر الدخول: `{last_close:.2f}`\n"
        f"🛑 الستوب (SL): `{sl:.2f}` (15 pips)\n"
        f"🎯 الهدف (TP): `{tp:.2f}` (30 pips)\n"
        f"📊 RSI: `{rsi:.1f}` | EMA: `{ema_9:.2f}/{ema_21:.2f}`\n"
        f"----------------------------------\n"
        f"🛡 **تنبيه إدارة الحساب (43€):**\n"
        f"🔹 حجم اللوت الموصى به: `0.01` فقط\n"
        f"🔹 أقصى مخاطرة للصفقة: ~1.20€\n"
        f"----------------------------------\n"
        f"⏱ {datetime.datetime.now().strftime('%H:%M:%S')}"
    )
    return msg

  except Exception as e:
    return f"❌ خطأ أثناء التحليل: {str(e)}"


# --- 4. أوامر التليجرام ---
@bot.message_handler(commands=["start"])
def start(message):
  bot.reply_to(
      message,
      "🤖 بوت السكالبينج جاهز وآمن لحساب 43€!\nأرسل /signal للحصول على صفقة.",
  )


@bot.message_handler(commands=["status"])
def status(message):
  bot.reply_to(message, "🟢 البوت متصل ومحدث 100% على Render!")


@bot.message_handler(commands=["signal"])
def signal(message):
  bot.reply_to(message, "🔍 جاري فحص مؤشرات السكالبينج (5m)...")
  gold_res = analyze_scalping("GC=F", "XAUUSD (الذهب)")
  bot.send_message(message.chat.id, gold_res, parse_mode="Markdown")


if __name__ == "__main__":
  bot.infinity_polling()
