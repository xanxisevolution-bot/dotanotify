import requests
import json
import os
import time
from datetime import datetime, timezone

# ─── CONFIG ────────────────────────────────────────────────────────────────────
MY_ACCOUNT_ID        = os.environ.get("MY_DOTA_ID", "")        # Steam32 Account ID
LINE_CHANNEL_TOKEN   = os.environ.get("LINE_CHANNEL_TOKEN", "") # Line Messaging API Channel Access Token
LINE_USER_ID         = os.environ.get("LINE_USER_ID", "")       # Line User ID (Uxxxxxxxx...)

WATCHLIST_FILE = "watchlist.json"
STATE_FILE     = "state.json"
HEROES_FILE    = "heroes_cache.json"

OPENDOTA = "https://api.opendota.com/api"
MATCH_LIMIT = 20   # จำนวน match ล่าสุดที่ดึงมาตรวจสอบ


# ─── HELPERS ───────────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

def api_get(url, retries=3, delay=2):
    for i in range(retries):
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                return r.json()
            print(f"  [WARN] HTTP {r.status_code} for {url}")
        except Exception as e:
            print(f"  [ERROR] {e}")
        if i < retries - 1:
            time.sleep(delay)
    return None


# ─── HERO NAME CACHE ───────────────────────────────────────────────────────────
def load_heroes():
    cache = load_json(HEROES_FILE, {})
    if cache:
        return cache
    print("Fetching hero names...")
    data = api_get(f"{OPENDOTA}/heroes")
    if data:
        heroes = {str(h["id"]): h["localized_name"] for h in data}
        save_json(HEROES_FILE, heroes)
        return heroes
    return {}

def hero_name(heroes, hero_id):
    return heroes.get(str(hero_id), f"Hero#{hero_id}")


# ─── LINE MESSAGING API ────────────────────────────────────────────────────────
def send_line(message):
    if not LINE_CHANNEL_TOKEN or not LINE_USER_ID:
        print(f"[LINE] (no credentials) {message}")
        return
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
                "Content-Type": "application/json",
            },
            json={
                "to": LINE_USER_ID,
                "messages": [{"type": "text", "text": message}]
            },
            timeout=10
        )
        if r.status_code == 200:
            print("[LINE] Sent successfully")
        else:
            print(f"[LINE] Failed: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[LINE] Error: {e}")


# ─── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    if not MY_ACCOUNT_ID:
        print("[ERROR] MY_DOTA_ID is not set")
        return

    watchlist = load_json(WATCHLIST_FILE, {"players": []})
    state     = load_json(STATE_FILE, {"last_match_id": 0})
    heroes    = load_heroes()

    # Build lookup: account_id (str) -> player info
    watched = {str(p["account_id"]): p for p in watchlist.get("players", [])}

    if not watched:
        print("Watchlist is empty — nothing to check")
        return

    print(f"Watchlist: {len(watched)} players")
    print(f"Last checked match ID: {state['last_match_id']}")

    # ── Fetch recent matches ──────────────────────────────────────────────────
    matches = api_get(f"{OPENDOTA}/players/{MY_ACCOUNT_ID}/matches?limit={MATCH_LIMIT}")
    if not matches:
        print("[ERROR] Could not fetch matches")
        return

    last_id = state.get("last_match_id", 0)
    new_matches = [m for m in matches if m["match_id"] > last_id]

    if not new_matches:
        print("No new matches found")
        return

    print(f"Found {len(new_matches)} new match(es) to check")

    alerts = []

    for m in sorted(new_matches, key=lambda x: x["match_id"]):
        match_id = m["match_id"]
        print(f"\nChecking match {match_id}...")
        time.sleep(1)  # rate limit courtesy

        details = api_get(f"{OPENDOTA}/matches/{match_id}")
        if not details:
            print(f"  Could not fetch details for {match_id}")
            continue

        players = details.get("players", [])
        start_ts = details.get("start_time", 0)
        match_dt = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%d/%m/%Y %H:%M") if start_ts else "?"

        # Find my team
        my_team = None
        for p in players:
            if str(p.get("account_id")) == str(MY_ACCOUNT_ID):
                slot = p.get("player_slot", 0)
                my_team = "radiant" if slot < 128 else "dire"
                break

        for p in players:
            pid = str(p.get("account_id", ""))
            if pid not in watched:
                continue

            watched_info = watched[pid]
            slot = p.get("player_slot", 0)
            their_team = "radiant" if slot < 128 else "dire"
            relation = "teammate" if their_team == my_team else "enemy"
            relation_th = "เพื่อนร่วมทีม 🤝" if relation == "teammate" else "ฝ่ายตรงข้าม ⚔️"

            # win/loss
            radiant_win = details.get("radiant_win", None)
            if radiant_win is not None:
                won = (their_team == "radiant") == radiant_win
                result = "ชนะ ✅" if won else "แพ้ ❌"
            else:
                result = "ไม่ทราบ"

            persona  = p.get("personaname") or watched_info.get("name") or f"Player#{pid}"
            h_name   = hero_name(heroes, p.get("hero_id", 0))
            kills    = p.get("kills", "?")
            deaths   = p.get("deaths", "?")
            assists  = p.get("assists", "?")
            note     = watched_info.get("note", "-")
            tag      = watched_info.get("tag", "")
            tag_text = f"🏷️ แท็ก: {tag}\n" if tag else ""

            msg = (
                f"\n🎮 พบผู้เล่นใน Watchlist!\n"
                f"──────────────────\n"
                f"👤 {persona}\n"
                f"{tag_text}"
                f"📝 Note: {note}\n"
                f"──────────────────\n"
                f"🕹️ สถานะ: {relation_th}\n"
                f"🦸 Hero: {h_name}  ({kills}/{deaths}/{assists})\n"
                f"🏆 ผล: {result}\n"
                f"🔗 Match ID: {match_id}\n"
                f"⏰ {match_dt} UTC\n"
                f"🌐 dotabuff.com/matches/{match_id}"
            )
            alerts.append(msg)
            print(f"  ✅ ALERT: {persona} ({pid}) — {relation} — {h_name}")

    if alerts:
        for alert in alerts:
            send_line(alert)
    else:
        print("\nNo watchlisted players found in new matches")

    # ── Save state ────────────────────────────────────────────────────────────
    if new_matches:
        new_last = max(m["match_id"] for m in new_matches)
        state["last_match_id"] = new_last
        save_json(STATE_FILE, state)
        print(f"\nState updated: last_match_id = {new_last}")


if __name__ == "__main__":
    main()
