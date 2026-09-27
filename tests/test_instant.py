from datetime import datetime
from types import SimpleNamespace

from dotanotify import instant


def test_gameserver_steam_id_conversion():
    assert instant.gameserver_steam_id(1589675028, 51577) == 90293515665310740


def test_find_server_ids_parses_console_formats_and_filters_noise():
    first = "90135088007115782"
    second = str(instant.gameserver_steam_id(2390127622, 14690))
    third = str(instant.gameserver_steam_id(2910480391, 4323))
    invalid = str((1 << 56) | (3 << 52) | 123456)
    text = (
        f"[SteamNetSockets] SDR server steamid:{first}(vport 0)\n"
        f"steamid:{first}\n"
        f"connecting to MatchID 5402426034 at ServerID [A:1:2390127622:14690]\n"
        "[U:1:123] is not a game server\n"
        f"steamid:{invalid}\n"
        f"Game Server SteamID [A:1:2910480391:4323] ({invalid})\n"
    )

    assert second == first
    assert instant.find_server_ids(text) == [first, third]
    assert instant.find_server_ids(f"steamid:{first}\nsteamid:{third}\nsteamid:{first}") == [
        first,
        third,
        first,
    ]


def test_console_log_paths_points_to_dota_directory():
    cfg_dir = "/steam/steamapps/common/dota 2 beta/game/dota/cfg"

    assert instant.console_log_paths([cfg_dir]) == [
        instant.Path(cfg_dir).parent / "console.log"
    ]


def test_fetch_realtime_stats_handles_status_json_and_shape(monkeypatch):
    class Response:
        status_code = 200

        def json(self):
            return {"match": {"match_id": 1}, "teams": []}

    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(instant.requests, "get", get)
    stats = instant.fetch_realtime_stats("api-key", "server-id")

    assert stats == {"match": {"match_id": 1}, "teams": []}
    assert calls[0][1] == {
        "params": {"key": "api-key", "server_steam_id": "server-id"},
        "timeout": 10,
    }

    class BadResponse:
        status_code = 400

        def json(self):
            raise AssertionError("non-200 responses should not be parsed")

    monkeypatch.setattr(instant.requests, "get", lambda *_args, **_kwargs: BadResponse())
    assert instant.fetch_realtime_stats("api-key", "bad-id") is None

    monkeypatch.setattr(instant.requests, "get", lambda *_args, **_kwargs: ResponseWithoutTeams())
    assert instant.fetch_realtime_stats("api-key", "server-id") is None


class ResponseWithoutTeams:
    status_code = 200

    def json(self):
        return {"match": {"match_id": 1}}


def test_check_steam_key_reports_success_and_http_status(monkeypatch):
    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    responses = iter([Response(200), Response(403)])
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return next(responses)

    monkeypatch.setattr(instant.requests, "get", get)

    assert instant.check_steam_key("api-key") == (True, "Steam API Key ใช้งานได้")
    ok, message = instant.check_steam_key("bad-key")
    assert not ok
    assert "403" in message
    assert calls[0][1] == {"params": {"key": "api-key", "partner": 0}, "timeout": 10}

    def fail_request(*_args, **_kwargs):
        raise RuntimeError("offline")

    monkeypatch.setattr(instant.requests, "get", fail_request)
    ok, message = instant.check_steam_key("api-key")
    assert not ok
    assert "offline" in message


def test_find_watched_in_realtime_formats_relations_and_skips_self():
    stats = {
        "match": {"match_id": 789},
        "teams": [
            {
                "team_number": 2,
                "players": [
                    {"accountid": 100, "name": "Me", "team": 2, "heroid": 1},
                    {"accountid": 200, "name": "Teammate", "team": 2, "heroid": 0},
                ],
            },
            {
                "team_number": 3,
                "players": [
                    {"accountid": 300, "name": "Enemy", "team": 3, "heroid": 5}
                ],
            },
        ],
    }
    now = datetime(2026, 1, 2, 3, 4)
    alerts = instant.find_watched_in_realtime(
        stats,
        {
            "players": [
                {"account_id": 100, "name": "Self"},
                {"account_id": 200, "name": "Watch Friend"},
                {"account_id": 300, "name": "Watch Enemy"},
            ]
        },
        "100",
        {"5": "Pudge"},
        now,
    )

    assert [account_id for account_id, _alert in alerts] == ["200", "300"]
    assert "Teammate" in alerts[0][1]
    assert "เพื่อนร่วมทีม 🤝" in alerts[0][1]
    assert "ยังไม่เลือกฮีโร่" in alerts[0][1]
    assert "ฝ่ายตรงข้าม ⚔️" in alerts[1][1]
    assert "Pudge" in alerts[1][1]
    assert "789" in alerts[0][1]
    assert "Self" not in "\n".join(alert for _account_id, alert in alerts)


def _stats_with_player(account_id=200):
    return {
        "match": {"match_id": 1234},
        "teams": [
            {
                "team_number": 2,
                "players": [
                    {"accountid": account_id, "name": "Watched", "team": 2, "heroid": 0}
                ],
            }
        ],
    }


def _make_monitor(
    path, fetch, clock, on_alert=None, logs=None, on_found=None, watchlist=None
):
    log_messages = logs if logs is not None else []
    return instant.InstantMonitor(
        SimpleNamespace(steam_api_key="test-key", my_account_id="100"),
        lambda: watchlist
        if watchlist is not None
        else {"players": [{"account_id": 200, "name": "Watched"}]},
        log_messages.append,
        on_alert or (lambda _alert: True),
        find_logs=[path],
        fetch=fetch,
        clock=clock,
        on_found=on_found,
    )


def test_poll_ignores_existing_console_history_and_delivers_new_match_once(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(instant.core, "load_heroes", lambda: {})
    path = tmp_path / "console.log"
    old_server = instant.gameserver_steam_id(1, 100)
    new_server = instant.gameserver_steam_id(2, 200)
    path.write_text(f"old steamid:{old_server}\n", encoding="utf-8")
    fetch_calls = []
    alerts = []
    monitor = _make_monitor(
        path,
        lambda key, server_id: (fetch_calls.append((key, server_id)), _stats_with_player())[1],
        lambda: 0,
        lambda alert: (alerts.append(alert), True)[1],
    )

    monitor.poll_once()
    assert fetch_calls == []

    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(f"new match steamid:{new_server}\n")
    monitor.poll_once()
    monitor.poll_once()

    assert fetch_calls == [("test-key", str(new_server))]
    assert len(alerts) == 1


def test_poll_waits_ten_seconds_between_empty_stats_fetches(tmp_path):
    path = tmp_path / "console.log"
    path.write_text("", encoding="utf-8")
    current_time = [0]
    fetch_calls = []
    monitor = _make_monitor(
        path,
        lambda _key, server_id: (fetch_calls.append(server_id), {"teams": []})[1],
        lambda: current_time[0],
    )
    monitor.poll_once()
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(
            f"server steamid:{instant.gameserver_steam_id(3, 300)}\n"
        )
    monitor.poll_once()
    current_time[0] = 9
    monitor.poll_once()
    assert len(fetch_calls) == 1

    current_time[0] = 10
    monitor.poll_once()
    assert len(fetch_calls) == 2


def test_poll_retries_failed_alerts_without_resending_delivered_alerts(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(instant.core, "load_heroes", lambda: {})
    monkeypatch.setattr(
        instant,
        "find_watched_in_realtime",
        lambda *_args: [("200", "alert A"), ("300", "alert B")],
    )
    path = tmp_path / "console.log"
    path.write_text("", encoding="utf-8")
    current_time = [0]
    delivery_results = iter([False, True, True])
    delivered = []
    monitor = _make_monitor(
        path,
        lambda *_args: _stats_with_player(),
        lambda: current_time[0],
        lambda alert: (delivered.append(alert), next(delivery_results))[1],
    )
    monitor.poll_once()
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(
            f"server steamid:{instant.gameserver_steam_id(4, 400)}\n"
        )
    monitor.poll_once()
    assert delivered == ["alert A", "alert B"]
    assert monitor._attempts == 1
    assert not monitor._handled

    current_time[0] = 10
    monitor.poll_once()

    assert delivered == ["alert A", "alert B", "alert A"]
    assert monitor._handled


def test_partial_failure_dedupes_delivered_id_when_hero_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(
        instant.core, "load_heroes", lambda: {"1": "Axe", "2": "Pudge"}
    )
    original_find = instant.find_watched_in_realtime
    alert_batches = []

    def find_alerts(*args):
        alerts = original_find(*args)
        alert_batches.append(alerts)
        return alerts

    monkeypatch.setattr(instant, "find_watched_in_realtime", find_alerts)
    path = tmp_path / "console.log"
    path.write_text("", encoding="utf-8")
    current_time = [0]
    fetch_count = [0]
    deliveries = []
    player_two_results = iter([False, True])

    def fetch(*_args):
        fetch_count[0] += 1
        first_hero = 0 if fetch_count[0] == 1 else 1
        return {
            "match": {"match_id": 1234},
            "teams": [
                {
                    "team_number": 2,
                    "players": [
                        {"accountid": 100, "name": "Me", "team": 2, "heroid": 1}
                    ],
                },
                {
                    "team_number": 3,
                    "players": [
                        {
                            "accountid": 200,
                            "name": "Player One",
                            "team": 3,
                            "heroid": first_hero,
                        },
                        {
                            "accountid": 300,
                            "name": "Player Two",
                            "team": 3,
                            "heroid": 2,
                        },
                    ],
                },
            ],
        }

    def deliver(alert):
        deliveries.append(alert)
        if "Player One" in alert:
            return True
        return next(player_two_results)

    monitor = _make_monitor(
        path,
        fetch,
        lambda: current_time[0],
        deliver,
        watchlist={
            "players": [
                {"account_id": 200, "name": "Player One"},
                {"account_id": 300, "name": "Player Two"},
            ]
        },
    )
    monitor.poll_once()
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(
            f"server steamid:{instant.gameserver_steam_id(10, 1000)}\n"
        )
    monitor.poll_once()
    current_time[0] = 10
    monitor.poll_once()

    assert [account_id for account_id, _alert in alert_batches[0]] == ["200", "300"]
    assert "ยังไม่เลือกฮีโร่" in alert_batches[0][0][1]
    assert "Axe" in alert_batches[1][0][1]
    assert ["Player One" in alert for alert in deliveries] == [True, False, False]
    assert ["Player Two" in alert for alert in deliveries] == [False, True, True]
    assert monitor._handled


def test_on_found_is_called_once_across_delivery_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(instant.core, "load_heroes", lambda: {})
    path = tmp_path / "console.log"
    path.write_text("", encoding="utf-8")
    current_time = [0]
    delivery_results = iter([False, True])
    found_alerts = []
    monitor = _make_monitor(
        path,
        lambda *_args: _stats_with_player(),
        lambda: current_time[0],
        lambda _alert: next(delivery_results),
        on_found=found_alerts.append,
    )
    monitor.poll_once()
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(
            f"server steamid:{instant.gameserver_steam_id(11, 1100)}\n"
        )
    monitor.poll_once()
    current_time[0] = 10
    monitor.poll_once()

    assert len(found_alerts) == 1
    assert len(found_alerts[0]) == 1
    assert "Watched" in found_alerts[0][0]
    assert monitor._handled


def test_poll_times_out_when_players_never_arrive(tmp_path):
    path = tmp_path / "console.log"
    path.write_text("", encoding="utf-8")
    current_time = [0]
    logs = []
    monitor = _make_monitor(
        path,
        lambda *_args: None,
        lambda: current_time[0],
        logs=logs,
    )
    monitor.poll_once()
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(
            f"server steamid:{instant.gameserver_steam_id(5, 500)}\n"
        )
    monitor.poll_once()
    assert not monitor._handled

    current_time[0] = 600
    monitor.poll_once()

    assert monitor._handled
    assert any("ไม่ได้ข้อมูลผู้เล่นจาก Steam (หมดเวลา)" in message for message in logs)


def test_new_server_id_resets_retry_state(tmp_path, monkeypatch):
    monkeypatch.setattr(instant.core, "load_heroes", lambda: {})
    monkeypatch.setattr(
        instant, "find_watched_in_realtime", lambda *_args: [("200", "alert")]
    )
    path = tmp_path / "console.log"
    path.write_text("", encoding="utf-8")
    current_time = [0]
    fetch_calls = []
    delivery_results = iter([False, True])
    monitor = _make_monitor(
        path,
        lambda _key, server_id: (fetch_calls.append(server_id), _stats_with_player())[1],
        lambda: current_time[0],
        lambda _alert: next(delivery_results),
    )
    monitor.poll_once()
    first_id = instant.gameserver_steam_id(6, 600)
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(f"server steamid:{first_id}\n")
    monitor.poll_once()
    assert monitor._attempts == 1
    assert not monitor._handled

    second_id = instant.gameserver_steam_id(7, 700)
    current_time[0] = 20
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(f"server steamid:{second_id}\n")
    monitor.poll_once()

    assert fetch_calls == [str(first_id), str(second_id)]
    assert monitor._server_id == str(second_id)
    assert monitor._attempts == 0
    assert monitor._handled


def test_poll_handles_truncated_console_log(tmp_path):
    path = tmp_path / "console.log"
    path.write_text("old history " * 30, encoding="utf-8")
    fetch_calls = []
    monitor = _make_monitor(
        path,
        lambda _key, server_id: (fetch_calls.append(server_id), {"teams": []})[1],
        lambda: 0,
    )
    monitor.poll_once()

    new_server = instant.gameserver_steam_id(8, 800)
    path.write_text(f"server steamid:{new_server}\n", encoding="utf-8")
    monitor.poll_once()

    assert fetch_calls == [str(new_server)]


def test_partial_console_line_is_buffered_until_newline(tmp_path):
    path = tmp_path / "console.log"
    path.write_text("", encoding="utf-8")
    fetch_calls = []
    monitor = _make_monitor(
        path,
        lambda _key, server_id: (fetch_calls.append(server_id), {"teams": []})[1],
        lambda: 0,
    )
    monitor.poll_once()
    server_id = str(instant.gameserver_steam_id(9, 900))
    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(f"server steamid:{server_id[:9]}")
    monitor.poll_once()
    assert fetch_calls == []

    with path.open("a", encoding="utf-8") as console_log:
        console_log.write(f"{server_id[9:]}\n")
    monitor.poll_once()
    assert fetch_calls == [server_id]


def test_poll_logs_missing_console_log_once_and_retries_lookup(tmp_path):
    path = tmp_path / "console.log"
    logs = []
    paths = []
    monitor = _make_monitor(path, lambda *_args: None, lambda: 0, logs=logs)
    monitor.find_logs = lambda: paths
    monitor.poll_once()
    monitor.poll_once()

    assert sum("ไม่พบ console.log" in message for message in logs) == 1

    path.write_text("existing history\n", encoding="utf-8")
    paths.append(path)
    monitor.poll_once()
    assert monitor._log_path == path


def test_poll_without_api_key_logs_once_and_skips_file_lookup(tmp_path):
    logs = []
    monitor = _make_monitor(tmp_path / "console.log", lambda *_args: None, lambda: 0, logs=logs)
    monitor.cfg.steam_api_key = ""
    monitor.find_logs = lambda: (_ for _ in ()).throw(AssertionError("must not look up logs"))

    monitor.poll_once()
    monitor.poll_once()

    assert sum("Steam Web API Key" in message for message in logs) == 1


def test_instant_monitor_start_and_stop():
    monitor = instant.InstantMonitor(
        SimpleNamespace(steam_api_key="test-key", my_account_id="100"),
        lambda: {"players": []},
        lambda _message: None,
        lambda _alert: True,
        find_logs=[],
    )

    monitor.start()
    thread = monitor._thread
    assert monitor.is_running

    monitor.stop()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert not monitor.is_running
