from __future__ import annotations

import pytest

from scripts.generate_olcrtc_rescue_configs import (
    OlcRtcRescueConfig,
    build_client_yaml,
    build_server_yaml,
    build_uri,
    normalize_room_id,
)


KEY = "a" * 64


def test_generates_wbstream_vp8_server_and_client_yaml():
    config = OlcRtcRescueConfig(
        room_id="https://stream.wb.ru/room/019e3ab0-e4c1-7d0b-8ea4-7df731ec636d",
        key_hex=KEY,
    )

    server = build_server_yaml(config)
    client = build_client_yaml(config)

    assert 'id: "019e3ab0-e4c1-7d0b-8ea4-7df731ec636d"' in server
    assert "mode: srv" in server
    assert "mode: cnc" in client
    assert 'provider: "wbstream"' in server
    assert 'transport: "vp8channel"' in server
    assert "fps: 60" in server
    assert "batch_size: 64" in server
    assert "max_session_duration: 2h" in server
    assert "max_payload_size: 4096" in server
    assert 'host: "127.0.0.1"' in client
    assert "port: 8808" in client


def test_generates_olcrtc_uri_with_vp8_payload():
    config = OlcRtcRescueConfig(room_id="https://stream.wb.ru/room/room-1", key_hex=KEY)

    uri = build_uri(config, label="RootVPN Test")

    assert uri == (
        "olcrtc://wbstream?vp8channel<vp8-fps=60&vp8-batch=64>"
        "@room-1#aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa$RootVPN Test"
    )


def test_rejects_bad_key():
    config = OlcRtcRescueConfig(room_id="room-1", key_hex="bad")

    with pytest.raises(ValueError, match="64 hex"):
        build_server_yaml(config)


def test_rejects_non_positive_vp8_tuning():
    config = OlcRtcRescueConfig(room_id="room-1", key_hex=KEY, vp8_fps=0)

    with pytest.raises(ValueError, match="vp8_fps"):
        build_client_yaml(config)


def test_normalize_room_id_only_strips_wbstream_room_urls():
    assert normalize_room_id("https://stream.wb.ru/room/abc", carrier="wbstream") == "abc"
    assert normalize_room_id("https://meet.example/room/abc", carrier="jitsi") == "https://meet.example/room/abc"
