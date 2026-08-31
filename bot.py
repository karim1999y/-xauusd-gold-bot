import os
import datetime
import telebot
import yfinance as yf

# استخراج التوكن من متغيرات البيئة أو وضعه مباشرة
TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
bot = telebot.TeleBot(TOKEN)


# دالة جلب بيانات التحليل والسعر المباشر
def get_market_signal(symbol_ticker, symbol_name):
  try:
    ticker = yf.Ticker(symbol_ticker)
    data = ticker.history(period="1d", interval="5m")

    if data.empty:
      return f"❌ تعذر جلب بيانات {symbol_name} حالياً."

    current_price = data["Close"].iloc[-1]
    prev_price = data["Close"].iloc[-2] if len(data) > 1 else current_price

    # تحديد اتجاه مبسط بناءً على حركة السعر
    direction = "🟢 شراء (BUY)" if current_price >= prev_price else "🔴 بيع (SELL)"

    signal_msg = (
        f"📊 **إشارة تداول جديدة ({symbol_name})**\n"
        f"----------------------------------\n"
        f"🔹 الاتجاه: {direction}\n"
        f"💰 السعر الحالي: {current_price:.2f}\n"
        f"⏱ التوقيت: {datetime.datetime.now().strftime('%H:%M:%S')}\n"
        f"----------------------------------\n"
        f"💡 تم جلب الإشارة بنجاح!"
    )
    return signal_msg
  except Exception as e:
    return f"❌ حدث خطأ أثناء جلب البيانات: {str(e)}"


# أمر /start
@bot.message_handler(commands=["start"])
def send_welcome(message):
  bot.reply_to(
      message,
      "أهلاً بك! البوت جاهز ومربوط بالسوق.\nأرسل /signal للحصول على إشارة جديدة أو /status لمعرفة الحالة.",
  )


# أمر /status
@bot.message_handler(commands=["status"])
def send_status(message):
  bot.reply_to(
      message,
      "🟢 حالة البوت: متصل ويعمل 24/7 على Render!\n📊 الرموز: XAUUSD / EURUSD",
  )


# أمر /signal (يعمل دائماً بدون فحص العطلة)
@bot.message_handler(commands=["signal"])
def send_signal(message):
  bot.reply_to(message, "⏳ جاري تحليل السوق وجلب الإشارة...")

  # جلب إشارة الذهب كمثال
  gold_signal = get_market_signal("GC=F", "XAUUSD (الذهب)")
  bot.send_message(message.chat.id, gold_signal, parse_mode="Markdown")


if __name__ == "__main__":
  print("🚀 البوت يعمل الآن ويستقبل الأوامر...")
  bot.infinity_polling()
