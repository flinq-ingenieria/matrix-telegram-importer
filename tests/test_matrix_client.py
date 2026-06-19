from __future__ import annotations

import json
from pathlib import Path

import responses

from telegram_to_matrix.matrix_client import MatrixClient


@responses.activate
def test_matrix_client_send_preserves_ts_and_user_id(tmp_path: Path) -> None:
    client = MatrixClient(
        homeserver="https://matrix.test",
        as_token="as",
        hs_token="hs",
        synapse_admin_token="admin",
    )

    responses.add(
        responses.PUT,
        "https://matrix.test/_matrix/client/v3/rooms/%21room%3Atest/send/m.room.message/tg-1-1",
        json={"event_id": "$event"},
        status=200,
    )

    event_id = client.send_message(
        room_id="!room:test",
        txn_id="tg-1-1",
        sender_mxid="@alice:test",
        timestamp_ms=1700000000000,
        content={"msgtype": "m.text", "body": "hola"},
    )

    assert event_id == "$event"
    req = responses.calls[0].request
    assert "user_id=%40alice%3Atest" in req.url
    assert "ts=1700000000000" in req.url


@responses.activate
def test_matrix_client_create_room_sets_power_override() -> None:
    client = MatrixClient(
        homeserver="https://matrix.test",
        as_token="as",
        hs_token="hs",
        synapse_admin_token="admin",
    )

    responses.add(
        responses.POST,
        "https://matrix.test/_matrix/client/v3/createRoom",
        json={"room_id": "!room:test"},
        status=200,
    )

    room_id = client.create_room("Grupo", "@alice:test", actor_mxid="@bot:test")

    assert room_id == "!room:test"
    req = responses.calls[0].request
    assert "user_id=%40bot%3Atest" in req.url
    body = json.loads(req.body)
    assert body["power_level_content_override"]["users"]["@bot:test"] == 100
    assert body["power_level_content_override"]["users"]["@alice:test"] == 100
    assert body["power_level_content_override"]["state_default"] == 50


@responses.activate
def test_matrix_client_upload_media(tmp_path: Path) -> None:
    client = MatrixClient(
        homeserver="https://matrix.test",
        as_token="as",
        hs_token="hs",
        synapse_admin_token="admin",
    )

    responses.add(
        responses.POST,
        "https://matrix.test/_matrix/media/v3/upload",
        json={"content_uri": "mxc://matrix.test/abc"},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://matrix.test/_matrix/client/v1/media/download/matrix.test/abc",
        status=200,
    )

    file_path = tmp_path / "audio.ogg"
    file_path.write_bytes(b"123")

    uri = client.upload_media(file_path, "audio/ogg", "@alice:test")
    assert uri == "mxc://matrix.test/abc"


@responses.activate
def test_matrix_client_set_room_history_visibility() -> None:
    client = MatrixClient(
        homeserver="https://matrix.test",
        as_token="as",
        hs_token="hs",
        synapse_admin_token="admin",
    )

    responses.add(
        responses.PUT,
        "https://matrix.test/_matrix/client/v3/rooms/%21room%3Atest/state/m.room.history_visibility",
        json={},
        status=200,
    )

    client.set_room_history_visibility("!room:test", "@alice:test", "shared")

    req = responses.calls[0].request
    assert "user_id=%40alice%3Atest" in req.url
    assert json.loads(req.body) == {"history_visibility": "shared"}


@responses.activate
def test_matrix_client_make_room_admin() -> None:
    client = MatrixClient(
        homeserver="https://matrix.test",
        as_token="as",
        hs_token="hs",
        synapse_admin_token="admin",
    )

    responses.add(
        responses.POST,
        "https://matrix.test/_synapse/admin/v1/rooms/%21room%3Atest/make_room_admin",
        json={},
        status=200,
    )

    client.make_room_admin("!room:test", "@alice:test")

    req = responses.calls[0].request
    assert json.loads(req.body) == {"user_id": "@alice:test"}


@responses.activate
def test_matrix_client_list_users_and_deactivate() -> None:
    client = MatrixClient(
        homeserver="https://matrix.test",
        as_token="as",
        hs_token="hs",
        synapse_admin_token="admin",
    )

    responses.add(
        responses.GET,
        "https://matrix.test/_synapse/admin/v2/users",
        json={
            "users": [
                {"name": "@tg_one:test"},
                {"name": "@alice:test"},
            ],
            "next_token": None,
        },
        status=200,
    )
    responses.add(
        responses.POST,
        "https://matrix.test/_synapse/admin/v1/deactivate/%40tg_one%3Atest",
        json={},
        status=200,
    )

    payload = client.list_users()
    assert payload["users"][0]["name"] == "@tg_one:test"

    client.deactivate_user("@tg_one:test")

    req = responses.calls[1].request
    assert json.loads(req.body) == {"erase": True}
