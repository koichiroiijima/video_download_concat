from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from video_concat import browser_cdp
from video_concat.browser_cdp import (
    BrowserError,
    _playwright_browsers_roots,
    find_browser,
)


def _chromium_relative() -> Path:
    if os.name == "nt":
        return Path("chrome-win64") / "chrome.exe"
    if sys.platform == "darwin":
        return (
            Path("chrome-mac")
            / "Chromium.app"
            / "Contents"
            / "MacOS"
            / "Chromium"
        )
    return Path("chrome-linux64") / "chrome"


def _make_chromium(root: Path, revision: int = 1234) -> Path:
    binary = root / f"chromium-{revision}" / _chromium_relative()
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("", encoding="utf-8")
    return binary


def _isolate(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
) -> None:
    """Playwright 以外のブラウザを無効化し、探索先を root だけにする。"""
    monkeypatch.setattr(browser_cdp, "_playwright_browsers_roots", lambda: [root])
    monkeypatch.setattr(browser_cdp.shutil, "which", lambda name: None)
    monkeypatch.delenv("VIDEO_CONCAT_BROWSER", raising=False)
    for key in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
        monkeypatch.delenv(key, raising=False)


def test_find_browser_detects_playwright_chromium(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = _make_chromium(tmp_path)
    _isolate(monkeypatch, tmp_path)

    assert find_browser() == str(binary)


def test_find_browser_prefers_newest_playwright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _make_chromium(tmp_path, revision=999)
    newest = _make_chromium(tmp_path, revision=1248)
    _isolate(monkeypatch, tmp_path)

    assert find_browser() == str(newest)


def test_explicit_browser_path_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom-chrome"
    explicit.write_text("", encoding="utf-8")

    assert find_browser(str(explicit)) == str(explicit)


def test_env_browser_wins_over_playwright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_browser = tmp_path / "env-chrome"
    env_browser.write_text("", encoding="utf-8")
    _make_chromium(tmp_path / "playwright")
    monkeypatch.setenv("VIDEO_CONCAT_BROWSER", str(env_browser))
    monkeypatch.setattr(
        browser_cdp, "_playwright_browsers_roots", lambda: [tmp_path / "playwright"]
    )

    assert find_browser() == str(env_browser)


def test_find_browser_raises_without_browser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolate(monkeypatch, tmp_path)

    with pytest.raises(BrowserError):
        find_browser()


def test_playwright_roots_uses_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))

    roots = _playwright_browsers_roots()

    assert roots[0] == tmp_path


def test_playwright_roots_ignores_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", "0")

    roots = _playwright_browsers_roots()

    assert all(str(root) != "0" for root in roots)
