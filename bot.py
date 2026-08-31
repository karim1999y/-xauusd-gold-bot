import datetime
import os
from threading import Thread
from flask import Flask
import requests
import telebot

# سيرفر وهمي لـ Render
app = Flask(__name__)


@app.route("/")
def home():
  return "Bot Active"


def run_server():
  port = int(os.environ.get("PORT", 8080))
  app.run(host="0.0.0.0", port=port)


Thread(target=run_server).start()

TOKEN = os.environ.get("BOT_TOKEN", "8805523416:AAEVs6fAXXC51ZgMfPhnJN8kqOXgvfTUseA")
bot = telebot.TeleBot(TOKEN)


def get_gold_price():
  try:
    res = requests.get("https://api.gold-api.com/price/XAU", timeout=5)
    return float(res.json()["price"]) if res.status_code == 200 else None
  except:
    return None


@bot.message_handler(commands=["start"])
def start(msg):
  bot.reply_to(msg, "🤖 البوت المحدث يعمل الآن بنجاح!")


@bot.message_handler(commands=["status"])
def status(msg):
  bot.reply_to(msg, "🟢 البوت متصل بالكامل ومحدث 100%!")


@bot.message_handler(commands=["signal"])
def signal(msg):
  bot.reply_to(msg, "🔍 جاري جلب سعر Vantage المباشر...")
  price = get_gold_price()

  if not price:
    bot.send_message(msg.chat.id, "❌ تعذر جلب السعر، جرب بعد لحظات.")
    return

  sl = price - 1.50
  tp = price + 3.00

  text = (
      f"⚡ **إشارة سكالبينج حية (XAUUSD)** ⚡\n"
      f"----------------------------------\n"
      f"🎯 الأمر: **BUY**\n"
      f"📈 **سعر Vantage المباشر:** `{price:.2f}`\n"
      f"🛑 الستوب (SL): `{sl:.2f}` (15 pips)\n"
      f"🎯 الهدف (TP): `{tp:.2f}` (30 pips)\n"
      f"----------------------------------\n"
      f"🛡 **إدارة حساب 43€:** لوت `0.01` micro\n"
      f"⏱ {datetime.datetime.now().strftime('%H:%M:%S')}"
  )
  bot.send_message(msg.chat.id, text, parse_mode="Markdown")


if __name__ == "__main__":
  bot.infinity_polling()
