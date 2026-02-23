from flask import Flask
import requests, threading, time, os, imaplib, email
from email.header import decode_header

app = Flask(__name__)

# ══════════════════════════════════════════════════════════
#  CREDENTIALS — set in Render Environment Variables
# ══════════════════════════════════════════════════════════
BOT_TOKEN  = os.environ.get("BOT_TOKEN",  "")
CHAT_ID    = os.environ.get("CHAT_ID",    "")
GMAIL_USER = os.environ.get("GMAIL_USER", "")
GMAIL_PASS = os.environ.get("GMAIL_PASS", "")
POLL_EVERY = int(os.environ.get("POLL_EVERY", "30"))

# ══════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════
def send_telegram_message(message):
    try:
        url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id"    : CHAT_ID,
            "text"       : message,
            "parse_mode" : "HTML"
        }
        r = requests.post(url, data=payload)
        print(f"Telegram sent: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

# ══════════════════════════════════════════════════════════
#  EMAIL PARSER
#  Expected subject format:
#  SnR ALERT | XAUUSD | A Level | 2650.00
# ══════════════════════════════════════════════════════════
def parse_alert_email(subject):
    try:
        parts = [p.strip() for p in subject.split("|")]
        if len(parts) == 4 and parts[0].strip() == "SnR ALERT":
            symbol      = parts[1]
            level_type  = parts[2]
            level_price = parts[3]
            return symbol, level_type, level_price
    except Exception as e:
        print(f"Parse error: {e}")
    return None, None, None

# ══════════════════════════════════════════════════════════
#  ALERT MESSAGE BUILDER
# ══════════════════════════════════════════════════════════
def send_alert(symbol, level_type, level_price):
    message = (
        f"🚨 <b>KEY LEVEL ALERT!</b>\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📊 Symbol  : {symbol}\n"
        f"📍 Level   : {level_price} ({level_type})\n"
        f"⏰ Time    : {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📝 Open TradingView to review."
    )
    print(f"Sending alert → {symbol} | {level_type} | {level_price}")
    send_telegram_message(message)

# ══════════════════════════════════════════════════════════
#  GMAIL IMAP MONITOR
# ══════════════════════════════════════════════════════════
def gmail_monitor():
    print("📧 Gmail monitor started...")
    send_telegram_message(
        "🤖 <b>SnR Alert Bot is LIVE!</b>\n"
        "📧 Monitoring TradingView email alerts every 30 seconds..."
    )

    while True:
        try:
            # Connect to Gmail
            mail = imaplib.IMAP4_SSL("imap.gmail.com")
            mail.login(GMAIL_USER, GMAIL_PASS)
            mail.select("inbox")

            # Search for unread emails with "SnR ALERT" in subject
            status, messages = mail.search(None, '(UNSEEN SUBJECT "SnR ALERT")')

            if status == "OK":
                email_ids = messages[0].split()

                if email_ids:
                    print(f"Found {len(email_ids)} new alert email(s)")

                for email_id in email_ids:
                    # Fetch email
                    status, msg_data = mail.fetch(email_id, "(RFC822)")

                    for response_part in msg_data:
                        if isinstance(response_part, tuple):
                            msg = email.message_from_bytes(response_part[1])

                            # Decode subject line
                            raw_subject         = msg["Subject"]
                            decoded, encoding   = decode_header(raw_subject)[0]
                            if isinstance(decoded, bytes):
                                subject = decoded.decode(encoding or "utf-8")
                            else:
                                subject = decoded

                            print(f"Email subject: {subject}")

                            # Parse subject into parts
                            symbol, level_type, level_price = parse_alert_email(subject)

                            if symbol:
                                print(f"✅ Parsed: {symbol} | {level_type} | {level_price}")
                                threading.Thread(
                                    target=send_alert,
                                    args=(symbol, level_type, level_price),
                                    daemon=True
                                ).start()
                            else:
                                print(f"⚠️ Could not parse: {subject}")

                    # Mark as READ — prevents processing same email twice
                    mail.store(email_id, "+FLAGS", "\\Seen")

            mail.logout()

        except Exception as e:
            print(f"Gmail monitor error: {e}")

        # Wait before checking again
        time.sleep(POLL_EVERY)

# ══════════════════════════════════════════════════════════
#  HEALTH ENDPOINTS
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
    monitor_thread = threading.Thread(target=gmail_monitor, daemon=True)
    monitor_thread.start()
    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting SnR Alert Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
```

5. Click **"Commit changes"** ✅

---

### File 2 — Update `requirements.txt`

1. Click on `requirements.txt` in your repo
2. Click **pencil icon** to edit
3. Delete everything and paste just these **2 lines:**
```
flask
requests
```
4. Click **"Commit changes"** ✅

---

### File 3 — Delete `build.sh`

1. Click on `build.sh` in your repo
2. Click the **three dots menu** (`...`) top right
3. Click **"Delete file"**
4. Click **"Commit changes"** ✅

---

### File 4 — Update Render Build Command

1. Go to Render dashboard → your `snr-alert-bot` service
2. Click **"Settings"** tab
3. Find **"Build Command"**
4. Change it from:
```
   chmod +x build.sh && ./build.sh
```
   To just:
```
   pip install -r requirements.txt
```
5. Click **"Save Changes"**
6. Also go to **Environment Variables** → remove `CHART_URL` and `YF_SYMBOL` — no longer needed
7. Click **"Manual Deploy"** → **"Deploy latest commit"**

---

### Verify Your Repo Now Has 3 Files Only:
```
snr-alert-bot/
├── README.md
├── server.py
└── requirements.txt
