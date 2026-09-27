import http.server
import json
import os
import threading
import time
import requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ─── CONFIG ────────────────────────────────────────────────────────────────────
LINE_CHANNEL_TOKEN = os.environ.get("LINE_CHANNEL_TOKEN", "")
LINE_USER_ID       = os.environ.get("LINE_USER_ID", "")
GSI_TOKEN          = "dota_watchlist_secret"   # ต้องตรงกับใน .cfg
PORT               = 3001

WATCHLIST_FILE = "watchlist.json"
HEROES_FILE    = "heroes_cache.json"
OPENDOTA       = "https://api.opendota.com/api"

# ─── STATE ─────────────────────────────────────────────────────────────────────
alerted_match_id  = None   # match_id ที่แจ้งเตือนไปแล้ว ไม่แจ้งซ้ำ
last_game_state   = None


# ─── HELPERS ───────────────────────────────────────────────────────────────────
def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def load_heroes():
    cache = load_json(HEROES_FILE, {})
    if cache:
        return cache
    try:
        r = requests.get(f"{OPENDOTA}/heroes", timeout=10)
        if r.status_code == 200:
            heroes = {str(h["id"]): h["localized_name"] for h in r.json()}
            with open(HEROES_FILE, "w", encoding="utf-8") as f:
                json.dump(heroes, f, indent=2)
            return heroes
    except Exception:
        pass
    return {}

def send_line(message):
    if not LINE_CHANNEL_TOKEN or not LINE_USER_ID:
        print(f"[LINE] (no credentials)\n{message}")
        return
    try:
        r = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {LINE_CHANNEL_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"to": LINE_USER_ID, "messages": [{"type": "text", "text": message}]},
            timeout=10
        )
        if r.status_code == 200:
            print("[LINE] Sent successfully")
        else:
            print(f"[LINE] Failed: {r.status_code} {r.text}")
    except Exception as e:
        print(f"[LINE] Error: {e}")


# ─── MATCH CHECK ───────────────────────────────────────────────────────────────
def check_players(gsi_data):
    global alerted_match_id

    game_state = gsi_data.get("map", {}).get("game_state", "")
    match_id   = gsi_data.get("map", {}).get("matchid", 0)

    # เช็คเฉพาะตอนแมทช์กำลังเล่น และยังไม่เคยแจ้งเตือน match นี้
    if game_state not in ("DOTA_GAMERULES_STATE_PRE_GAME",
                          "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS"):
        return
    if not match_id or match_id == alerted_match_id:
        return

    all_players = gsi_data.get("allplayers", {})
    if not all_players:
        return

    watchlist = load_json(WATCHLIST_FILE, {"players": []})
    watched   = {str(p["account_id"]): p for p in watchlist.get("players", [])}
    if not watched:
        return

    heroes = load_heroes()

    # หา team ของเรา จาก player block
    my_team = gsi_data.get("player", {}).get("team_name", "")  # "radiant" / "dire"

    alerts = []
    for slot, pdata in all_players.items():
        account_id = str(pdata.get("accountid", ""))
        if account_id not in watched:
            continue

        info     = watched[account_id]
        name     = pdata.get("name") or info.get("name") or f"Player#{account_id}"
        p_team   = pdata.get("team_name", "")
        hero_id  = str(pdata.get("heroid", 0))
        h_name   = heroes.get(hero_id, f"Hero#{hero_id}")

        if my_team and p_team:
            relation = "เพื่อนร่วมทีม 🤝" if p_team == my_team else "ฝ่ายตรงข้าม ⚔️"
        else:
            relation = "ไม่ทราบทีม"

        note = info.get("note", "-")
        tag  = info.get("tag", "")
        tag_text = f"🏷️ แท็ก: {tag}\n" if tag else ""

        msg = (
            f"\n🎮 พบผู้เล่นใน Watchlist! (LIVE)\n"
            f"──────────────────\n"
            f"👤 {name}\n"
            f"{tag_text}"
            f"📝 Note: {note}\n"
            f"──────────────────\n"
            f"🕹️ สถานะ: {relation}\n"
            f"🦸 Hero: {h_name}\n"
            f"🔗 Match ID: {match_id}\n"
            f"⏰ {datetime.now().strftime('%d/%m/%Y %H:%M')}"
        )
        alerts.append(msg)
        print(f"  ✅ ALERT: {name} ({account_id}) — {relation} — {h_name}")

    if alerts:
        alerted_match_id = match_id
        for alert in alerts:
            send_line(alert)
        print(f"Sent {len(alerts)} alert(s) for match {match_id}")
    else:
        alerted_match_id = match_id   # mark ว่าเช็คแล้ว ไม่เจอ
        print(f"Match {match_id} — no watchlisted players found")


# ─── HTTP SERVER ───────────────────────────────────────────────────────────────
class GSIHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        global last_game_state
        try:
            length = int(self.headers.get("Content-Length", 0))
            body   = self.rfile.read(length)
            data   = json.loads(body)

            # ตรวจสอบ auth token
            if data.get("auth", {}).get("token") != GSI_TOKEN:
                self.send_response(403)
                self.end_headers()
                return

            self.send_response(200)
            self.end_headers()

            game_state = data.get("map", {}).get("game_state", "")
            match_id   = data.get("map", {}).get("matchid", 0)

            # แสดง state เฉพาะตอนเปลี่ยน
            if game_state != last_game_state:
                print(f"[{datetime.now().strftime('%H:%M:%S')}] Game state: {game_state} | Match: {match_id}")
                last_game_state = game_state

            # รัน check ใน thread แยกเพื่อไม่บล็อค server
            threading.Thread(target=check_players, args=(data,), daemon=True).start()

        except Exception as e:
            print(f"[ERROR] {e}")
            self.send_response(500)
            self.end_headers()

    def log_message(self, format, *args):
        pass   # ปิด access log ที่ verbose เกินไป


# ─── MAIN ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"🛡️  Dota Watchlist — Live GSI Server")
    print(f"   Listening on http://localhost:{PORT}")
    print(f"   Watchlist: {WATCHLIST_FILE}")
    print(f"   กด Ctrl+C เพื่อหยุด\n")

    watchlist = load_json(WATCHLIST_FILE, {"players": []})
    print(f"   Watching {len(watchlist.get('players', []))} player(s)\n")

    server = http.server.HTTPServer(("localhost", PORT), GSIHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")
