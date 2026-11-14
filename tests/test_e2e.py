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
