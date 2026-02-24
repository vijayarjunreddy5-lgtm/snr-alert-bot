from flask import Flask
import requests, threading, time, os
import yfinance as yf

app = Flask(__name__)

# ══════════════════════════════════════════════════════════
#  CREDENTIALS — set in Render Environment Variables
# ══════════════════════════════════════════════════════════
BOT_TOKEN   = os.environ.get("BOT_TOKEN",   "")
CHAT_ID     = os.environ.get("CHAT_ID",     "")
LEVEL_ZONE  = float(os.environ.get("LEVEL_ZONE",  "1.0"))   # $1.00 = 10 pips
CANDLE_COUNT= int(os.environ.get("CANDLE_COUNT", "50"))     # last 50 x 1H candles

# ══════════════════════════════════════════════════════════
#  URLS
# ══════════════════════════════════════════════════════════
SWISSQUOTE_URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"

# ══════════════════════════════════════════════════════════
#  LEVEL STORAGE
#  Each level: {
#    "price"  : float,
#    "type"   : str,   # "A Level" | "V Level" | "Bullish Gap" | "Bearish Gap"
#    "fresh"  : bool,
#    "alerted": bool   # anti-spam flag
#  }
# ══════════════════════════════════════════════════════════
key_levels  = []
levels_lock = threading.Lock()

# ══════════════════════════════════════════════════════════
#  TELEGRAM
# ══════════════════════════════════════════════════════════
def send_telegram(message):
    try:
        url     = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id"   : CHAT_ID,
            "text"      : message,
            "parse_mode": "HTML"
        }
        r = requests.post(url, data=payload, timeout=10)
        print(f"Telegram: {r.status_code}")
    except Exception as e:
        print(f"Telegram error: {e}")

# ══════════════════════════════════════════════════════════
#  CANDLE HELPERS
# ══════════════════════════════════════════════════════════
def is_green(o, c): return c >= o
def is_red  (o, c): return c <  o

def detect_level_type(o1, c1, o2, c2):
    """
    Detect level type from 2 consecutive closed candles.
    Returns level type string or None if no pattern.
    Candle 1 = older, Candle 2 = newer
    """
    c1g = is_green(o1, c1)
    c1r = is_red  (o1, c1)
    c2g = is_green(o2, c2)
    c2r = is_red  (o2, c2)

    if c1g and c2r: return "A Level"      # Green → Red
    if c1r and c2g: return "V Level"      # Red → Green
    if c1g and c2g: return "Bullish Gap"  # Green → Green
    if c1r and c2r: return "Bearish Gap"  # Red → Red
    return None

# ══════════════════════════════════════════════════════════
#  FRESH / UNFRESH LOGIC
#  Given a candle's OHLC vs a level price:
#  Fresh → Unfresh : wick touched level, body closed away (rejection)
#  Unfresh → Fresh : body closed through level (breakout)
# ══════════════════════════════════════════════════════════
def check_state_change(lvl_price, o, h, l, c, is_fresh):
    wick_touch = h >= lvl_price and l <= lvl_price
    body_break = min(o, c) < lvl_price and max(o, c) > lvl_price
    rejected   = wick_touch and not body_break

    if is_fresh and rejected:
        return False   # Fresh → Unfresh
    if not is_fresh and body_break:
        return True    # Unfresh → Fresh
    return is_fresh    # no change

# ══════════════════════════════════════════════════════════
#  THREAD 1 — LEVEL DETECTION
#  Runs every 15 minutes
#  Fetches 1H candles via yfinance (GC=F)
#  Detects all 4 level types + updates Fresh/Unfresh state
# ══════════════════════════════════════════════════════════
def level_detector():
    print("📊 Level detector started...")
    send_telegram(
        "🤖 <b>SnR Alert Bot is LIVE!</b>\n"
        "📊 Detecting XAUUSD 1H key levels every 15 minutes...\n"
        "⚡ Monitoring real-time price every 5 seconds..."
    )

    while True:
        try:
            print("🔄 Fetching 1H candles from yfinance...")

            ticker = yf.Ticker("GC=F")
            df     = ticker.history(period="7d", interval="1h")

            if df.empty:
                print("⚠️ yfinance returned empty data, retrying in 15 mins...")
                time.sleep(900)
                continue

            # Keep only last N closed candles (exclude the running candle = last row)
            df = df.iloc[-(CANDLE_COUNT + 1):-1]

            opens  = df["Open"].tolist()
            highs  = df["High"].tolist()
            lows   = df["Low"].tolist()
            closes = df["Close"].tolist()

            new_levels = []

            # ── Detect levels from each pair of consecutive candles ────────────
            for i in range(len(opens) - 1):
                o1, c1 = opens[i],     closes[i]
                o2, c2 = opens[i + 1], closes[i + 1]
                h1, l1 = highs[i],     lows[i]

                ltype = detect_level_type(o1, c1, o2, c2)
                if ltype is None:
                    continue

                lvl_price = round(c1, 2)   # level = 1st candle close

                # ── Update Fresh/Unfresh state using all subsequent candles ────
                is_fresh = True
                for j in range(i + 1, len(opens)):
                    is_fresh = check_state_change(
                        lvl_price,
                        opens[j], highs[j], lows[j], closes[j],
                        is_fresh
                    )

                # ── Check if this level already exists in our store ────────────
                # If yes → preserve its alerted flag to avoid spam reset
                existing_alerted = False
                with levels_lock:
                    for existing in key_levels:
                        if abs(existing["price"] - lvl_price) < 0.01:
                            existing_alerted = existing["alerted"]
                            break

                new_levels.append({
                    "price"  : lvl_price,
                    "type"   : ltype,
                    "fresh"  : is_fresh,
                    "alerted": existing_alerted
                })

            # ── Replace level store with freshly detected levels ──────────────
            with levels_lock:
                old_count   = len(key_levels)
                key_levels.clear()
                key_levels.extend(new_levels)
                new_count   = len(key_levels)

            fresh_count   = sum(1 for l in new_levels if l["fresh"])
            unfresh_count = sum(1 for l in new_levels if not l["fresh"])

            print(f"✅ Levels updated: {new_count} total | {fresh_count} Fresh | {unfresh_count} Unfresh")

            # Notify on Telegram when levels are first loaded
            if old_count == 0 and new_count > 0:
                send_telegram(
                    f"📊 <b>Key Levels Loaded</b>\n"
                    f"━━━━━━━━━━━━━━━━━\n"
                    f"🟢 Fresh   : {fresh_count}\n"
                    f"🔴 Unfresh : {unfresh_count}\n"
                    f"📊 Total   : {new_count}\n"
                    f"━━━━━━━━━━━━━━━━━"
                )

        except Exception as e:
            print(f"Level detector error: {e}")

        # Wait 15 minutes before next scan
        time.sleep(900)

# ══════════════════════════════════════════════════════════
#  THREAD 2 — REAL TIME PRICE MONITOR
#  Runs every 5 seconds
#  Fetches live bid/ask from Swissquote
#  Compares mid price vs all Fresh levels
# ══════════════════════════════════════════════════════════
def get_live_price():
    """
    Fetch real-time XAUUSD price from Swissquote.
    Returns mid price (bid + ask) / 2 or None on failure.
    """
    try:
        r    = requests.get(SWISSQUOTE_URL, timeout=5)
        data = r.json()

        # Parse bid and ask from response
        # Swissquote format:
        # [{"spreadProfilePrices": [{"ask": 2650.50, "bid": 2650.00, ...}], ...}]
        bid = None
        ask = None

        if isinstance(data, list) and len(data) > 0:
            profiles = data[0].get("spreadProfilePrices", [])
            if profiles:
                bid = profiles[0].get("bid")
                ask = profiles[0].get("ask")

        if bid and ask:
            mid = round((bid + ask) / 2, 2)
            return mid

    except Exception as e:
        print(f"Swissquote error: {e}")

    return None

def price_monitor():
    print("⚡ Real-time price monitor started...")
    last_price = None

    while True:
        try:
            current_price = get_live_price()

            if current_price is None:
                time.sleep(5)
                continue

            print(f"💰 Price: {current_price} | Fresh levels: {sum(1 for l in key_levels if l['fresh'])}")

            if last_price is None:
                last_price = current_price
                time.sleep(5)
                continue

            # ── Compare price against every Fresh level ────────────────────────
            with levels_lock:
                levels_copy = list(key_levels)

            for idx, lvl in enumerate(levels_copy):

                # Fresh levels ONLY
                if not lvl["fresh"]:
                    continue

                lvl_price = lvl["price"]
                ltype     = lvl["type"]
                distance  = abs(current_price - lvl_price)

                # ── Price within proximity zone → fire alert ───────────────────
                if distance <= LEVEL_ZONE and not lvl["alerted"]:

                    print(f"🚨 ALERT: Price {current_price} near {lvl_price} ({ltype})")

                    # Mark as alerted immediately to prevent spam
                    with levels_lock:
                        for stored in key_levels:
                            if abs(stored["price"] - lvl_price) < 0.01:
                                stored["alerted"] = True
                                break

                    # Determine price approaching from above or below
                    direction = "approaching from above 📉" if current_price > lvl_price else "approaching from below 📈"

                    send_telegram(
                        f"🚨 <b>KEY LEVEL ALERT!</b>\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"📊 Symbol   : XAUUSD\n"
                        f"📍 Level    : {lvl_price} ({ltype})\n"
                        f"💰 Price    : {current_price}\n"
                        f"📏 Distance : ${distance:.2f}\n"
                        f"📌 Direction: {direction}\n"
                        f"⏰ Time     : {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"📝 Open TradingView to review."
                    )

                # ── Reset alerted flag once price moves away (3x zone) ─────────
                elif distance > LEVEL_ZONE * 3 and lvl["alerted"]:
                    with levels_lock:
                        for stored in key_levels:
                            if abs(stored["price"] - lvl_price) < 0.01:
                                stored["alerted"] = False
                                break
                    print(f"🔄 Level {lvl_price} reset — will alert again if price returns")

            last_price = current_price

        except Exception as e:
            print(f"Price monitor error: {e}")

        time.sleep(5)

# ══════════════════════════════════════════════════════════
#  HEALTH ENDPOINTS
# ══════════════════════════════════════════════════════════
@app.route("/", methods=["GET"])
def home():
    return {"status": "SnR Alert Bot is alive ✅"}, 200

@app.route("/health", methods=["GET"])
def health():
    return {"status": "running"}, 200

@app.route("/levels", methods=["GET"])
def show_levels():
    with levels_lock:
        fresh   = [l for l in key_levels if l["fresh"]]
        unfresh = [l for l in key_levels if not l["fresh"]]
    return {
        "total"  : len(key_levels),
        "fresh"  : len(fresh),
        "unfresh": len(unfresh),
        "levels" : key_levels
    }, 200

# ══════════════════════════════════════════════════════════
#  START
# ══════════════════════════════════════════════════════════
if __name__ == "__main__":
    # Thread 1 — level detection every 15 mins
    t1 = threading.Thread(target=level_detector, daemon=True)
    t1.start()

    # Small delay so levels load before price monitor starts
    time.sleep(5)

    # Thread 2 — real time price every 5 seconds
    t2 = threading.Thread(target=price_monitor, daemon=True)
    t2.start()

    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting SnR Alert Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
