from __future__ import annotations

import logging
from pathlib import Path

import pytest

from video_concat.config import AppConfig, ConfigError, find_config, load_config


def _write(path: Path, text: str, *, bom: bool = False) -> Path:
    data = ("\ufeff" if bom else "") + text
    path.write_text(data, encoding="utf-8")
    return path


def test_load_config_all_values(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "video-concat.ini",
        """
[http]
referer = https://example.com/a%20b
user_agent = TestAgent/1.0

[download]
concurrency = 4
retries = 5
timeout = 12.5

[playlist]
variant = 2
base_url = https://example.com/base/
token_query = token
token_header = X-Token

[output]
path = out.mp4
work_dir = work
save_playlist = save.m3u8

[behavior]
keep_segments = true
force = yes

[ffmpeg]
path = tools/ffmpeg
auto_download = false
""",
    )

    config = load_config(path)

    assert config.referer == "https://example.com/a%20b"
    assert config.user_agent == "TestAgent/1.0"
    assert config.concurrency == 4
    assert config.retries == 5
    assert config.timeout == 12.5
    assert config.variant == "2"
    assert config.base_url == "https://example.com/base/"
    assert config.token_query == "token"
    assert config.token_header == "X-Token"
    assert config.output == (tmp_path / "out.mp4")
    assert config.work_dir == (tmp_path / "work")
    assert config.save_playlist == (tmp_path / "save.m3u8")
    assert config.keep_segments is True
    assert config.force is True
    assert config.ffmpeg_path == (tmp_path / "tools" / "ffmpeg")
    assert config.auto_download is False


def test_load_config_bom_and_empty_values(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "video-concat.ini",
        """
[http]
referer =
user_agent =

[download]
concurrency =
""",
        bom=True,
    )

    config = load_config(path)
    assert config == AppConfig()


def test_load_config_missing_returns_defaults() -> None:
    assert load_config(None) == AppConfig()


def test_find_config_explicit_missing(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        find_config(tmp_path / "missing.ini")


def test_find_config_explicit(tmp_path: Path) -> None:
    path = _write(tmp_path / "custom.ini", "[http]\nreferer = https://example.com/\n")
    assert find_config(path) == path.resolve()


def test_find_config_default_from_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path / "video-concat.ini", "[http]\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert find_config() == path.resolve()


@pytest.mark.parametrize(
    "text",
    [
        "[download]\nconcurrency = 0\n",
        "[download]\nretries = 0\n",
        "[download]\ntimeout = 0\n",
        "[playlist]\nvariant = fast\n",
        "[playlist]\nvariant = -1\n",
        "[http]\nreferer = ftp://example.com/\n",
        "[playlist]\nbase_url = ftp://example.com/\n",
        "[ffmpeg]\nauto_download = maybe\n",
    ],
)
def test_load_config_invalid_values(tmp_path: Path, text: str) -> None:
    path = _write(tmp_path / "video-concat.ini", text)
    with pytest.raises(ConfigError):
        load_config(path)


def test_unknown_keys_warn(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    path = _write(
        tmp_path / "video-concat.ini",
        "[unknown]\nkey = value\n[http]\nunknown = value\n",
    )
    with caplog.at_level(logging.WARNING):
        load_config(path)
    assert "未知のセクション" in caplog.text
    assert "未知の項目" in caplog.text
