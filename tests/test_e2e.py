from __future__ import annotations

import glob
import http.server
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from video_concat import cli

ROOT = Path(__file__).resolve().parents[1]


def _find_ffmpeg() -> str | None:
    env = os.environ.get("VIDEO_CONCAT_TEST_FFMPEG")
    if env and Path(env).is_file():
        return env
    for candidate in (
        ROOT / "vendor" / "ffmpeg" / "linux-x86_64" / "ffmpeg",
        ROOT / "vendor" / "ffmpeg" / "ffmpeg",
    ):
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg")


FFMPEG = _find_ffmpeg()

pytestmark = pytest.mark.skipif(
    FFMPEG is None,
    reason="テスト用 ffmpeg がありません（VIDEO_CONCAT_TEST_FFMPEG で指定できます）",
)


def _make_segment(ffmpeg: str, path: Path, frequency: int) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=160x120:rate=10",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency={frequency}:sample_rate=44100",
        "-t",
        "1",
        "-c:v",
        "mpeg4",
        "-q:v",
        "5",
        "-c:a",
        "aac",
        "-f",
        "mpegts",
        str(path),
    ]
    subprocess.run(command, check=True)


class _Handler(http.server.SimpleHTTPRequestHandler):
    records: list[tuple[str, str | None]] = []
    lock = threading.Lock()

    def __init__(self, *args, directory: str | None = None, **kwargs):
        super().__init__(*args, directory=directory, **kwargs)

    def log_message(self, format: str, *args) -> None:
        pass

    def do_GET(self) -> None:
        with self.lock:
            self.records.append((self.path, self.headers.get("Referer")))
        super().do_GET()


@pytest.fixture()
def media_server(tmp_path: Path):
    _make_segment(FFMPEG, tmp_path / "seg0.ts", 440)
    _make_segment(FFMPEG, tmp_path / "seg1.ts", 660)
    (tmp_path / "playlist.m3u8").write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                "#EXT-X-TARGETDURATION:1",
                "#EXT-X-MEDIA-SEQUENCE:0",
                "#EXTINF:1.0,",
                "seg0.ts",
                "#EXTINF:1.0,",
                "seg1.ts",
                "#EXT-X-ENDLIST",
                "",
            ]
        ),
        encoding="utf-8",
    )

    _Handler.records = []
    handler = lambda *args, **kwargs: _Handler(
        *args, directory=str(tmp_path), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}", tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _config(tmp_path: Path, referer: str) -> Path:
    path = tmp_path / "video-concat.ini"
    path.write_text(
        f"[http]\nreferer = {referer}\n[download]\nconcurrency = 2\n",
        encoding="utf-8",
    )
    return path


def test_e2e_referer_from_config(
    media_server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_url, _ = media_server
    output = tmp_path / "out.mp4"
    config = _config(tmp_path, "https://config.example/")

    code = cli.main(
        [
            f"{base_url}/playlist.m3u8",
            "-o",
            str(output),
            "--ffmpeg",
            FFMPEG,
            "--config",
            str(config),
            "--quiet",
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert output.is_file() and output.stat().st_size > 0

    paths = {path: referer for path, referer in _Handler.records}
    assert paths.get("/playlist.m3u8") == "https://config.example/"
    assert paths.get("/seg0.ts") == "https://config.example/"
    assert paths.get("/seg1.ts") == "https://config.example/"


def test_e2e_cli_header_overrides_config(
    media_server, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base_url, _ = media_server
    output = tmp_path / "out-cli.mp4"
    config = _config(tmp_path, "https://config.example/")

    code = cli.main(
        [
            f"{base_url}/playlist.m3u8",
            "-o",
            str(output),
            "--ffmpeg",
            FFMPEG,
            "--config",
            str(config),
            "--quiet",
            "-H",
            "Referer: https://cli.example/",
        ]
    )

    assert code == 0, capsys.readouterr().err
    paths = {path: referer for path, referer in _Handler.records}
    assert paths.get("/playlist.m3u8") == "https://cli.example/"
    assert paths.get("/seg0.ts") == "https://cli.example/"


_DYNAMIC_HTML = """<!doctype html>
<html lang="ja">
<meta charset="utf-8">
<title>動的プレイリスト</title>
<video id="player" controls></video>
<script>
setTimeout(() => {
  fetch('master.m3u8', {cache: 'no-store'}).then((response) => response.text());
}, 200);
</script>
</html>
"""


class _PageHandler(_Handler):
    require_cookie = False
    cookies: list[tuple[str, str | None]] = []

    def do_GET(self) -> None:
        with self.lock:
            self.records.append((self.path, self.headers.get("Referer")))
            self.cookies.append((self.path, self.headers.get("Cookie")))

        if self.path == "/dynamic.html":
            body = _DYNAMIC_HTML.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header(
                "Set-Cookie", "session=cdp-test; Path=/; HttpOnly"
            )
            self.end_headers()
            self.wfile.write(body)
            return

        if self.require_cookie and (
            self.path.endswith(".m3u8") or self.path.endswith(".ts")
        ):
            cookie = self.headers.get("Cookie") or ""
            if "session=cdp-test" not in cookie:
                self.send_error(403, "cookie required")
                return

        http.server.SimpleHTTPRequestHandler.do_GET(self)


@pytest.fixture()
def page_server(tmp_path: Path):
    v0 = tmp_path / "v0"
    v0.mkdir()
    _make_segment(FFMPEG, v0 / "seg0.ts", 440)
    _make_segment(FFMPEG, v0 / "seg1.ts", 660)
    (tmp_path / "master.m3u8").write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=160x120",
                "v0/playlist.m3u8",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (v0 / "playlist.m3u8").write_text(
        "\n".join(
            [
                "#EXTM3U",
                "#EXT-X-VERSION:3",
                "#EXT-X-TARGETDURATION:1",
                "#EXT-X-MEDIA-SEQUENCE:0",
                "#EXTINF:1.0,",
                "seg0.ts",
                "#EXTINF:1.0,",
                "seg1.ts",
                "#EXT-X-ENDLIST",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.html").write_text(
        '<video controls src="master.m3u8"></video>',
        encoding="utf-8",
    )
    (tmp_path / "dynamic.html").write_text(_DYNAMIC_HTML, encoding="utf-8")
    (tmp_path / "empty.html").write_text(
        "<html><body>no video</body></html>", encoding="utf-8"
    )

    _Handler.records = []
    _PageHandler.cookies = []
    _PageHandler.require_cookie = False
    handler = lambda *args, **kwargs: _PageHandler(
        *args, directory=str(tmp_path), **kwargs
    )
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _find_test_browser() -> str | None:
    env = os.environ.get("VIDEO_CONCAT_TEST_BROWSER")
    if env and Path(env).is_file():
        return env
    for pattern in (
        "~/.cache/ms-playwright/chromium-*/chrome-linux64/chrome",
        "~/.cache/ms-playwright/chromium-*/chrome-linux/chrome",
    ):
        matches = sorted(glob.glob(os.path.expanduser(pattern)))
        if matches:
            return matches[-1]
    for name in (
        "microsoft-edge",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "chrome",
    ):
        found = shutil.which(name)
        if found:
            return found
    return None


BROWSER = _find_test_browser()


def test_e2e_from_page_static(
    page_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    page_url = f"{page_server}/index.html"
    output = tmp_path / "from-page.mp4"

    code = cli.main(
        [
            "--from-page",
            page_url,
            "--page-mode",
            "static",
            "-o",
            str(output),
            "--ffmpeg",
            FFMPEG,
            "--quiet",
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert output.is_file() and output.stat().st_size > 0

    records = dict(_Handler.records)
    assert records.get("/master.m3u8") == page_url
    assert records.get("/v0/playlist.m3u8") == page_url
    assert records.get("/v0/seg0.ts") == page_url
    assert records.get("/v0/seg1.ts") == page_url


def test_e2e_from_page_list_candidates(
    page_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    page_url = f"{page_server}/index.html"
    output = tmp_path / "not-created.mp4"

    code = cli.main(
        [
            "--from-page",
            page_url,
            "--page-mode",
            "static",
            "--list-candidates",
            "-o",
            str(output),
        ]
    )

    captured = capsys.readouterr()
    assert code == 0, captured.err
    assert "master.m3u8" in captured.out
    assert not output.exists()


def test_e2e_from_page_no_candidates(
    page_server: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(
        [
            "--from-page",
            f"{page_server}/empty.html",
            "--page-mode",
            "static",
            "--list-candidates",
        ]
    )

    assert code == 1
    assert "検出できませんでした" in capsys.readouterr().err


@pytest.mark.skipif(
    BROWSER is None,
    reason="テスト用の Chrome / Edge / Chromium がありません",
)
def test_e2e_from_page_browser_cookie_and_referer(
    page_server: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _PageHandler.require_cookie = True
    page_url = f"{page_server}/dynamic.html"
    output = tmp_path / "browser.mp4"

    code = cli.main(
        [
            "--from-page",
            page_url,
            "--page-mode",
            "browser",
            "--browser-path",
            BROWSER,
            "--page-wait",
            "2",
            "-o",
            str(output),
            "--ffmpeg",
            FFMPEG,
            "--quiet",
        ]
    )

    assert code == 0, capsys.readouterr().err
    assert output.is_file() and output.stat().st_size > 0

    records = dict(_Handler.records)
    cookies = dict(_PageHandler.cookies)
    for path in ("/master.m3u8", "/v0/playlist.m3u8", "/v0/seg0.ts"):
        assert records.get(path) == page_url
        assert "session=cdp-test" in (cookies.get(path) or "")
