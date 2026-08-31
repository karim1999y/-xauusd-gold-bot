import datetime
import os
import time
from threading import Thread
from flask import Flask
import requests
import telebot

# 1. إنشاء سيرفر وهمي لإبقاء Render يعمل 24/7 دون توقف
app = Flask(__name__)


@app.route('/')
def home():
  return 'Bot is Running'


def run_web():
  port = int(os.environ.get('PORT', 10000))
  app.run(host='0.0.0.0', port=port)


# تشغيل سيرفر الويب في خيط خلفي (Thread)
t = Thread(target=run_web)
t.daemon = True
t.start()

# 2. إعداد التوكن وتشفيره عبر Environment Variables
TOKEN = os.environ.get('BOT_TOKEN', 'ضع_التوكن_هنا')
bot = telebot.TeleBot(TOKEN)


def get_gold_price():
  """جلب سعر الذهب المباشر"""
  try:
    res = requests.get('https://api.gold-api.com/price/XAU', timeout=5)
    if res.status_code == 200:
      return float(res.json()['price'])
    return None
  except Exception as e:
    print(f'Error fetching price: {e}')
    return None


@bot.message_handler(commands=['start'])
def start(msg):
  bot.reply_to(
      msg, '🤖 أهلاً بك! البوت المحدث يعمل الآن بنجاح ومربوط بالسوق المباشر.'
  )


@bot.message_handler(commands=['status'])
def status(msg):
  bot.reply_to(msg, '🟢 حالة البوت: متصل بالكامل ومحدث 100% على Render!')


@bot.message_handler(commands=['signal'])
def signal(msg):
  bot.reply_to(msg, '🔍 جاري جلب سعر Vantage المباشر...')
  price = get_gold_price()

  if not price:
    bot.send_message(
        msg.chat.id, '❌ تعذر جلب السعر حالياً، أعد المحاولة بعد لحظات.'
    )
    return

  # حساب الأهداف والستوب لسكالبينج الذهب
  sl = price - 1.50
  tp = price + 3.00

  text = (
      f'⚡ **إشارة سكالبينج حية (XAUUSD)** ⚡\n'
      f'----------------------------------\n'
      f'🎯 الأمر: **BUY**\n'
      f'📈 **سعر Vantage المباشر:** `{price:.2f}`\n'
      f'🛑 الستوب (SL): `{sl:.2f}` (15 pips)\n'
      f'🎯 الهدف (TP): `{tp:.2f}` (30 pips)\n'
      f'----------------------------------\n'
      f'🛡 **إدارة حساب 43€:** لوت `0.01` micro\n'
      f'⏱ {datetime.datetime.now().strftime("%H:%M:%S")}'
  )
  bot.send_message(msg.chat.id, text, parse_mode='Markdown')


# 3. تشغيل استماع البوت وتنظيف الاتصالات القديمة لتفادي خطأ 409
if __name__ == '__main__':
  # مسح أي اتصالات معلقة أو جلسات سابقة
  bot.remove_webhook()
  time.sleep(1)

  # البدء في الاستماع وتخطي الطلبات المعلقة القديمة
  bot.infinity_polling(skip_pending=True)
