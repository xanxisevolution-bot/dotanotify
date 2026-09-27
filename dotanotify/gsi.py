import http.server
import json
import re
import sys
import threading
from datetime import datetime
from pathlib import Path

from dotanotify.core import build_live_alert, load_heroes

if sys.platform == "win32":
    import winreg


def _players_by_id(watched):
    if isinstance(watched, dict) and "players" in watched:
        watched = watched["players"]
    if isinstance(watched, dict):
        return {str(key): value for key, value in watched.items()}
    return {
        str(player["account_id"]): player
        for player in watched or []
        if isinstance(player, dict) and "account_id" in player
    }


def find_watched_players(gsi_data, watched, heroes):
    watched_by_id = _players_by_id(watched)
    all_players = gsi_data.get("allplayers", {})
    if not all_players:
        return []
    my_team = gsi_data.get("player", {}).get("team_name", "")
    match_id = gsi_data.get("map", {}).get("matchid", 0)
    alerts = []
    for player_data in all_players.values():
        account_id = str(player_data.get("accountid", ""))
        if account_id not in watched_by_id:
            continue
        info = watched_by_id[account_id]
        name = player_data.get("name") or info.get("name") or f"Player#{account_id}"
        player_team = player_data.get("team_name", "")
        hero_id = str(player_data.get("heroid", 0))
        hero = heroes.get(hero_id, f"Hero#{hero_id}")
        if my_team and player_team:
            relation = (
                "เพื่อนร่วมทีม 🤝"
                if player_team == my_team
                else "ฝ่ายตรงข้าม ⚔️"
            )
        else:
            relation = "ไม่ทราบทีม"
        alerts.append(
            build_live_alert(match_id, name, info, hero, relation, datetime.now())
        )
    return alerts


def _steam_roots_from_registry():
    if sys.platform != "win32":
        return []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            steam_path, _ = winreg.QueryValueEx(key, "SteamPath")
        return [Path(steam_path)]
    except OSError:
        return []


def find_dota_cfg_dirs(steam_roots=None):
    roots = list(steam_roots) if steam_roots is not None else _steam_roots_from_registry()
    if sys.platform == "win32" and steam_roots is None:
        roots.append(Path(r"C:\Program Files (x86)\Steam"))

    cfg_dirs = []
    seen = set()
    for steam_root in roots:
        steam_root = Path(steam_root)
        libraries = [steam_root]
        library_file = steam_root / "steamapps" / "libraryfolders.vdf"
        try:
            contents = library_file.read_text(encoding="utf-8")
        except OSError:
            contents = ""
        for raw_path in re.findall(r'"path"\s*"([^"]+)"', contents):
            libraries.append(Path(raw_path.replace("\\\\", "\\")))

        for library in libraries:
            cfg_dir = (
                library
                / "steamapps"
                / "common"
                / "dota 2 beta"
                / "game"
                / "dota"
                / "cfg"
            )
            try:
                resolved = cfg_dir.resolve()
                if cfg_dir.is_dir() and resolved not in seen:
                    seen.add(resolved)
                    cfg_dirs.append(cfg_dir)
            except OSError:
                continue
    return cfg_dirs


def gsi_cfg_text(port, token):
    return (
        '"DotaNotify"\n'
        "{\n"
        f'    "uri"           "http://127.0.0.1:{port}/"\n'
        '    "timeout"       "5.0"\n'
        '    "buffer"        "0.1"\n'
        '    "throttle"      "0.5"\n'
        '    "heartbeat"     "30.0"\n'
        '    "data"\n'
        "    {\n"
        '        "provider"      "1"\n'
        '        "map"           "1"\n'
        '        "player"        "1"\n'
        '        "hero"          "1"\n'
        '        "allplayers"    "1"\n'
        "    }\n"
        '    "auth"\n'
        "    {\n"
        f'        "token"         "{token}"\n'
        "    }\n"
        "}\n"
    )


def install_gsi_cfg(cfg_dir, port, token):
    destination = Path(cfg_dir) / "gamestate_integration"
    destination.mkdir(parents=True, exist_ok=True)
    config_path = destination / "gamestate_integration_dotanotify.cfg"
    config_path.write_text(gsi_cfg_text(port, token), encoding="utf-8")
    return config_path


class GSIServer:
    def __init__(self, port, token, get_watchlist, on_alert, log):
        self.port = port
        self.token = token
        self.get_watchlist = get_watchlist
        self.on_alert = on_alert
        self.log = log
        self._server = None
        self._thread = None
        self._alerted_match_id = None
        self._missing_allplayers_logged = set()
        self._last_game_state = None
        self._lock = threading.Lock()

    @property
    def is_running(self):
        return self._thread is not None and self._thread.is_alive()

    def start(self):
        if self.is_running:
            return

        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    data = json.loads(self.rfile.read(length))
                    if data.get("auth", {}).get("token") != owner.token:
                        self.send_response(403)
                        self.end_headers()
                        return

                    self.send_response(200)
                    self.end_headers()
                    owner._process(data)
                except Exception as error:
                    owner.log(f"GSI error: {error}")
                    try:
                        self.send_response(500)
                        self.end_headers()
                    except OSError:
                        pass

            def log_message(self, format, *args):
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", self.port), Handler)
        self._server.daemon_threads = True
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="DotaNotifyGSI"
        )
        self._thread.start()
        self.log(f"Live GSI เปิดแล้วที่ http://127.0.0.1:{self.port}/")

    def stop(self):
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        self._server = None
        self._thread = None

    def _process(self, data):
        game_state = data.get("map", {}).get("game_state", "")
        match_id = data.get("map", {}).get("matchid", 0)
        if game_state != self._last_game_state:
            self.log(f"Game state: {game_state} | Match: {match_id}")
            self._last_game_state = game_state
        if game_state not in (
            "DOTA_GAMERULES_STATE_PRE_GAME",
            "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
        ):
            return
        if not match_id:
            return

        with self._lock:
            if match_id == self._alerted_match_id:
                return
            if not data.get("allplayers"):
                if match_id not in self._missing_allplayers_logged:
                    self._missing_allplayers_logged.add(match_id)
                    self.log(
                        "Dota ไม่ส่งข้อมูล allplayers (มีให้เฉพาะตอนดู/spectate) "
                        "— ใช้เช็คอัตโนมัติหลังจบแมทช์แทน"
                    )
                return
            alerts = find_watched_players(
                data, self.get_watchlist(), load_heroes()
            )
            self._alerted_match_id = match_id
            self._missing_allplayers_logged.discard(match_id)

        if alerts:
            for alert in alerts:
                self.on_alert(alert)
            self.log(f"Sent {len(alerts)} live alert(s) for match {match_id}")
        else:
            self.log(f"Match {match_id} — no watchlisted players found")
