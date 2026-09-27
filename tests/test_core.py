import threading
from types import SimpleNamespace

import pytest

from dotanotify import core


def test_config_roundtrip_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTANOTIFY_DATA_DIR", str(tmp_path))
    config = core.Config(
        my_account_id="123", check_interval_min=12, gsi_token="secure-token"
    )
    core.save_config(config)

    assert core.load_config() == config

    core.save_json(tmp_path / "config.json", {"line_user_id": "U123", "unknown": True})
    loaded = core.load_config()
    assert loaded.line_user_id == "U123"
    assert loaded.my_account_id == ""
    assert loaded.check_interval_min == 5
    assert loaded.gsi_token
    assert not hasattr(loaded, "unknown")


def test_load_config_generates_and_persists_gsi_token(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTANOTIFY_DATA_DIR", str(tmp_path))

    generated = core.load_config().gsi_token
    assert generated
    assert generated != "dota_watchlist_secret"
    assert core.load_config().gsi_token == generated

    core.save_json(tmp_path / "config.json", {"gsi_token": "dota_watchlist_secret"})
    rotated = core.load_config().gsi_token
    assert rotated
    assert rotated != "dota_watchlist_secret"
    assert core.load_config().gsi_token == rotated


def test_first_match_run_sets_baseline_without_alerts(monkeypatch):
    monkeypatch.setattr(
        core,
        "api_get",
        lambda url: [{"match_id": 15}, {"match_id": 17}, {"match_id": 16}],
    )
    messages = []
    alerts, state = core.check_recent_matches(
        SimpleNamespace(my_account_id="1"),
        {"players": [{"account_id": 2}]},
        {"last_match_id": 0},
        {},
        messages.append,
        sleep=lambda _: None,
    )

    assert alerts == []
    assert state["last_match_id"] == 17
    assert any("จุดเริ่มต้น" in message for message in messages)


def test_new_matches_alert_teammate_and_enemy_and_advance_state(monkeypatch):
    responses = {
        "matches?limit=20": [
            {"match_id": 9},
            {"match_id": 10},
            {"match_id": 11},
            {"match_id": 12},
        ],
        "/matches/11": {
            "start_time": 1710000000,
            "radiant_win": True,
            "players": [
                {"account_id": 1, "player_slot": 0},
                {
                    "account_id": 2,
                    "player_slot": 1,
                    "personaname": "Teammate",
                    "hero_id": 7,
                    "kills": 5,
                    "deaths": 2,
                    "assists": 8,
                },
            ],
        },
        "/matches/12": {
            "start_time": 1710000000,
            "radiant_win": True,
            "players": [
                {"account_id": 1, "player_slot": 0},
                {
                    "account_id": 3,
                    "player_slot": 128,
                    "personaname": "Enemy",
                    "hero_id": 14,
                    "kills": 2,
                    "deaths": 7,
                    "assists": 1,
                },
            ],
        },
    }
    calls = []

    def fake_api_get(url):
        calls.append(url)
        for key, value in responses.items():
            if key in url:
                return value
        raise AssertionError(f"Unexpected API URL: {url}")

    monkeypatch.setattr(core, "api_get", fake_api_get)
    alerts, state = core.check_recent_matches(
        SimpleNamespace(my_account_id="1"),
        {
            "players": [
                {"account_id": 2, "name": "Watch Teammate", "tag": "smurf", "note": "note"},
                {"account_id": 3, "name": "Watch Enemy", "tag": "", "note": "other"},
            ]
        },
        {"last_match_id": 10, "account_id": "1", "failures": {}},
        {"7": "Earthshaker", "14": "Pudge"},
        lambda _: None,
        sleep=lambda _: None,
    )

    assert state["last_match_id"] == 12
    assert len(alerts) == 2
    assert "เพื่อนร่วมทีม 🤝" in alerts[0]
    assert "ชนะ ✅" in alerts[0]
    assert "ฝ่ายตรงข้าม ⚔️" in alerts[1]
    assert "แพ้ ❌" in alerts[1]
    assert "Hero: Earthshaker  (5/2/8)" in alerts[0]
    assert all("/matches/9" not in url for url in calls)
    assert sum("/matches/" in url for url in calls) == 2


def test_empty_watchlist_does_not_fetch(monkeypatch):
    monkeypatch.setattr(
        core, "api_get", lambda _: (_ for _ in ()).throw(AssertionError("network call"))
    )
    alerts, state = core.check_recent_matches(
        SimpleNamespace(my_account_id="1"),
        {"players": []},
        {"last_match_id": 0},
        {},
        lambda _: None,
        sleep=lambda _: None,
    )
    assert alerts == []
    assert state == {"last_match_id": 0, "account_id": "", "failures": {}}


def test_empty_account_id_does_not_fetch_and_logs(monkeypatch):
    monkeypatch.setattr(
        core, "api_get", lambda _: (_ for _ in ()).throw(AssertionError("network call"))
    )
    messages = []
    state = {"last_match_id": 23}
    alerts, new_state = core.check_recent_matches(
        SimpleNamespace(my_account_id=""),
        {"players": [{"account_id": 2}]},
        state,
        {},
        messages.append,
        sleep=lambda _: None,
    )

    assert alerts == []
    assert new_state == {
        "last_match_id": 23,
        "account_id": "",
        "failures": {},
    }
    assert "ยังไม่ได้ตั้งค่า My Steam32 Account ID" in messages


def test_failed_alert_delivery_retries_before_advancing_match_cursor(monkeypatch):
    monkeypatch.setattr(
        core,
        "api_get",
        lambda url: (
            [{"match_id": 11}, {"match_id": 12}]
            if "matches?limit=20" in url
            else {
                "start_time": 1710000000,
                "radiant_win": True,
                "players": [
                    {"account_id": 1, "player_slot": 0},
                    {"account_id": 2, "player_slot": 1, "hero_id": 7},
                ],
            }
        ),
    )
    delivered_alerts = []
    should_fail = [True]

    def deliver(alert):
        delivered_alerts.append(alert)
        if should_fail[0]:
            should_fail[0] = False
            return False
        return True

    args = (
        SimpleNamespace(my_account_id="1"),
        {"players": [{"account_id": 2, "name": "Watched"}]},
        {"last_match_id": 10, "account_id": "1", "failures": {}},
        {},
        lambda _: None,
    )
    first_alerts, first_state = core.check_recent_matches(
        *args, sleep=lambda _: None, deliver=deliver
    )

    assert len(first_alerts) == 1
    assert first_state["last_match_id"] == 10
    assert first_state["failures"] == {"11": 1}

    second_alerts, second_state = core.check_recent_matches(
        args[0],
        args[1],
        first_state,
        args[3],
        args[4],
        sleep=lambda _: None,
        deliver=deliver,
    )

    assert len(second_alerts) == 2
    assert second_state["last_match_id"] == 12
    assert second_state["failures"] == {}
    assert len(delivered_alerts) == 3


def test_detail_failure_is_retried_three_times_then_later_match_processed(monkeypatch):
    detail_calls = []

    def fake_api_get(url):
        if "matches?limit=20" in url:
            return [{"match_id": 11}, {"match_id": 12}]
        detail_calls.append(url.rsplit("/", 1)[-1])
        if url.endswith("/matches/11"):
            return None
        return {"players": [{"account_id": 1, "player_slot": 0}]}

    monkeypatch.setattr(core, "api_get", fake_api_get)
    state = {"last_match_id": 10, "account_id": "1", "failures": {}}
    for _ in range(3):
        _, state = core.check_recent_matches(
            SimpleNamespace(my_account_id="1"),
            {"players": [{"account_id": 2}]},
            state,
            {},
            lambda _: None,
            sleep=lambda _: None,
        )

    assert detail_calls.count("11") == 3
    assert detail_calls.count("12") == 1
    assert state == {"last_match_id": 12, "account_id": "1", "failures": {}}


def test_account_switch_resets_cursor_to_new_baseline(monkeypatch):
    monkeypatch.setattr(
        core,
        "api_get",
        lambda url: [{"match_id": 15}, {"match_id": 17}]
        if "matches?limit=20" in url
        else (_ for _ in ()).throw(AssertionError("match details must not be fetched")),
    )
    alerts, state = core.check_recent_matches(
        SimpleNamespace(my_account_id="2"),
        {"players": [{"account_id": 3}]},
        {"last_match_id": 10, "account_id": "1", "failures": {"11": 2}},
        {},
        lambda _: None,
        sleep=lambda _: None,
    )

    assert alerts == []
    assert state == {"last_match_id": 17, "account_id": "2", "failures": {}}


def test_deliver_alert_without_line_credentials_logs_and_succeeds():
    messages = []

    assert core.deliver_alert(core.Config(), "alert text", messages.append)
    assert messages == ["[ไม่ได้ตั้งค่า LINE] alert text"]


def test_parse_settings_validates_all_values():
    values = {
        "my_account_id": "123",
        "line_channel_token": " line-token ",
        "line_user_id": " user-id ",
        "check_interval_min": "10",
        "gsi_port": "3030",
        "gsi_token": "secure-token",
        "auto_start": True,
    }
    assert core.parse_settings(values) == {
        "my_account_id": "123",
        "line_channel_token": "line-token",
        "line_user_id": "user-id",
        "check_interval_min": 10,
        "gsi_port": 3030,
        "gsi_token": "secure-token",
        "auto_start": True,
    }
    empty_id = dict(values)
    empty_id["my_account_id"] = ""
    assert core.parse_settings(empty_id)["my_account_id"] == ""

    for key, value in (
        ("check_interval_min", "0"),
        ("check_interval_min", "121"),
        ("check_interval_min", "invalid"),
        ("gsi_port", "0"),
        ("gsi_port", "65536"),
        ("gsi_port", "invalid"),
        ("my_account_id", "not-numeric"),
        ("gsi_token", ""),
    ):
        invalid = dict(values)
        invalid[key] = value
        with pytest.raises(ValueError):
            core.parse_settings(invalid)


def test_monitor_restart_exits_old_loop_thread():
    monitor = core.Monitor(
        SimpleNamespace(check_interval_min=60), lambda: {"players": []}, lambda _: None
    )
    first_check_started = threading.Event()
    release_first_check = threading.Event()
    second_check_started = threading.Event()
    calls = []
    calls_lock = threading.Lock()

    def fake_check():
        with calls_lock:
            calls.append(threading.current_thread())
            call_number = len(calls)
        if call_number == 1:
            first_check_started.set()
            release_first_check.wait(2)
        elif call_number == 2:
            second_check_started.set()

    monitor._check_once = fake_check
    monitor.start()
    old_thread = monitor._thread
    try:
        assert first_check_started.wait(1)
        monitor.stop()
        monitor.start()
        current_thread = monitor._thread
        assert second_check_started.wait(1)
        release_first_check.set()
        old_thread.join(timeout=1)

        assert not old_thread.is_alive()
        assert current_thread.is_alive()
        assert len(calls) == 2
        assert monitor.is_running
    finally:
        release_first_check.set()
        monitor.stop()
        monitor._thread.join(timeout=1)
