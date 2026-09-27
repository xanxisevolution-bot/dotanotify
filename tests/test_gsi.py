import json
import socket
import threading
import time
import urllib.error
import urllib.request

from dotanotify import gsi


def sample_gsi():
    return {
        "map": {
            "game_state": "DOTA_GAMERULES_STATE_GAME_IN_PROGRESS",
            "matchid": 12345,
        },
        "player": {"team_name": "radiant"},
        "allplayers": {
            "0": {
                "accountid": 10,
                "name": "Friend",
                "team_name": "radiant",
                "heroid": 7,
            },
            "1": {
                "accountid": 20,
                "name": "Enemy",
                "team_name": "dire",
                "heroid": 14,
            },
        },
    }


def test_find_watched_players_formats_teammate_and_enemy():
    alerts = gsi.find_watched_players(
        sample_gsi(),
        {
            "players": [
                {"account_id": 10, "tag": "danger", "note": "friend note"},
                {"account_id": 20, "tag": "", "note": "enemy note"},
            ]
        },
        {"7": "Earthshaker", "14": "Pudge"},
    )

    assert len(alerts) == 2
    assert "เพื่อนร่วมทีม 🤝" in alerts[0]
    assert "Earthshaker" in alerts[0]
    assert "ฝ่ายตรงข้าม ⚔️" in alerts[1]
    assert "Pudge" in alerts[1]


def test_server_rejects_bad_token_and_deduplicates_match(monkeypatch):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    watched = [{"account_id": 10, "name": "Friend", "tag": "", "note": "-"}]
    alerts = []
    logs = []
    monkeypatch.setattr(gsi, "load_heroes", lambda: {"7": "Earthshaker"})
    server = gsi.GSIServer(port, "secret", lambda: watched, alerts.append, logs.append)
    server.start()
    try:
        payload = sample_gsi()
        payload["allplayers"] = {"0": sample_gsi()["allplayers"]["0"]}
        payload["auth"] = {"token": "wrong"}
        request = urllib.request.Request(
            f"http://127.0.0.1:{port}/",
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            urllib.request.urlopen(request, timeout=3)
            assert False, "Expected HTTP 403"
        except urllib.error.HTTPError as error:
            assert error.code == 403

        payload["auth"] = {"token": "secret"}

        def post(data):
            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/",
                data=json.dumps(data).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=3) as response:
                assert response.status == 200

        payload["allplayers"] = {}
        post(payload)
        post(payload)
        deadline = time.time() + 2
        while not any("Dota ไม่ส่งข้อมูล allplayers" in message for message in logs):
            if time.time() >= deadline:
                break
            threading.Event().wait(0.01)
        assert not alerts
        assert sum("Dota ไม่ส่งข้อมูล allplayers" in message for message in logs) == 1

        payload["allplayers"] = {"0": sample_gsi()["allplayers"]["0"]}
        post(payload)
        post(payload)
        deadline = time.time() + 2
        while not alerts and time.time() < deadline:
            threading.Event().wait(0.01)
        assert len(alerts) == 1
    finally:
        server.stop()


def test_gsi_config_text_and_install(tmp_path):
    text = gsi.gsi_cfg_text(3030, "tok")
    assert '"http://127.0.0.1:3030/"' in text
    assert '"token"         "tok"' in text
    path = gsi.install_gsi_cfg(tmp_path, 3030, "tok")
    assert path == tmp_path / "gamestate_integration" / "gamestate_integration_dotanotify.cfg"
    assert path.read_text(encoding="utf-8") == text


def test_find_dota_cfg_dirs_parses_libraryfolders(tmp_path):
    steam = tmp_path / "Steam"
    extra_library = tmp_path / "SteamLibrary"
    cfg_dir = (
        extra_library
        / "steamapps"
        / "common"
        / "dota 2 beta"
        / "game"
        / "dota"
        / "cfg"
    )
    cfg_dir.mkdir(parents=True)
    (steam / "steamapps").mkdir(parents=True)
    (steam / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n    "1"\n    {{\n        "path" "{extra_library}"\n    }}\n}}\n',
        encoding="utf-8",
    )

    assert gsi.find_dota_cfg_dirs([steam]) == [cfg_dir]
