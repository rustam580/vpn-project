from __future__ import annotations

from enum import Enum
from types import SimpleNamespace

from src.vpnbot.channel_gate import (
    channel_gate_allowed_callback,
    channel_gate_allowed_message,
    channel_gate_chat_id,
    channel_gate_enabled,
    channel_gate_url,
    is_channel_member_status,
    normalize_channel_chat_id,
)


class FakeStatus(Enum):
    MEMBER = "member"
    LEFT = "left"


def test_channel_gate_chat_id_prefers_explicit_target() -> None:
    settings = SimpleNamespace(
        channel_subscription_required=True,
        channel_chat_id="-1001234567890",
        channel_url="https://t.me/rootvpn_news",
    )

    assert channel_gate_enabled(settings) is True
    assert channel_gate_chat_id(settings) == "-1001234567890"
    assert channel_gate_url(settings) == "https://t.me/rootvpn_news"


def test_normalize_channel_chat_id_supports_public_channel_handles() -> None:
    assert normalize_channel_chat_id("@rootvpn_news") == "@rootvpn_news"
    assert normalize_channel_chat_id("rootvpn_news") == "@rootvpn_news"
    assert normalize_channel_chat_id("https://t.me/rootvpn_news") == "@rootvpn_news"
    assert normalize_channel_chat_id("https://t.me/+privateInvite") == ""


def test_channel_member_status_accepts_only_real_members() -> None:
    assert is_channel_member_status("creator") is True
    assert is_channel_member_status("administrator") is True
    assert is_channel_member_status("member") is True
    assert is_channel_member_status(FakeStatus.MEMBER) is True
    assert is_channel_member_status("left") is False
    assert is_channel_member_status(FakeStatus.LEFT) is False
    assert is_channel_member_status("kicked") is False


def test_channel_gate_allows_only_entrypoint_commands_before_subscription() -> None:
    assert channel_gate_allowed_message("/start") is True
    assert channel_gate_allowed_message("/help@RootVPNBot") is True
    assert channel_gate_allowed_message("/support") is True
    assert channel_gate_allowed_message("/channel") is True
    assert channel_gate_allowed_message("/buy") is False
    assert channel_gate_allowed_message("Купить доступ") is False


def test_channel_gate_allows_channel_callback_before_subscription() -> None:
    assert channel_gate_allowed_callback("quick:channel") is True
    assert channel_gate_allowed_callback("quick:faq") is False
    assert channel_gate_allowed_callback("buyplan:m1:all") is False
