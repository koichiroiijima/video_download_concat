from __future__ import annotations

import hashlib
import io
import os
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

from video_concat.ffmpeg import FFmpegError
from video_concat.ffmpeg_bootstrap import (
    FFMPEG_BINARY_NAME,
    install_ffmpeg,
    locate_ffmpeg,
    resolve_ffmpeg,
)
from video_concat.ffmpeg_sources import FFmpegRelease


def _release(archive: Path, binary_name: str, kind: str) -> FFmpegRelease:
    return FFmpegRelease(
        platform_key="test",
        url=archive.as_uri(),
        sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
        size=archive.stat().st_size,
        binary_name=binary_name,
        archive_kind=kind,
        version="test",
    )


def _make_tar(path: Path, binary_name: str, payload: bytes) -> None:
    with tarfile.open(path, "w:xz") as tf:
        info = tarfile.TarInfo(f"ffmpeg-test/bin/{binary_name}")
        info.size = len(payload)
        tf.addfile(info, io.BytesIO(payload))


def _make_zip(path: Path, binary_name: str, payload: bytes) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"ffmpeg-test/bin/{binary_name}", payload)


def test_install_ffmpeg_from_tar(tmp_path: Path) -> None:
    archive = tmp_path / "ffmpeg.tar.xz"
    payload = b"#!/bin/sh\nexit 0\n"
    _make_tar(archive, FFMPEG_BINARY_NAME, payload)
    release = _release(archive, FFMPEG_BINARY_NAME, "tar.xz")

    destination = install_ffmpeg(
        tmp_path / "bin",
        release=release,
        show_progress=False,
        verify_binary=False,
    )

    assert destination == tmp_path / "bin" / FFMPEG_BINARY_NAME
    assert destination.read_bytes() == payload
    if os.name != "nt":
        assert os.access(destination, os.X_OK)


def test_install_ffmpeg_from_zip(tmp_path: Path) -> None:
    archive = tmp_path / "ffmpeg.zip"
    payload = b"fake exe"
    _make_zip(archive, FFMPEG_BINARY_NAME, payload)
    release = _release(archive, FFMPEG_BINARY_NAME, "zip")

    destination = install_ffmpeg(
        tmp_path / "bin",
        release=release,
        show_progress=False,
        verify_binary=False,
    )

    assert destination.read_bytes() == payload


def test_install_ffmpeg_checksum_mismatch(tmp_path: Path) -> None:
    archive = tmp_path / "ffmpeg.tar.xz"
    _make_tar(archive, FFMPEG_BINARY_NAME, b"data")
    release = _release(archive, FFMPEG_BINARY_NAME, "tar.xz")
    bad = FFmpegRelease(
        platform_key=release.platform_key,
        url=release.url,
        sha256="0" * 64,
        size=release.size,
        binary_name=release.binary_name,
        archive_kind=release.archive_kind,
        version=release.version,
    )

    with pytest.raises(FFmpegError, match="SHA256"):
        install_ffmpeg(
            tmp_path / "bin",
            release=bad,
            show_progress=False,
            verify_binary=False,
        )


def test_install_ffmpeg_existing_without_force(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    destination = tmp_path / "bin" / FFMPEG_BINARY_NAME
    destination.parent.mkdir()
    destination.write_bytes(b"existing")

    def fail(*args, **kwargs):
        raise AssertionError("download_file を呼んではいけません")

    monkeypatch.setattr("video_concat.ffmpeg_bootstrap.download_file", fail)
    result = install_ffmpeg(
        tmp_path / "bin",
        release=FFmpegRelease(
            platform_key="test",
            url="https://example.com/ffmpeg.tar.xz",
            sha256="0" * 64,
            size=1,
            binary_name=FFMPEG_BINARY_NAME,
            archive_kind="tar.xz",
            version="test",
        ),
        show_progress=False,
    )
    assert result == destination


def test_locate_ffmpeg_prefers_bundled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bundled = tmp_path / FFMPEG_BINARY_NAME
    bundled.write_bytes(b"binary")
    monkeypatch.setattr(sys, "executable", str(tmp_path / "video-concat"))
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/ffmpeg")

    found = locate_ffmpeg()
    assert found == str(bundled)
    if os.name != "nt":
        assert os.access(bundled, os.X_OK)


def test_locate_ffmpeg_explicit_missing(tmp_path: Path) -> None:
    with pytest.raises(FFmpegError):
        locate_ffmpeg(tmp_path / "missing")


def test_resolve_ffmpeg_auto_download_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("video_concat.ffmpeg_bootstrap.locate_ffmpeg", lambda explicit=None: None)
    with pytest.raises(FFmpegError, match="自動ダウンロードは無効"):
        resolve_ffmpeg(auto_download=False)


def test_resolve_ffmpeg_source_mode_does_not_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("video_concat.ffmpeg_bootstrap.locate_ffmpeg", lambda explicit=None: None)
    monkeypatch.setattr("video_concat.ffmpeg_bootstrap.is_frozen", lambda: False)
    with pytest.raises(FFmpegError, match="開発実行中"):
        resolve_ffmpeg(auto_download=True)
