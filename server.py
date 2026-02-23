from flask import Flask
from playwright.sync_api import sync_playwright
import requests, threading, time, os, imaplib, email
from email.header import decode_header

app = Flask(__name__)

# ══════════════════════════════════════════════════════════
#  CREDENTIALS — set these in Render Environment Variables
# ══════════════════════════════════════════════════════════
BOT_TOKEN    = os.environ.get("BOT_TOKEN",    "")
CHAT_ID      = os.environ.get("CHAT_ID",      "")
CHART_URL    = os.environ.get("CHART_URL",    "")
GMAIL_USER   = os.environ.get("GMAIL_USER",   "")   # your gmail address
GMAIL_PASS   = os.environ.get("GMAIL_PASS",   "")   # gmail app password
POLL_EVERY   = int(os.environ.get("POLL_EVERY", "30"))  # check email every 30 sec

# ══════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════
def send_telegram_message(message):
    try:
        url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
        r       = requests.post(url, data=payload)
        print(f"Telegram message sent: {r.status_code}")
    except Exception as e:
        print(f"Telegram message error: {e}")

def send_telegram_photo(photo_path, caption):
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto"
        with open(photo_path, "rb") as photo:
            payload = {"chat_id": CHAT_ID, "caption": caption, "parse_mode": "HTML"}
            r       = requests.post(url, data=payload, files={"photo": photo})
        print(f"Telegram photo sent: {r.status_code}")
        os.remove(photo_path)
    except Exception as e:
        print(f"Telegram photo error: {e}")

# ══════════════════════════════════════════════════════════
#  SCREENSHOT
# ══════════════════════════════════════════════════════════
def take_screenshot():
    path = f"/tmp/chart_{int(time.time())}.png"
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu"]
            )
            page = browser.new_page(viewport={"width": 1600, "height": 900})
            print(f"Opening chart...")
            page.goto(CHART_URL, wait_until="networkidle", timeout=30000)
            time.sleep(6)
            page.screenshot(path=path, full_page=False)
            browser.close()
        print(f"Screenshot saved: {path}")
        return path
    except Exception as e:
        print(f"Screenshot error: {e}")
        return None

# ══════════════════════════════════════════════════════════
#  ALERT SENDER
# ══════════════════════════════════════════════════════════
def send_alert(symbol, level_type, level_price):
    msg = (
        f"🚨 <b>KEY LEVEL ALERT!</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📊 Symbol : {symbol}\n"
        f"📍 Level  : {level_price} ({level_type})\n"
        f"🕐 Time   : {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"⚠️ Review chart before taking any action."
    )
    print(f"Sending alert for {symbol} @ {level_price}...")
    photo = take_screenshot()
    if photo:
        send_telegram_photo(photo, msg)
    else:
        send_telegram_message(msg + "\n\n⚠️ Screenshot failed.")

# ══════════════════════════════════════════════════════════
#  GMAIL IMAP MONITOR
#  Looks for emails with subject starting with "SnR ALERT"
#  Expected subject format:
#  SnR ALERT | BTCUSDT | A Level | 97500.00
# ══════════════════════════════════════════════════════════
def parse_alert_email(subject):
    # Subject format: SnR ALERT | BTCUSDT | A Level | 97500.00
    try:
        parts = [p.strip() for p in subject.split("|")]
        if len(parts) == 4 and parts[0] == "SnR ALERT":
            symbol      = parts[1]
            level_type  = parts[2]
            level_price = parts[3]
            return symbol, level_type, level_price
    except Exception as e:
        print(f"Email parse error: {e}")
    return None, None, None

def gmail_monitor():
    print("📧 Gmail monitor started...")
    send_telegram_message("🤖 <b>SnR Alert Bot is LIVE!</b>\nMonitoring TradingView email alerts 24/7...")

    while True:
        try:
            # Connect to Gmail via IMAP
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(GMAIL_USER, GMAIL_PASS)
            mail.select("inbox")

            # Search for UNREAD emails with "SnR ALERT" in subject
            status, messages = mail.search(None, '(UNSEEN SUBJECT "SnR ALERT")')

            if status == "OK":
                email_ids = messages[0].split()

                if email_ids:
                    print(f"Found {len(email_ids)} new alert email(s)")

                for email_id in email_ids:
                    # Fetch the email
                    status, msg_data = mail.fetch(email_id, "(RFC822)")

                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])

                            # Decode subject
                            subject_raw = msg["Subject"]
                            subject, encoding = decode_header(subject_raw)[0]
                            if isinstance(subject, bytes):
                                subject = subject.decode(encoding or "utf-8")

                            print(f"Email subject: {subject}")

                            # Parse the subject line
                            symbol, level_type, level_price = parse_alert_email(subject)

                            if symbol:
                                print(f"✅ Parsed: {symbol} | {level_type} | {level_price}")
                                # Send alert in its own thread
                                threading.Thread(
                                    target=send_alert,
                                    args=(symbol, level_type, level_price),
                                    daemon=True
                                ).start()
                            else:
                                print(f"⚠️ Could not parse subject: {subject}")

                    # Mark email as READ so we don't process it again
                    mail.store(email_id, "+FLAGS", "\\Seen")

            mail.logout()

        except Exception as e:
            print(f"Gmail monitor error: {e}")

        time.sleep(POLL_EVERY)

# ══════════════════════════════════════════════════════════
#  HEALTH CHECK ENDPOINTS
# ══════════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def home():
    return {"status": "SnR Alert Bot is alive ✅"}, 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "running"}, 200

# ══════════════════════════════════════════════════════════
#  START
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Start Gmail monitor in background thread
    monitor_thread = threading.Thread(target=gmail_monitor, daemon=True)
    monitor_thread.start()

    # Start Flask server
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting server on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
