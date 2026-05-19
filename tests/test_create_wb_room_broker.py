from __future__ import annotations

import json

import pytest

from scripts.create_wb_room_broker import (
    CreatedRoom,
    RoomBrokerError,
    build_create_room_payload,
    build_room_url,
    format_broker_output,
    load_access_token,
)


def test_build_create_room_payload_matches_wb_room_shape() -> None:
    assert build_create_room_payload() == {
        "roomType": "ROOM_TYPE_ALL_ON_SCREEN",
        "roomPrivacy": "ROOM_PRIVACY_FREE",
    }


def test_build_room_url_uses_stream_room_path() -> None:
    assert build_room_url("room-1") == "https://stream.wb.ru/room/room-1"
    assert build_room_url("room-1", base_url="https://example.test/") == "https://example.test/room/room-1"


def test_load_access_token_prefers_explicit_token(tmp_path, monkeypatch) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n", encoding="utf-8")
    monkeypatch.setenv("WBSTREAM_ACCESS_TOKEN", "env-token")

    assert load_access_token(token=" explicit-token ", token_file=str(token_file)) == "explicit-token"


def test_load_access_token_reads_env(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("WBSTREAM_ACCESS_TOKEN", "env-token")

    assert load_access_token(token_file=str(tmp_path / "missing")) == "env-token"


def test_load_access_token_reads_file(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WBSTREAM_ACCESS_TOKEN", raising=False)
    token_file = tmp_path / "token"
    token_file.write_text("file-token\n", encoding="utf-8")

    assert load_access_token(token_file=str(token_file)) == "file-token"


def test_load_access_token_fails_when_missing(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("WBSTREAM_ACCESS_TOKEN", raising=False)

    with pytest.raises(RoomBrokerError, match="access token is missing"):
        load_access_token(token_file=str(tmp_path / "missing"))


def test_format_broker_output_emits_rooms_json() -> None:
    output = format_broker_output(
        [
            CreatedRoom(room_id="room-1", room_url="https://stream.wb.ru/room/room-1"),
            CreatedRoom(room_id="room-2", room_url="https://stream.wb.ru/room/room-2"),
        ]
    )

    body = json.loads(output)
    assert [room["room_url"] for room in body["rooms"]] == [
        "https://stream.wb.ru/room/room-1",
        "https://stream.wb.ru/room/room-2",
    ]
