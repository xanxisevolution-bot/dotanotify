import re
import threading
import time
from datetime import datetime
from pathlib import Path

import requests

from dotanotify import core
from dotanotify.gsi import _players_by_id, find_dota_cfg_dirs


STEAM_API = "https://api.steampowered.com"
_SERVER_ID_PATTERN = re.compile(
    r"steamid:(?P<steam_id>\d{17})"
    r"|\[A:1:(?P<account>\d+):(?P<instance>\d+)\]"
)


def gameserver_steam_id(account, instance):
    return (1 << 56) | (4 << 52) | (int(instance) << 32) | int(account)


def find_server_ids(text):
    server_ids = []
    for match in _SERVER_ID_PATTERN.finditer(text):
        steam_id = match.group("steam_id")
        if steam_id is None:
            steam_id = str(
                gameserver_steam_id(match.group("account"), match.group("instance"))
            )
        if (int(steam_id) >> 52) & 0xF != 4:
            continue
        if not server_ids or server_ids[-1] != steam_id:
            server_ids.append(steam_id)
    return server_ids


def console_log_paths(cfg_dirs):
    return [Path(cfg_dir).parent / "console.log" for cfg_dir in cfg_dirs]


def fetch_realtime_stats(api_key, server_steam_id):
    try:
        response = requests.get(
            f"{STEAM_API}/IDOTA2MatchStats_570/GetRealtimeStats/v1/",
            params={"key": api_key, "server_steam_id": server_steam_id},
            timeout=10,
        )
        if response.status_code != 200:
            return None
        stats = response.json()
    except (requests.RequestException, ValueError, TypeError):
        return None
    if not isinstance(stats, dict) or "teams" not in stats:
        return None
    return stats


def check_steam_key(api_key):
    try:
        response = requests.get(
            f"{STEAM_API}/IDOTA2Match_570/GetTopLiveGame/v1/",
            params={"key": api_key, "partner": 0},
            timeout=10,
        )
        if response.status_code == 200:
            return True, "Steam API Key ใช้งานได้"
        return False, f"Steam API Key ใช้งานไม่ได้ (HTTP {response.status_code})"
    except Exception as error:
        return False, f"ตรวจสอบ Steam API Key ไม่สำเร็จ: {error}"


def find_watched_in_realtime(
    stats, watched, my_account_id, heroes, now=None
) -> list[tuple[str, str]]:
    watched_by_id = _players_by_id(watched)
    own = set(core.account_ids(my_account_id))
    teams = stats.get("teams", []) if isinstance(stats, dict) else []
    players = []
    my_team = None
    for team_data in teams if isinstance(teams, list) else []:
        if not isinstance(team_data, dict):
            continue
        for player in team_data.get("players", []) or []:
            if not isinstance(player, dict):
                continue
            team_number = player.get("team", team_data.get("team_number"))
            players.append((player, team_number))
    for player, team_number in players:
        if str(player.get("accountid", "")) in own:
            my_team = team_number
            break

    alerts = []
    for player, team_number in players:
        account_id = str(player.get("accountid", ""))
        if not account_id or account_id == "0" or account_id in own:
            continue
        watched_info = watched_by_id.get(account_id)
        if watched_info is None:
            continue
        if not isinstance(watched_info, dict):
            watched_info = {}
        if my_team is None or team_number is None:
            relation = "ไม่ทราบทีม"
        elif str(team_number) == str(my_team):
            relation = "เพื่อนร่วมทีม 🤝"
        else:
            relation = "ฝ่ายตรงข้าม ⚔️"

        hero_id = player.get("heroid", 0) or 0
        if str(hero_id) == "0":
            hero_name = "ยังไม่เลือกฮีโร่"
        else:
            hero_name = heroes.get(str(hero_id), f"Hero#{hero_id}")
        player_name = (
            player.get("name")
            or watched_info.get("name")
            or f"Player#{account_id}"
        )
        match_id = (stats.get("match") or {}).get("match_id", 0)
        alerts.append(
            (
                account_id,
                core.build_live_alert(
                    match_id,
                    player_name,
                    watched_info,
                    hero_name,
                    relation,
                    now or datetime.now(),
                ),
            )
        )
    return alerts


class InstantMonitor:
    def __init__(
        self,
        cfg,
        get_watchlist,
        log,
        on_alert,
        find_logs=None,
        fetch=fetch_realtime_stats,
        clock=time.monotonic,
        on_found=None,
    ):
        self.cfg = cfg
        self.get_watchlist = get_watchlist
        self.log = log
        self.on_alert = on_alert
        self.find_logs = (
            find_logs
            if find_logs is not None
            else lambda: console_log_paths(find_dota_cfg_dirs())
        )
        self.fetch = fetch
        self.clock = clock
        self.on_found = on_found
        self._stop_event = threading.Event()
        self._thread = None
        self._poll_lock = threading.Lock()
        self._missing_log_logged = False
        self._missing_key_logged = False
        self._log_path = None
        self._file_identity = None
        self._position = None
        self._line_buffer = ""
        self._server_id = None
        self._handled = False
        self._attempts = 0
        self._started_at = None
        self._last_fetch = None
        self._delivered_ids = set()
        self._found_notified = False

    @property
    def is_running(self):
        return (
            self._thread is not None
            and self._thread.is_alive()
            and not self._stop_event.is_set()
        )

    def start(self):
        if self.is_running:
            return
        stop_event = threading.Event()
        self._stop_event = stop_event
        self._thread = threading.Thread(
            target=self._run,
            args=(stop_event,),
            daemon=True,
            name="DotaNotifyInstantMonitor",
        )
        self._thread.start()

    def stop(self):
        self._stop_event.set()

    def poll_once(self):
        with self._poll_lock:
            if not self.cfg.steam_api_key:
                if not self._missing_key_logged:
                    self._missing_key_logged = True
                    self.log("ยังไม่ได้ตั้งค่า Steam Web API Key — ปิดแจ้งเตือนทันที")
                return

            path = self._first_existing_log()
            if path is None:
                if not self._missing_log_logged:
                    self._missing_log_logged = True
                    self.log(
                        "ไม่พบ console.log — ใส่ -condebug ใน Launch Options "
                        "ของ Dota 2 แล้วเปิดเกมใหม่"
                    )
            else:
                new_text = self._read_new_text(path)
                server_ids = find_server_ids(new_text)
                if server_ids:
                    server_id = server_ids[-1]
                    if server_id != self._server_id:
                        self._server_id = server_id
                        self._handled = False
                        self._attempts = 0
                        self._started_at = self.clock()
                        self._last_fetch = None
                        self._delivered_ids = set()
                        self._found_notified = False
                        self.log(
                            f"เจอเซิร์ฟเวอร์แมทช์ใหม่ ({server_id}) "
                            "กำลังดึงรายชื่อผู้เล่น"
                        )

            self._poll_current_server()

    def _first_existing_log(self):
        paths = self.find_logs() if callable(self.find_logs) else self.find_logs
        if paths is None:
            return None
        if isinstance(paths, (str, Path)):
            paths = [paths]
        for path in paths:
            candidate = Path(path)
            if candidate.is_file():
                return candidate
        return None

    def _read_new_text(self, path):
        try:
            stat = path.stat()
        except OSError:
            return ""
        identity = (stat.st_dev, stat.st_ino)
        if self._log_path is None:
            self._log_path = path
            self._file_identity = identity
            self._position = stat.st_size
            self._line_buffer = ""
            return ""
        if path != self._log_path:
            self._log_path = path
            self._file_identity = identity
            self._position = stat.st_size
            self._line_buffer = ""
            return ""
        if identity != self._file_identity:
            self._file_identity = identity
            self._position = 0
            self._line_buffer = ""
        elif stat.st_size < self._position:
            self._position = 0
            self._line_buffer = ""

        try:
            with path.open("rb") as log_file:
                log_file.seek(self._position)
                new_bytes = log_file.read()
                self._position = log_file.tell()
        except OSError:
            return ""
        text = self._line_buffer + new_bytes.decode("utf-8", errors="replace")
        last_newline = max(text.rfind("\n"), text.rfind("\r"))
        if last_newline == -1:
            self._line_buffer = text
            return ""
        complete_text = text[: last_newline + 1]
        self._line_buffer = text[last_newline + 1 :]
        return complete_text

    def _poll_current_server(self):
        if self._server_id is None or self._handled:
            return
        now = self.clock()
        if self._last_fetch is not None and now - self._last_fetch < 10:
            self._expire_if_needed(now)
            return
        self._last_fetch = now
        try:
            stats = self.fetch(self.cfg.steam_api_key, self._server_id)
        except Exception as error:
            self.log(f"ดึงข้อมูลผู้เล่นจาก Steam ไม่สำเร็จ: {error}")
            stats = None

        if not self._has_players(stats):
            self._expire_if_needed(now)
            return

        alerts = find_watched_in_realtime(
            stats,
            self.get_watchlist(),
            self.cfg.my_account_id,
            core.load_heroes(),
        )
        if not alerts:
            self._handled = True
            self.log("ไม่พบผู้เล่นใน Watchlist ในแมทช์นี้")
            return

        if not self._found_notified:
            self._found_notified = True
            if self.on_found:
                try:
                    self.on_found([alert for _account_id, alert in alerts])
                except Exception as error:
                    self.log(f"แสดงการแจ้งเตือนผู้เล่นที่พบไม่สำเร็จ: {error}")

        delivery_failed = False
        for account_id, alert in alerts:
            if account_id in self._delivered_ids:
                continue
            try:
                delivered = self.on_alert(alert)
            except Exception as error:
                self.log(f"ส่งการแจ้งเตือนทันทีไม่สำเร็จ: {error}")
                delivered = False
            if delivered:
                self._delivered_ids.add(account_id)
            else:
                delivery_failed = True

        if delivery_failed:
            self._attempts += 1
            if self._attempts >= 3:
                self._handled = True
                self.log(
                    f"ส่งการแจ้งเตือนแมทช์ {self._server_id} ไม่สำเร็จ 3 ครั้ง "
                    "ปิดการลองซ้ำ"
                )
            else:
                self.log(
                    f"ส่งการแจ้งเตือนทันทีไม่สำเร็จ ({self._attempts}/3) "
                    "จะลองอีกครั้ง"
                )
            return

        self._handled = True
        self.log(f"ส่งการแจ้งเตือนทันที {len(alerts)} รายการสำเร็จ")

    def _has_players(self, stats):
        if not isinstance(stats, dict):
            return False
        teams = stats.get("teams")
        if not isinstance(teams, list):
            return False
        return any(
            str(player.get("accountid", "")) not in ("", "0")
            for team in teams
            if isinstance(team, dict)
            for player in team.get("players", []) or []
            if isinstance(player, dict)
        )

    def _expire_if_needed(self, now):
        if (
            self._started_at is not None
            and now - self._started_at >= 600
            and not self._handled
        ):
            self._handled = True
            self.log("ไม่ได้ข้อมูลผู้เล่นจาก Steam (หมดเวลา)")

    def _run(self, stop_event):
        while not stop_event.is_set():
            try:
                self.poll_once()
            except Exception as error:
                self.log(f"เกิดข้อผิดพลาดในการแจ้งเตือนทันที: {error}")
            if stop_event.wait(2):
                break
