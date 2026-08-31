import datetime
import os
import telebot
import yfinance as yf

TOKEN = os.environ.get("BOT_TOKEN", "ضع_توكن_البوت_هنا")
bot = telebot.TeleBot(TOKEN)


# دالة حساب مؤشر RSI
def calculate_rsi(data, window=14):
  delta = data["Close"].diff()
  gain = (delta.where(delta > 0, 0)).rolling(window=window).mean()
  loss = (-delta.where(delta < 0, 0)).rolling(window=window).mean()
  rs = gain / loss
  return 100 - (100 / (1 + rs))


# دالة تحليل السكالبينج (EMA + RSI)
def analyze_scalping(symbol_ticker, symbol_name):
  try:
    # جلب بيانات فريم 5 دقائق
    df = yf.download(tickers=symbol_ticker, period="1d", interval="5m")

    if df.empty or len(df) < 20:
      return f"❌ لا تتوفر سيولة أو بيانات كافية لـ {symbol_name} حالياً."

    # حساب المؤشرات الفنية
    df["EMA_9"] = df["Close"].ewm(span=9, adjust=False).mean()
    df["EMA_21"] = df["Close"].ewm(span=21, adjust=False).mean()
    df["RSI"] = calculate_rsi(df, window=14)

    last_close = float(df["Close"].iloc[-1])
    ema_9 = float(df["EMA_9"].iloc[-1])
    ema_21 = float(df["EMA_21"].iloc[-1])
    rsi = float(df["RSI"].iloc[-1])

    # استراتيجية السكالبينج
    action = None
    if ema_9 > ema_21 and rsi > 50:
      action = "BUY"
    elif ema_9 < ema_21 and rsi < 50:
      action = "SELL"

    if action:
      # حساب الأهداف ووقف الخسارة للسكالبينج (نقاط سريعة)
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
      return msg
    else:
      return f"⏳ السوق في حالة تذبذب لـ {symbol_name}، لا توجد فرصة سكالبينج واضحة الآن (RSI: {rsi:.1f})."

  except Exception as e:
    return f"❌ خطأ أثناء تحليل السكالبينج: {str(e)}"


@bot.message_handler(commands=["start"])
def start(message):
  bot.reply_to(
      message,
      "🤖 بوت السكالبينج جاهز!\nأرسل /signal للحصول على صفقة سكالبينج حية.",
  )


@bot.message_handler(commands=["status"])
def status(message):
  bot.reply_to(message, "🟢 بوت السكالبينج متصل 24/7 ومربوط بالتحليل الفني!")


@bot.message_handler(commands=["signal"])
def signal(message):
  bot.reply_to(message, "🔍 جاري فحص مؤشرات السكالبينج (5m)...")
  gold_res = analyze_scalping("GC=F", "XAUUSD")
  bot.send_message(message.chat.id, gold_res, parse_mode="Markdown")


if __name__ == "__main__":
  bot.infinity_polling()
