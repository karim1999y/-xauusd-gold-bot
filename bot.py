import json
import logging
import os
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# 1. خادم المنفذ الوهمي لـ Render (Health Check)
# ==========================================
class HealthCheckHandler(BaseHTTPRequestHandler):

  def do_GET(self):
    self.send_response(200)
    self.send_header('Content-type', 'text/html')
    self.end_headers()
    self.wfile.write(b'OK - Scalping Bot is Running Live!')

  def log_message(self, format, *args):
    return


def run_port_listener():
  port = int(os.environ.get('PORT', 10000))
  server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
  server.serve_forever()


threading.Thread(target=run_port_listener, daemon=True).start()

# ==========================================
# 2. الإعدادات وإرسال الرسائل
# ==========================================
logging.basicConfig(
    level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s'
)

TELEGRAM_TOKEN = os.environ.get('TELEGRAM_TOKEN', '')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID', '')


def send_telegram_message(message, chat_id=None):
  target_chat = chat_id or TELEGRAM_CHAT_ID
  if not TELEGRAM_TOKEN or not target_chat:
    return False

  url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage'
  payload = {'chat_id': target_chat, 'text': message, 'parse_mode': 'HTML'}

  try:
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(
        url, data=data, headers={'Content-Type': 'application/json'}
    )
    with urllib.request.urlopen(req) as response:
      return response.status == 200
  except Exception as e:
    logging.error(f'خطأ أثناء إرسال الرسالة: {e}')
    return False


# ==========================================
# 3. الاستماع والاستجابة لأوامر تليجرام
# ==========================================
def poll_telegram_updates():
  offset = 0
  while True:
    try:
      url = f'https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates?offset={offset}&timeout=30'
      req = urllib.request.Request(url)
      with urllib.request.urlopen(req) as response:
        result = json.loads(response.read().decode('utf-8'))
        if result.get('ok'):
          for update in result.get('result', []):
            offset = update['update_id'] + 1
            message = update.get('message', {})
            text = message.get('text', '')
            chat_id = message.get('chat', {}).get('id')

            if text == '/status':
              reply = (
                  '🟢 <b>حالة البوت:</b> متصل ويعمل 24/7 على Render!\n📊'
                  ' <b>الرموز:</b> XAUUSD / EURUSD'
              )
              send_telegram_message(reply, chat_id)
            elif text == '/signal':
              reply = (
                  '🔴 <b>السوق مغلق حالياً (عطلة نهاية الأسبوع).</b>\n⏳ ستتم'
                  ' استئناف التحليلات مع افتتاح السوق.'
              )
              send_telegram_message(reply, chat_id)
    except Exception as e:
      logging.error(f'خطأ في استقبال الأوامر: {e}')
      time.sleep(5)


if __name__ == '__main__':
  send_telegram_message('🤖 <b>تم إقلاع البوت وتثبيت المنفذ بنجاح!</b>')
  poll_telegram_updates()
