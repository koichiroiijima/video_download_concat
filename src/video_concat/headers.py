"""HTTP ヘッダの解析と検証。"""

from __future__ import annotations

import re

_HEADER_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def canonical_header(name: str) -> str:
    """ヘッダ名を一般的な表記（X-Api-Key など）に揃える。"""
    return "-".join(part.capitalize() for part in name.strip().split("-"))


def validate_header_name(name: str) -> None:
    """HTTP ヘッダ名として使えるか検証する。"""
    if not name or not _HEADER_NAME_RE.fullmatch(name):
        raise ValueError(f"ヘッダ名が不正です: {name!r}")


def validate_header_value(value: str) -> None:
    """ヘッダ値に改行などが含まれていないか検証する。"""
    if "\r" in value or "\n" in value:
        raise ValueError("ヘッダ値に改行を含めることはできません")


def parse_header_items(items: list[str]) -> dict[str, str]:
    """``NAME: VALUE`` 形式の複数指定を辞書に変換する。"""
    headers: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition(":")
        name = name.strip()
        if not separator or not name:
            raise ValueError(
                f"ヘッダの形式が不正です: {item!r}"
                "（NAME: VALUE の形式で指定してください）"
            )
        validate_header_name(name)
        value = value.strip()
        validate_header_value(value)
        headers[canonical_header(name)] = value
    return headers
