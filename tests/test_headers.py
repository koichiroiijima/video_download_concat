from __future__ import annotations

import pytest

from video_concat.headers import (
    canonical_header,
    parse_header_items,
    validate_header_name,
    validate_header_value,
)


def test_canonical_header() -> None:
    assert canonical_header("x-api-key") == "X-Api-Key"
    assert canonical_header("REFERER") == "Referer"


def test_parse_header_items() -> None:
    headers = parse_header_items(
        ["Referer: https://example.com/", "x-token:abc"]
    )
    assert headers == {
        "Referer": "https://example.com/",
        "X-Token": "abc",
    }


@pytest.mark.parametrize(
    "items",
    [
        ["invalid"],
        [": value"],
        ["X-Test: value\nInjected: yes"],
        ["Bad Name: value"],
    ],
)
def test_parse_header_items_invalid(items: list[str]) -> None:
    with pytest.raises(ValueError):
        parse_header_items(items)


def test_header_validation() -> None:
    validate_header_name("X-Test")
    validate_header_value("value")
    with pytest.raises(ValueError):
        validate_header_name("")
    with pytest.raises(ValueError):
        validate_header_value("a\r\nb")
