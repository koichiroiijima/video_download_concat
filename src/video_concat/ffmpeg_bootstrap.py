"""ffmpeg の探索と、見つからない場合の自動ダウンロード。"""

from __future__ import annotations

import hashlib
import logging
import lzma
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from .ffmpeg import FFmpegError, find_ffmpeg
from .ffmpeg_sources import (
    FFmpegRelease,
    available_platform_keys,
    get_release,
)
from .net import DEFAULT_USER_AGENT, DownloadError, download_file

logger = logging.getLogger(__name__)

FFMPEG_BINARY_NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"


def is_frozen() -> bool:
    """PyInstaller でビルドされた実行ファイルから動いているか。"""
    return bool(getattr(sys, "frozen", False))


def executable_dir() -> Path:
    """実行ファイルのあるディレクトリ。"""
    return Path(sys.executable).resolve().parent


def bundled_candidates() -> list[Path]:
    """同梱 ffmpeg の候補パス。"""
    candidates: list[Path] = []

    # onefile 形式の一時展開先にも対応しておく。
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / FFMPEG_BINARY_NAME)

    base = executable_dir()
    candidates.extend(
        [
            base / FFMPEG_BINARY_NAME,
            base / "ffmpeg" / FFMPEG_BINARY_NAME,
            base / "bin" / FFMPEG_BINARY_NAME,
        ]
    )
    return candidates


def _ensure_executable(path: Path) -> None:
    if os.name == "nt":
        return
    if os.access(path, os.X_OK):
        return
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError as exc:
        raise FFmpegError(
            f"ffmpeg に実行権限がありません: {path}\n"
            f"chmod +x を実行するか、--ffmpeg で別の ffmpeg を指定してください。"
        ) from exc


def locate_ffmpeg(explicit: str | Path | None = None) -> str | None:
    """明示指定、同梱、PATH の順に ffmpeg を探す。"""
    if explicit is not None:
        # 明示指定が見つからない場合は自動ダウンロードせずエラーにする。
        return find_ffmpeg(explicit)

    for candidate in bundled_candidates():
        if candidate.is_file():
            _ensure_executable(candidate)
            return str(candidate)

    return shutil.which("ffmpeg")


def _format_size(size: int) -> str:
    return f"{size / (1024 * 1024):.0f} MB"


def _manual_install_message(reason: str) -> str:
    release = get_release()
    source = release.url if release is not None else "https://ffmpeg.org/download.html"
    return (
        f"ffmpeg が見つかりません。{reason}\n"
        f"  {FFMPEG_BINARY_NAME} をこのプログラムと同じフォルダに置くか、"
        "--ffmpeg でパスを指定してください。\n"
        f"  取得元: {source}"
    )


class _DownloadProgress:
    """ffmpeg 本体のダウンロード進捗を stderr に表示する。"""

    def __init__(self, fallback_total: int, *, enabled: bool = True) -> None:
        self.total = fallback_total
        self.enabled = enabled
        self.tty = sys.stderr.isatty()
        self.last_step = -1
        self.finished = False

    def __call__(self, done: int, total: int | None) -> None:
        if not self.enabled:
            return
        if total:
            self.total = total
        if not self.total:
            step = done // (10 * 1024 * 1024)
        else:
            step = done * 100 // self.total

        if self.tty:
            percent = (
                min(100, done * 100 // self.total) if self.total else 0
            )
            print(
                f"\rffmpeg をダウンロード中... {percent}%"
                f"（{done / (1024 * 1024):.1f} MB）",
                end="",
                file=sys.stderr,
                flush=True,
            )
        elif step != self.last_step:
            percent = (
                min(100, done * 100 // self.total) if self.total else 0
            )
            print(
                f"ffmpeg をダウンロード中... {percent}%"
                f"（{done / (1024 * 1024):.1f} MB）",
                file=sys.stderr,
                flush=True,
            )
        self.last_step = step

    def finish(self) -> None:
        if self.enabled and self.tty and not self.finished:
            print(file=sys.stderr, flush=True)
        self.finished = True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _ensure_writable_dir(directory: Path) -> None:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=".video-concat-write-", dir=directory, delete=True
        ):
            pass
    except OSError as exc:
        raise FFmpegError(
            f"ffmpeg を配置できません: {directory} に書き込み権限がありません。\n"
            f"  書き込み可能なフォルダにプログラム一式を置くか、"
            f"{FFMPEG_BINARY_NAME} を手動で配置してください。"
        ) from exc


def _extract_binary(
    archive: Path, release: FFmpegRelease, destination: Path
) -> None:
    """アーカイブ内の ffmpeg 本体だけを取り出す。"""
    if release.archive_kind == "zip":
        with zipfile.ZipFile(archive) as zf:
            member = next(
                (
                    info
                    for info in zf.infolist()
                    if not info.is_dir()
                    and PurePosixPath(info.filename).name == release.binary_name
                ),
                None,
            )
            if member is None:
                raise FFmpegError(
                    f"ダウンロードしたアーカイブに {release.binary_name} がありません"
                )
            with zf.open(member) as source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        return

    if release.archive_kind == "tar.xz":
        with tarfile.open(archive, "r:xz") as tf:
            member = next(
                (
                    item
                    for item in tf.getmembers()
                    if item.isfile()
                    and PurePosixPath(item.name).name == release.binary_name
                ),
                None,
            )
            if member is None:
                raise FFmpegError(
                    f"ダウンロードしたアーカイブに {release.binary_name} がありません"
                )
            source = tf.extractfile(member)
            if source is None:
                raise FFmpegError(
                    f"ダウンロードしたアーカイブから {release.binary_name} を"
                    "読み出せませんでした"
                )
            with source, destination.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
        return

    raise FFmpegError(
        f"未対応のアーカイブ形式です: {release.archive_kind}"
    )


def _verify_binary(path: Path) -> None:
    try:
        result = subprocess.run(
            [str(path), "-version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise FFmpegError(f"ダウンロードした ffmpeg を実行できません: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        if len(detail) > 1000:
            detail = "..." + detail[-1000:]
        raise FFmpegError(
            f"ダウンロードした ffmpeg の動作確認に失敗しました"
            f"（終了コード {result.returncode}）\n{detail}"
        )


def install_ffmpeg(
    destination_dir: Path,
    *,
    release: FFmpegRelease | None = None,
    timeout: float = 60.0,
    retries: int = 3,
    show_progress: bool = True,
    verify_binary: bool = True,
    force: bool = False,
) -> Path:
    """ffmpeg をダウンロードして destination_dir に配置する。

    既に同名ファイルがある場合は、force が真でない限りそのまま返す。
    """
    destination_dir = Path(destination_dir).expanduser().resolve()

    selected = release or get_release()
    if selected is None:
        keys = ", ".join(available_platform_keys())
        raise FFmpegError(
            "このプラットフォーム用の ffmpeg 自動ダウンロード情報がありません。\n"
            f"  対応: {keys}\n"
            f"  {FFMPEG_BINARY_NAME} を手動で配置するか、--ffmpeg で指定してください。"
        )

    destination = destination_dir / selected.binary_name
    if destination.exists() and not force:
        return destination
    if destination.exists() and not destination.is_file():
        raise FFmpegError(f"ffmpeg の配置先がファイルではありません: {destination}")

    _ensure_writable_dir(destination_dir)

    print(
        f"ffmpeg が見つかりません。初回セットアップのため自動ダウンロードします"
        f"（約 {_format_size(selected.size)}）。",
        file=sys.stderr,
    )

    temp_binary: Path | None = None
    progress = _DownloadProgress(selected.size, enabled=show_progress)

    try:
        with tempfile.TemporaryDirectory(prefix="video-concat-ffmpeg-") as temp:
            archive_name = PurePosixPath(urlsplit(selected.url).path).name
            archive = Path(temp) / archive_name

            download_file(
                selected.url,
                archive,
                headers={"User-Agent": DEFAULT_USER_AGENT},
                timeout=timeout,
                retries=retries,
                progress=progress,
            )
            progress.finish()

            actual = _sha256(archive)
            if actual != selected.sha256:
                raise FFmpegError(
                    "ダウンロードした ffmpeg の SHA256 が一致しません。\n"
                    f"  期待: {selected.sha256}\n"
                    f"  実際: {actual}\n"
                    "通信経路が改ざんされていないか確認してください。"
                )

            fd, temp_name = tempfile.mkstemp(
                prefix=".video-concat-ffmpeg-",
                suffix=".exe" if os.name == "nt" else "",
                dir=destination_dir,
            )
            os.close(fd)
            temp_binary = Path(temp_name)

            _extract_binary(archive, selected, temp_binary)
            if temp_binary.stat().st_size == 0:
                raise FFmpegError("ダウンロードした ffmpeg が空ファイルです")

            if os.name != "nt":
                temp_binary.chmod(0o755)

            if verify_binary:
                _verify_binary(temp_binary)

            temp_binary.replace(destination)
            temp_binary = None
    except FFmpegError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, lzma.LZMAError, DownloadError) as exc:
        raise FFmpegError(
            "ffmpeg の自動ダウンロードに失敗しました: "
            f"{exc}\n"
            f"  {FFMPEG_BINARY_NAME} を手動で配置するか、"
            "--ffmpeg で指定してください。"
        ) from exc
    finally:
        progress.finish()
        if temp_binary is not None:
            temp_binary.unlink(missing_ok=True)

    print(f"ffmpeg を配置しました: {destination}", file=sys.stderr)
    return destination


def resolve_ffmpeg(
    *,
    explicit: str | Path | None = None,
    auto_download: bool = True,
    timeout: float = 60.0,
    retries: int = 3,
    quiet: bool = False,
) -> str:
    """ffmpeg の実行パスを解決する。必要なら自動ダウンロードする。"""
    found = locate_ffmpeg(explicit)
    if found is not None:
        return found

    if not auto_download:
        raise FFmpegError(_manual_install_message("自動ダウンロードは無効です。"))

    if not is_frozen():
        raise FFmpegError(
            _manual_install_message(
                "開発実行中は自動ダウンロードしません。"
            )
        )

    installed = install_ffmpeg(
        executable_dir(),
        timeout=timeout,
        retries=retries,
        show_progress=not quiet,
    )
    return str(installed)
