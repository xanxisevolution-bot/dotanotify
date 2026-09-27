from types import SimpleNamespace

from dotanotify import core


def test_config_roundtrip_and_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("DOTANOTIFY_DATA_DIR", str(tmp_path))
    config = core.Config(my_account_id="123", check_interval_min=12)
    core.save_config(config)

    assert core.load_config() == config

    core.save_json(tmp_path / "config.json", {"line_user_id": "U123", "unknown": True})
    loaded = core.load_config()
    assert loaded.line_user_id == "U123"
    assert loaded.my_account_id == ""
    assert loaded.check_interval_min == 5
    assert not hasattr(loaded, "unknown")


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
        {"last_match_id": 10},
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
    assert state == {"last_match_id": 0}
