"""同梱・自動ダウンロードする ffmpeg の配布元情報。"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class FFmpegRelease:
    """1 つのプラットフォーム向け ffmpeg 配布物。"""

    platform_key: str
    url: str
    sha256: str
    size: int
    binary_name: str
    archive_kind: str
    version: str


# BtbN/FFmpeg-Builds の LGPL 静的ビルド。
# バージョンと SHA256 を固定し、自動ダウンロード時に検証する。
_RELEASES: dict[str, FFmpegRelease] = {
    "linux-x86_64": FFmpegRelease(
        platform_key="linux-x86_64",
        url=(
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
            "autobuild-2026-11-02-12-51/"
            "ffmpeg-n8.1.3-32-g760d10603e-linux64-lgpl-8.1.tar.xz"
        ),
        sha256="be5123da8ba6a1745814151ccdaaee5151212b65dde44cbe914e5b2cf10661a1",
        size=135179424,
        binary_name="ffmpeg",
        archive_kind="tar.xz",
        version="8.1.3 (BtbN autobuild-2026-11-02-12-51)",
    ),
    "windows-x86_64": FFmpegRelease(
        platform_key="windows-x86_64",
        url=(
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/"
            "autobuild-2026-11-02-12-51/"
            "ffmpeg-n8.1.3-32-g760d10603e-win64-lgpl-8.1.zip"
        ),
        sha256="d84ba8f0636333a242728908d8cdefa1496893cbc616c706a8379aa4570ced04",
        size=167779962,
        binary_name="ffmpeg.exe",
        archive_kind="zip",
        version="8.1.3 (BtbN autobuild-2026-11-02-12-51)",
    ),
}


def current_platform_key() -> str | None:
    """実行中のプラットフォームに対応するキーを返す。未対応なら None。"""
    machine = platform.machine().lower()
    if machine not in {"x86_64", "amd64"}:
        return None

    if sys.platform == "win32":
        return "windows-x86_64"
    if sys.platform.startswith("linux"):
        return "linux-x86_64"
    return None


def get_release(platform_key: str | None = None) -> FFmpegRelease | None:
    """プラットフォームに対応する配布物情報を返す。"""
    key = platform_key or current_platform_key()
    if key is None:
        return None
    return _RELEASES.get(key)


def available_platform_keys() -> list[str]:
    """定義済みプラットフォームキーの一覧。"""
    return sorted(_RELEASES)
