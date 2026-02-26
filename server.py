from flask import Flask
import requests, threading, time, os, datetime
from datetime import timezone

app = Flask(__name__)

# ══════════════════════════════════════════════════════════
#  CREDENTIALS — set in Render Environment Variables
# ══════════════════════════════════════════════════════════
BOT_TOKEN      = os.environ.get("BOT_TOKEN",      "")
CHAT_ID        = os.environ.get("CHAT_ID",        "")
TWELVE_API_KEY = os.environ.get("TWELVE_API_KEY", "")
LEVEL_ZONE     = float(os.environ.get("LEVEL_ZONE",   "1.0"))  # $1.00 = 10 pips
CANDLE_COUNT   = int(os.environ.get("CANDLE_COUNT", "50"))     # last 50 x 1H candles

# ══════════════════════════════════════════════════════════
#  URLS
# ══════════════════════════════════════════════════════════
TWELVE_URL     = "https://api.twelvedata.com/time_series"
SWISSQUOTE_URL = "https://forex-data-feed.swissquote.com/public-quotes/bboquotes/instrument/XAU/USD"
METALS_URL     = "https://api.metals.live/v1/spot/gold"

# ══════════════════════════════════════════════════════════
#  LEVEL TYPE LABELS
# ══════════════════════════════════════════════════════════
LEVEL_EMOJI = {
    "A Level"     : "🔴 A Level     (Green -> Red)",
    "V Level"     : "🟢 V Level     (Red -> Green)",
    "Bullish Gap" : "🟢 Bullish Gap (Green -> Green)",
    "Bearish Gap" : "🔴 Bearish Gap (Red -> Red)"
}

# ══════════════════════════════════════════════════════════
#  LEVEL STORAGE
#  alerted = True means this level will NEVER alert again
#            (permanent — no reset even if price revisits)
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
#  MARKET HOURS CHECK
# ══════════════════════════════════════════════════════════
def is_market_open():
    now     = datetime.datetime.now(timezone.utc)
    weekday = now.weekday()  # Mon=0 ... Sat=5, Sun=6

    if weekday == 5:                     # Saturday — fully closed
        return False
    if weekday == 6 and now.hour < 17:   # Sunday before 17:00 UTC
        return False
    if weekday == 4 and now.hour >= 22:  # Friday after 22:00 UTC
        return False

    # Weekday off-hours: 2AM–8AM IST = 20:30–02:30 UTC
    utc_minutes = now.hour * 60 + now.minute
    off_start   = 20 * 60 + 30   # 20:30 UTC = 2:00 AM IST
    off_end     = 2  * 60 + 30   # 02:30 UTC = 8:00 AM IST

    if utc_minutes >= off_start or utc_minutes < off_end:
        return False

    return True

# ══════════════════════════════════════════════════════════
#  CANDLE HELPERS
# ══════════════════════════════════════════════════════════
def is_green(o, c): return c >= o
def is_red  (o, c): return c <  o

def detect_level_type(o1, c1, o2, c2):
    c1g = is_green(o1, c1)
    c1r = is_red  (o1, c1)
    c2g = is_green(o2, c2)
    c2r = is_red  (o2, c2)

    if c1g and c2r: return "A Level"
    if c1r and c2g: return "V Level"
    if c1g and c2g: return "Bullish Gap"
    if c1r and c2r: return "Bearish Gap"
    return None

def check_state_change(lvl_price, o, h, l, c, is_fresh):
    wick_touch = h >= lvl_price and l <= lvl_price
    body_break = min(o, c) < lvl_price and max(o, c) > lvl_price
    rejected   = wick_touch and not body_break

    if is_fresh and rejected:      return False  # Fresh → Unfresh
    if not is_fresh and body_break: return True  # Unfresh → Fresh
    return is_fresh

# ══════════════════════════════════════════════════════════
#  FETCH 1H CANDLES — Twelve Data
# ══════════════════════════════════════════════════════════
def fetch_candles():
    try:
        params = {
            "symbol"    : "XAU/USD",
            "interval"  : "1h",
            "outputsize": CANDLE_COUNT + 20,  # fetch extra to cover weekend gaps
            "apikey"    : TWELVE_API_KEY,
            "format"    : "JSON",
            "order"     : "ASC"
        }
        r    = requests.get(TWELVE_URL, params=params, timeout=15)
        data = r.json()

        if "values" not in data:
            print(f"Twelve Data error: {data.get('message', 'Unknown error')}")
            return None, None, None, None

        values = data["values"][:-1]  # exclude running candle

        # Filter out weekend/off-hours candles to match TradingView
        # TradingView only shows candles during market hours
        # XAUUSD market: Mon 00:00 – Fri 22:00 UTC, skip Sat/Sun
        filtered = []
        for v in values:
            # datetime format from Twelve Data: "2026-02-25 10:00:00"
            dt      = datetime.datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S")
            weekday = dt.weekday()  # Mon=0 ... Sat=5, Sun=6

            if weekday == 5:   # Saturday — skip
                continue
            if weekday == 6:   # Sunday before 17:00 UTC — skip
                if dt.hour < 17:
                    continue

            filtered.append(v)

        # Keep only last CANDLE_COUNT candles after filtering
        filtered = filtered[-CANDLE_COUNT:]

        opens  = [float(v["open"])  for v in filtered]
        highs  = [float(v["high"])  for v in filtered]
        lows   = [float(v["low"])   for v in filtered]
        closes = [float(v["close"]) for v in filtered]

        print(f"✅ Twelve Data: {len(closes)} candles fetched (filtered)")
        return opens, highs, lows, closes

    except Exception as e:
        print(f"Twelve Data fetch error: {e}")
        return None, None, None, None

# ══════════════════════════════════════════════════════════
#  THREAD 1 — LEVEL DETECTION
#  FIX 1: Runs every 5 minutes (was 15)
#  FIX 1: Posts update to group every time levels refresh
#  FIX 2: Preserves alerted=True permanently across refreshes
# ══════════════════════════════════════════════════════════
def level_detector():
    print("📊 Level detector started...")
    send_telegram(
        "🤖 <b>SnR Alert Bot is LIVE!</b>\n"
        "📊 Detecting XAUUSD 1H key levels every 5 minutes...\n"
        "⚡ Monitoring real-time price every 5 seconds..."
    )

    while True:
        try:
            print("🔄 Fetching 1H candles from Twelve Data...")

            opens, highs, lows, closes = fetch_candles()

            if opens is None:
                print("⚠️ No candle data, retrying in 5 mins...")
                time.sleep(300)
                continue

            new_levels = []

            for i in range(len(opens) - 1):
                o1, c1 = opens[i],     closes[i]
                o2, c2 = opens[i + 1], closes[i + 1]

                ltype = detect_level_type(o1, c1, o2, c2)
                if ltype is None:
                    continue

                lvl_price = round(c1, 2)

                # Replay subsequent candles → current Fresh/Unfresh state
                is_fresh = True
                for j in range(i + 1, len(opens)):
                    is_fresh = check_state_change(
                        lvl_price,
                        opens[j], highs[j], lows[j], closes[j],
                        is_fresh
                    )

                # FIX 2: Preserve alerted=True permanently
                # Once a level has been alerted, it stays alerted forever
                # even after a levels refresh — no second alert ever
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
                    "alerted": existing_alerted  # ← carries over permanently
                })

            with levels_lock:
                key_levels.clear()
                key_levels.extend(new_levels)
                new_count = len(key_levels)

            fresh_count   = sum(1 for l in new_levels if l["fresh"])
            unfresh_count = sum(1 for l in new_levels if not l["fresh"])

            print(f"✅ Levels updated: {new_count} total | {fresh_count} Fresh | {unfresh_count} Unfresh")

        except Exception as e:
            print(f"Level detector error: {e}")

        time.sleep(300)  # FIX 1: every 5 minutes

# ══════════════════════════════════════════════════════════
#  LIVE PRICE — Swissquote primary, metals.live fallback
# ══════════════════════════════════════════════════════════
def get_live_price():
    # Primary: Swissquote
    try:
        r    = requests.get(SWISSQUOTE_URL, timeout=5)
        data = r.json()
        if isinstance(data, list) and len(data) > 0:
            profiles = data[0].get("spreadProfilePrices", [])
            if profiles:
                bid = profiles[0].get("bid")
                ask = profiles[0].get("ask")
                if bid and ask:
                    return round((bid + ask) / 2, 2)
    except Exception as e:
        print(f"Swissquote error: {e}")

    # Fallback: metals.live
    try:
        r    = requests.get(METALS_URL, timeout=5)
        data = r.json()
        if isinstance(data, list) and len(data) > 0 and "gold" in data[0]:
            return round(float(data[0]["gold"]), 2)
    except Exception as e:
        print(f"metals.live error: {e}")

    return None

# ══════════════════════════════════════════════════════════
#  THREAD 2 — REAL TIME PRICE MONITOR (every 5 seconds)
#  FIX 3: Loop continues after match — catches ALL levels hit simultaneously
#  FIX 4: Message shows only key level price, no confusion
#  FIX 2: alerted flag NEVER resets — one alert per level, forever
# ══════════════════════════════════════════════════════════
def price_monitor():
    print("⚡ Real-time price monitor started...")

    while True:
        try:
            # Skip when market is closed
            if not is_market_open():
                print("💤 Market closed — skipping price check")
                time.sleep(60)
                continue

            current_price = get_live_price()

            if current_price is None:
                time.sleep(5)
                continue

            fresh_count = sum(1 for l in key_levels if l["fresh"])
            print(f"Price: {current_price} | Fresh levels: {fresh_count}")

            with levels_lock:
                levels_copy = list(key_levels)

            # FIX 3: Loop through ALL levels — don't break/return after first match
            # This ensures simultaneous alerts fire for multiple levels at once
            for lvl in levels_copy:

                if not lvl["fresh"]:
                    continue

                lvl_price = lvl["price"]
                ltype     = lvl["type"]
                distance  = abs(current_price - lvl_price)

                # FIX 2: Only alert if NOT already alerted — permanently
                if distance <= LEVEL_ZONE and not lvl["alerted"]:

                    print(f"🚨 ALERT: {current_price} near {lvl_price} ({ltype})")

                    # Mark permanently alerted — will NEVER reset
                    with levels_lock:
                        for stored in key_levels:
                            if abs(stored["price"] - lvl_price) < 0.01:
                                stored["alerted"] = True
                                break

                    level_label = LEVEL_EMOJI.get(ltype, ltype)

                    # FIX 4: Clean message — key level price is the focus
                    send_telegram(
                        f"🚨 <b>KEY LEVEL ALERT!</b>\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"Key Level : {lvl_price}\n"
                        f"Type      : {level_label}\n"
                        f"Time      : {time.strftime('%Y-%m-%d %H:%M:%S')} UTC\n"
                        f"━━━━━━━━━━━━━━━━━\n"
                        f"Open TradingView to review."
                    )

                # FIX 2: ← NO reset block here anymore
                # alerted stays True permanently — price can revisit 100 times,
                # no second alert will ever fire for this level

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
    t1 = threading.Thread(target=level_detector, daemon=True)
    t1.start()

    time.sleep(5)

    t2 = threading.Thread(target=price_monitor, daemon=True)
    t2.start()

    port = int(os.environ.get("PORT", 5000))
    print(f"🚀 Starting SnR Alert Bot on port {port}...")
    app.run(host="0.0.0.0", port=port, debug=False)
