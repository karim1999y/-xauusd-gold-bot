import os
import time
import json
import urllib.request
import urllib.parse

TOKEN = os.getenv("TELEGRAM_TOKEN")

if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN is not set")


API = f"https://api.telegram.org/bot{TOKEN}"


def telegram(method, data=None):
    url = f"{API}/{method}"

    if data:
        data = urllib.parse.urlencode(data).encode()

    with urllib.request.urlopen(url, data=data, timeout=30) as response:
        return json.loads(response.read().decode())


def send_message(chat_id, text):
    telegram(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
        },
    )


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text == "/start":
        send_message(
            chat_id,
            "👋 أهلاً بك في XAU Gold Signals\n\n"
            "🟡 بوت إشارات الذهب XAUUSD\n\n"
            "اكتب /test لتجربة البوت."
        )

    elif text == "/test":
        send_message(
            chat_id,
            "✅ البوت يعمل بنجاح!\n\n"
            "🟡 XAUUSD TEST SIGNAL\n"
            "📊 الاتجاه: SELL\n"
            "🎯 Entry: 4625-4629\n"
            "🛑 SL: 4636\n"
            "🎯 TP1: 4610\n"
            "🎯 TP2: 4600\n\n"
            "⚠️ هذه إشارة تجريبية وليست توصية مالية."
        )

    else:
        send_message(
            chat_id,
            "الأوامر المتاحة:\n\n"
            "/start - تشغيل البوت\n"
            "/test - اختبار البوت"
        )


def main():
    print("Bot started...")

    offset = None

    while True:
        try:
            result = telegram(
                "getUpdates",
                {
                    "timeout": 25,
                    "offset": offset,
                },
            )

            for update in result.get("result", []):
                offset = update["update_id"] + 1

                message = update.get("message")

                if message:
                    handle_message(message)

        except Exception as e:
            print("Error:", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
