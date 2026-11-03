"""Linux / Windows 用の配布フォルダを作るビルドスクリプト。"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
DIST = ROOT / "dist"
BUILD = ROOT / "build"
VENDOR = ROOT / "vendor" / "ffmpeg"

sys.path.insert(0, str(SRC))

from video_concat.ffmpeg_bootstrap import FFMPEG_BINARY_NAME, install_ffmpeg  # noqa: E402
from video_concat.ffmpeg_sources import (  # noqa: E402
    current_platform_key,
    get_release,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="video-concat の配布フォルダと zip を作成します。"
    )
    parser.add_argument(
        "--platform",
        choices=["linux-x86_64", "windows-x86_64"],
        help="対象プラットフォーム（既定: 実行中のプラットフォーム）",
    )
    parser.add_argument(
        "--fetch-ffmpeg",
        action="store_true",
        help="ビルド前に ffmpeg を vendor/ffmpeg へダウンロードする",
    )
    parser.add_argument(
        "--skip-ffmpeg",
        action="store_true",
        help="ffmpeg を配布フォルダへコピーしない",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="zip を作成しない",
    )
    return parser.parse_args()


def _check_platform(requested: str | None) -> str:
    current = current_platform_key()
    if current is None:
        raise SystemExit(
            "このプラットフォームはビルド対象外です。"
            "Linux x86_64 または Windows x86_64 で実行してください。"
        )
    if requested is not None and requested != current:
        raise SystemExit(
            "異なるプラットフォーム向けのビルドはできません。\n"
            f"  実行中: {current}\n"
            f"  指定:   {requested}\n"
            "Windows 版は Windows 11 実機で build.ps1 を実行してください。"
        )
    return current


def _find_vendor_ffmpeg(platform_key: str) -> Path | None:
    candidates = [
        VENDOR / platform_key / FFMPEG_BINARY_NAME,
        VENDOR / FFMPEG_BINARY_NAME,
        ROOT / "packaging" / "ffmpeg" / FFMPEG_BINARY_NAME,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _run_pyinstaller() -> Path:
    if shutil.which("pyinstaller") is None:
        # PyInstaller は uv sync で dev 依存として入る。
        try:
            import PyInstaller  # noqa: F401
        except ImportError as exc:
            raise SystemExit(
                "PyInstaller が見つかりません。先に `uv sync` を実行してください。"
            ) from exc

    DIST.mkdir(parents=True, exist_ok=True)
    BUILD.mkdir(parents=True, exist_ok=True)

    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name",
        "video-concat",
        "--paths",
        str(SRC),
        "--distpath",
        str(DIST),
        "--workpath",
        str(BUILD),
        "--specpath",
        str(BUILD),
        str(ROOT / "packaging" / "entry.py"),
    ]
    print("PyInstaller を実行します:")
    print("  " + " ".join(command))
    subprocess.run(command, cwd=ROOT, check=True)

    output = DIST / "video-concat"
    if not output.is_dir():
        raise SystemExit(f"PyInstaller の出力が見つかりません: {output}")
    return output


def _copy_distribution_files(target: Path) -> None:
    shutil.copy2(ROOT / "packaging" / "video-concat.ini", target / "video-concat.ini")
    shutil.copy2(ROOT / "packaging" / "README-dist.txt", target / "README.txt")

    licenses = ROOT / "packaging" / "LICENSES"
    if licenses.is_dir():
        shutil.copytree(licenses, target / "LICENSES", dirs_exist_ok=True)


def _write_build_info(target: Path, platform_key: str) -> None:
    release = get_release(platform_key)
    licenses = target / "LICENSES"
    licenses.mkdir(parents=True, exist_ok=True)
    if release is None:
        text = (
            "ffmpeg は同梱されていません。\n"
            "初回起動時に自動ダウンロードするか、手動で配置してください。\n"
        )
    else:
        text = (
            f"FFmpeg: {release.version}\n"
            f"配布元: {release.url}\n"
            f"SHA256: {release.sha256}\n"
            f"ライセンス: LGPL（BtbN/FFmpeg-Builds の LGPL 静的ビルド）\n"
        )
    (licenses / "FFMPEG-BUILD-INFO.txt").write_text(text, encoding="utf-8")


def main() -> int:
    args = _parse_args()
    platform_key = _check_platform(args.platform)

    if args.fetch_ffmpeg:
        release = get_release(platform_key)
        if release is None:
            raise SystemExit("指定プラットフォームの ffmpeg 情報がありません。")
        install_ffmpeg(
            VENDOR / platform_key,
            release=release,
            show_progress=True,
            verify_binary=(platform_key == current_platform_key()),
        )

    built = _run_pyinstaller()

    target = DIST / f"video-concat-{platform_key}"
    if target.exists():
        shutil.rmtree(target)
    built.replace(target)

    if not args.skip_ffmpeg:
        ffmpeg = _find_vendor_ffmpeg(platform_key)
        if ffmpeg is None:
            print(
                "注意: ffmpeg が見つかりません。配布物には含めません。\n"
                "  初回起動時に自動ダウンロードされます。\n"
                f"  手動で置く場合: {VENDOR / platform_key / FFMPEG_BINARY_NAME}",
                file=sys.stderr,
            )
        else:
            destination = target / FFMPEG_BINARY_NAME
            shutil.copy2(ffmpeg, destination)
            if os.name != "nt":
                destination.chmod(0o755)
            print(f"ffmpeg を同梱しました: {destination}")

    _copy_distribution_files(target)
    _write_build_info(target, platform_key)

    if not args.no_zip:
        zip_base = DIST / f"video-concat-{platform_key}"
        zip_path = zip_base.with_suffix(".zip")
        zip_path.unlink(missing_ok=True)
        shutil.make_archive(
            str(zip_base), "zip", root_dir=DIST, base_dir=target.name
        )
        print(f"zip を作成しました: {zip_path}")

    print(f"配布フォルダ: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
