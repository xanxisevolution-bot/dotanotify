import json
import os
import shutil
import sys
import threading
import time
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path

import requests


OPENDOTA = "https://api.opendota.com/api"
MATCH_LIMIT = 20


@dataclass
class Config:
    my_account_id: str = ""
    line_channel_token: str = ""
    line_user_id: str = ""
    check_interval_min: int = 5
    gsi_port: int = 3001
    gsi_token: str = "dota_watchlist_secret"
    auto_start: bool = False


def data_dir():
    override = os.environ.get("DOTANOTIFY_DATA_DIR")
    if override:
        path = Path(override).expanduser()
    elif sys.platform == "win32":
        path = Path(os.environ.get("APPDATA", Path.home())) / "DotaNotify"
    else:
        path = Path.home() / ".dotanotify"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _data_file(name):
    return data_dir() / name


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError, TypeError):
        return default


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2, ensure_ascii=False)


def load_config():
    values = load_json(_data_file("config.json"), {})
    if not isinstance(values, dict):
        values = {}
    known = {field.name for field in fields(Config)}
    return Config(**{key: value for key, value in values.items() if key in known})


def save_config(cfg):
    save_json(_data_file("config.json"), asdict(cfg))


def api_get(url, retries=3, delay=2):
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=15)
            if response.status_code == 200:
                return response.json()
            print(f"  [WARN] HTTP {response.status_code} for {url}")
        except Exception as error:
            print(f"  [ERROR] {error}")
        if attempt < retries - 1:
            time.sleep(delay)
    return None


def _watchlist_source():
    if getattr(sys, "frozen", False):
        return Path(os.path.dirname(sys.executable)) / "watchlist.json"
    return Path.cwd() / "watchlist.json"


def load_watchlist():
    path = _data_file("watchlist.json")
    if not path.exists():
        source = _watchlist_source()
        if source.is_file() and source.resolve() != path.resolve():
            try:
                shutil.copyfile(source, path)
            except OSError:
                pass
    watchlist = load_json(path, {"players": []})
    return watchlist if isinstance(watchlist, dict) else {"players": []}


def save_watchlist(watchlist):
    save_json(_data_file("watchlist.json"), watchlist)


def lookup_player(account_id):
    player = api_get(f"{OPENDOTA}/players/{account_id}")
    if not player:
        return None
    profile = player.get("profile") or {}
    return {
        "personaname": profile.get("personaname", ""),
        "avatar": profile.get("avatarfull") or profile.get("avatarmedium", ""),
    }


def load_heroes():
    path = _data_file("heroes_cache.json")
    cache = load_json(path, {})
    if cache:
        return cache
    data = api_get(f"{OPENDOTA}/heroes")
    if data:
        heroes = {str(hero["id"]): hero["localized_name"] for hero in data}
        save_json(path, heroes)
        return heroes
    return {}


def send_line(token, user_id, message):
    if not token or not user_id:
        return False, "กรุณาตั้งค่า LINE Channel Access Token และ User ID"
    try:
        response = requests.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"to": user_id, "messages": [{"type": "text", "text": message}]},
            timeout=10,
        )
        if response.status_code == 200:
            return True, "ส่งข้อความสำเร็จ"
        return False, f"LINE ตอบกลับ HTTP {response.status_code}: {response.text}"
    except Exception as error:
        return False, f"ส่ง LINE ไม่สำเร็จ: {error}"


def build_match_alert(match_id, player, watched_info, heroes, my_team, radiant_win, start_ts):
    slot = player.get("player_slot", 0)
    their_team = "radiant" if slot < 128 else "dire"
    relation = "teammate" if their_team == my_team else "enemy"
    relation_th = "เพื่อนร่วมทีม 🤝" if relation == "teammate" else "ฝ่ายตรงข้าม ⚔️"
    if radiant_win is not None:
        won = (their_team == "radiant") == radiant_win
        result = "ชนะ ✅" if won else "แพ้ ❌"
    else:
        result = "ไม่ทราบ"
    pid = str(player.get("account_id", ""))
    persona = player.get("personaname") or watched_info.get("name") or f"Player#{pid}"
    hero_id = player.get("hero_id", 0)
    hero = heroes.get(str(hero_id), f"Hero#{hero_id}")
    kills = player.get("kills", "?")
    deaths = player.get("deaths", "?")
    assists = player.get("assists", "?")
    note = watched_info.get("note", "-")
    tag = watched_info.get("tag", "")
    tag_text = f"🏷️ แท็ก: {tag}\n" if tag else ""
    match_dt = (
        datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%d/%m/%Y %H:%M")
        if start_ts
        else "?"
    )
    return (
        f"\n🎮 พบผู้เล่นใน Watchlist!\n"
        f"──────────────────\n"
        f"👤 {persona}\n"
        f"{tag_text}"
        f"📝 Note: {note}\n"
        f"──────────────────\n"
        f"🕹️ สถานะ: {relation_th}\n"
        f"🦸 Hero: {hero}  ({kills}/{deaths}/{assists})\n"
        f"🏆 ผล: {result}\n"
        f"🔗 Match ID: {match_id}\n"
        f"⏰ {match_dt} UTC\n"
        f"🌐 dotabuff.com/matches/{match_id}"
    )


def build_live_alert(match_id, player_name, watched_info, hero_name, relation, now=None):
    tag = watched_info.get("tag", "")
    tag_text = f"🏷️ แท็ก: {tag}\n" if tag else ""
    note = watched_info.get("note", "-")
    timestamp = (now or datetime.now()).strftime("%d/%m/%Y %H:%M")
    return (
        f"\n🎮 พบผู้เล่นใน Watchlist! (LIVE)\n"
        f"──────────────────\n"
        f"👤 {player_name}\n"
        f"{tag_text}"
        f"📝 Note: {note}\n"
        f"──────────────────\n"
        f"🕹️ สถานะ: {relation}\n"
        f"🦸 Hero: {hero_name}\n"
        f"🔗 Match ID: {match_id}\n"
        f"⏰ {timestamp}"
    )


def check_recent_matches(cfg, watchlist, state, heroes, log, sleep=None):
    sleep = sleep or time.sleep
    alerts = []
    new_state = dict(state)
    players = watchlist.get("players", [])
    watched = {str(player["account_id"]): player for player in players if "account_id" in player}
    if not watched:
        log("Watchlist ว่าง — ไม่มีผู้เล่นให้ตรวจสอบ")
        return alerts, new_state

    matches = api_get(
        f"{OPENDOTA}/players/{cfg.my_account_id}/matches?limit={MATCH_LIMIT}"
    )
    if not matches:
        log("ไม่สามารถดึงข้อมูลแมทช์ได้")
        return alerts, new_state

    last_id = state.get("last_match_id", 0)
    newest_id = max(match["match_id"] for match in matches)
    if last_id == 0:
        new_state["last_match_id"] = newest_id
        log(f"ตั้งค่าแมทช์ล่าสุดเป็นจุดเริ่มต้นแล้ว (Match ID: {newest_id})")
        return alerts, new_state

    new_matches = [match for match in matches if match["match_id"] > last_id]
    if not new_matches:
        log("ไม่พบแมทช์ใหม่")
        return alerts, new_state

    log(f"พบ {len(new_matches)} แมทช์ใหม่ กำลังตรวจสอบ")
    for match in sorted(new_matches, key=lambda item: item["match_id"]):
        match_id = match["match_id"]
        sleep(1)
        details = api_get(f"{OPENDOTA}/matches/{match_id}")
        if not details:
            log(f"ไม่สามารถดึงรายละเอียดแมทช์ {match_id} ได้")
            continue

        players = details.get("players", [])
        my_team = None
        for player in players:
            if str(player.get("account_id")) == str(cfg.my_account_id):
                my_team = "radiant" if player.get("player_slot", 0) < 128 else "dire"
                break
        for player in players:
            player_id = str(player.get("account_id", ""))
            if player_id not in watched:
                continue
            alerts.append(
                build_match_alert(
                    match_id=match_id,
                    player=player,
                    watched_info=watched[player_id],
                    heroes=heroes,
                    my_team=my_team,
                    radiant_win=details.get("radiant_win"),
                    start_ts=details.get("start_time", 0),
                )
            )
    new_state["last_match_id"] = max(match["match_id"] for match in new_matches)
    log(f"อัปเดต Match ID ล่าสุดเป็น {new_state['last_match_id']}")
    if not alerts:
        log("ไม่พบผู้เล่นใน Watchlist ในแมทช์ใหม่")
    return alerts, new_state


class Monitor:
    def __init__(self, cfg, get_watchlist, log):
        self.cfg = cfg
        self.get_watchlist = get_watchlist
        self.log = log
        self._stop_event = threading.Event()
        self._thread = None
        self._check_lock = threading.Lock()

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive() and not self._stop_event.is_set()

    def start(self):
        if self.is_running:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="DotaNotifyMonitor")
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def check_now(self):
        thread = threading.Thread(target=self._check_once, daemon=True, name="DotaNotifyCheck")
        thread.start()
        return thread

    def _check_once(self):
        try:
            with self._check_lock:
                state_path = _data_file("state.json")
                state = load_json(state_path, {"last_match_id": 0})
                alerts, new_state = check_recent_matches(
                    self.cfg, self.get_watchlist(), state, load_heroes(), self.log
                )
                for alert in alerts:
                    ok, info = send_line(
                        self.cfg.line_channel_token, self.cfg.line_user_id, alert
                    )
                    self.log(info if ok else f"LINE: {info}")
                save_json(state_path, new_state)
        except Exception as error:
            self.log(f"เกิดข้อผิดพลาดในการตรวจสอบ: {error}")

    def _run(self):
        while not self._stop_event.is_set():
            self._check_once()
            if self._stop_event.wait(max(1, self.cfg.check_interval_min) * 60):
                break
