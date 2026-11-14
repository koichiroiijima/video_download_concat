from __future__ import annotations

from pathlib import Path

import pytest

from video_concat.cli import _merge_candidate_headers, main


def test_no_arguments_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    output = capsys.readouterr().out
    assert "usage: video-concat" in output
    assert "m3u8" in output


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--version"])
    assert exc_info.value.code == 0
    assert "video-concat" in capsys.readouterr().out


def test_missing_config_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = main(
        [
            "https://example.com/video.m3u8",
            "--config",
            str(tmp_path / "missing.ini"),
        ]
    )
    assert code == 1
    assert "設定ファイルが見つかりません" in capsys.readouterr().err


def test_invalid_config_returns_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "video-concat.ini"
    config.write_text("[http]\nreferer = ftp://example.com/\n", encoding="utf-8")
    code = main(
        [
            "https://example.com/video.m3u8",
            "--config",
            str(config),
        ]
    )
    assert code == 1
    assert "referer" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        (
            ["https://example.com/video.m3u8", "--from-page", "https://example.com/"],
            "同時に指定できません",
        ),
        (["--from-page", "file:///tmp/page.html"], "--from-page"),
        (
            [
                "--from-page",
                "https://example.com/",
                "--base-url",
                "https://example.com/",
            ],
            "--base-url",
        ),
        (["--list-candidates"], "--from-page"),
        (
            ["https://example.com/video.m3u8", "--source-index", "1"],
            "--source-index",
        ),
        (
            ["--prefer-video", "https://example.com/video.m3u8"],
            "--prefer-video",
        ),
        (
            [
                "--from-page",
                "https://example.com/",
                "--source-index",
                "0",
                "--prefer-video",
            ],
            "同時に指定できません",
        ),
        (
            ["--from-page", "https://example.com/", "--page-mode", "unknown"],
            "--page-mode",
        ),
        (
            ["--from-page", "https://example.com/", "--page-wait", "-1"],
            "--page-wait",
        ),
        (
            [
                "--from-page",
                "https://example.com/",
                "--page-mode",
                "static",
                "--headed",
            ],
            "--headed",
        ),
    ],
)
def test_from_page_argument_validation(
    arguments: list[str],
    message: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(arguments) == 1
    assert message in capsys.readouterr().err


def test_merge_candidate_headers_prefers_explicit_values() -> None:
    headers = {
        "Referer": "https://explicit.example/",
        "User-Agent": "explicit-agent",
        "Cookie": "explicit=1",
    }
    candidate = {
        "Referer": "https://browser.example/",
        "Origin": "https://browser.example",
        "Cookie": "browser=1",
        "User-Agent": "browser-agent",
    }

    _merge_candidate_headers(headers, candidate, allow_user_agent=False)

    assert headers["Referer"] == "https://explicit.example/"
    assert headers["User-Agent"] == "explicit-agent"
    assert headers["Cookie"] == "explicit=1"
    assert headers["Origin"] == "https://browser.example"


def test_merge_candidate_headers_uses_browser_user_agent() -> None:
    headers = {"User-Agent": "default-agent"}
    candidate = {"User-Agent": "browser-agent"}

    _merge_candidate_headers(headers, candidate, allow_user_agent=True)

    assert headers["User-Agent"] == "browser-agent"
