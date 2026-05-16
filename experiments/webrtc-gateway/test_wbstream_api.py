from __future__ import annotations

import pytest

from wbstream_api import extract_room_id, mask_secret


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("019e30d5-9b63-700e-8453-b514a5db7746", "019e30d5-9b63-700e-8453-b514a5db7746"),
        (
            "https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746",
            "019e30d5-9b63-700e-8453-b514a5db7746",
        ),
        (
            "https://stream.wb.ru/room/019e30d5-9b63-700e-8453-b514a5db7746/",
            "019e30d5-9b63-700e-8453-b514a5db7746",
        ),
    ],
)
def test_extract_room_id(raw: str, expected: str) -> None:
    assert extract_room_id(raw) == expected


def test_extract_room_id_rejects_empty() -> None:
    with pytest.raises(ValueError):
        extract_room_id("")


def test_mask_secret() -> None:
    assert mask_secret("") == ""
    assert mask_secret("short") == "*****"
    assert mask_secret("a" * 40) == "aaaaaaaa...aaaaaaaa"
