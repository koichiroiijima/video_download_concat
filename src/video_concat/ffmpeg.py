"""ffmpeg の concat デマルチプレクサを使った結合処理。"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    """ffmpeg が見つからない、または結合に失敗したときに送出する。"""


def find_ffmpeg(explicit: str | Path | None = None) -> str:
    """明示指定または PATH から ffmpeg の実行パスを探す。"""
    if explicit:
        value = str(explicit)
        found = shutil.which(value)
        if found:
            return found
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)
        raise FFmpegError(f"ffmpeg が見つかりません: {explicit}")

    found = shutil.which("ffmpeg")
    if not found:
        raise FFmpegError(
            "ffmpeg が見つかりません。ffmpeg をこのプログラムと同じフォルダに置くか、"
            "--ffmpeg でパスを指定してください。\n"
            "  Windows: ffmpeg.exe を video-concat.exe と同じフォルダに置く\n"
            "  Linux:   ffmpeg を video-concat と同じフォルダに置く\n"
            "  取得元:  https://ffmpeg.org/download.html"
        )
    return found


def _concat_escape(path: Path) -> str:
    # ffmpeg の concat リストでは、シングルクォートは '\'' と書く。
    value = str(path.resolve())
    if os.name == "nt":
        # Windows のバックスラッシュは concat デマルチプレクサで
        # エスケープ扱いになることがあるため、スラッシュに揃える。
        value = value.replace("\\", "/")
    return value.replace("'", "'\\''")


def write_concat_list(paths: list[Path], list_path: Path) -> Path:
    """ffmpeg の concat デマルチプレクサ用のリストを書き出す。"""
    list_path = Path(list_path)
    list_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [f"file '{_concat_escape(Path(path))}'" for path in paths]
    list_path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return list_path


def concat_segments(
    paths: list[Path],
    output: Path,
    ffmpeg: str,
    list_path: Path,
) -> Path:
    """セグメントを ffmpeg で無劣化結合し、output に書き出す。"""
    if not paths:
        raise FFmpegError("結合するセグメントがありません")

    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    concat_list = write_concat_list(paths, list_path)

    # 失敗時に中途半端な MP4 を残さないよう、一時ファイルに書いてから置き換える。
    temp_output = output.with_name(f"{output.stem}.tmp{output.suffix}")
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        "-y",
        "-safe",
        "0",
        "-f",
        "concat",
        "-i",
        str(concat_list),
        "-c",
        "copy",
        str(temp_output),
    ]
    logger.debug("ffmpeg を実行します: %s", " ".join(command))

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        temp_output.unlink(missing_ok=True)
        stderr = (result.stderr or "").strip()
        if len(stderr) > 4000:
            stderr = "..." + stderr[-4000:]
        raise FFmpegError(
            f"ffmpeg が終了コード {result.returncode} で失敗しました。\n{stderr}"
        )

    if not temp_output.is_file() or temp_output.stat().st_size == 0:
        temp_output.unlink(missing_ok=True)
        raise FFmpegError(f"出力ファイルが作成されませんでした: {output}")

    temp_output.replace(output)
    return output
