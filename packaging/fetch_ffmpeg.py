"""ビルド用に ffmpeg を vendor/ffmpeg へダウンロードする。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from video_concat.ffmpeg_bootstrap import install_ffmpeg  # noqa: E402
from video_concat.ffmpeg_sources import (  # noqa: E402
    available_platform_keys,
    current_platform_key,
    get_release,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="ffmpeg を vendor/ffmpeg にダウンロードします。"
    )
    parser.add_argument(
        "--platform",
        choices=available_platform_keys(),
        default=current_platform_key(),
        help="対象プラットフォーム（既定: 実行中のプラットフォーム）",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="保存先（既定: vendor/ffmpeg/<platform>）",
    )
    args = parser.parse_args()

    if args.platform is None:
        print(
            "このプラットフォーム用の ffmpeg 情報がありません。"
            "手動で配置してください。",
            file=sys.stderr,
        )
        return 1

    release = get_release(args.platform)
    if release is None:
        print(f"ffmpeg 情報がありません: {args.platform}", file=sys.stderr)
        return 1

    output = args.output or (ROOT / "vendor" / "ffmpeg" / args.platform)
    path = install_ffmpeg(
        output,
        release=release,
        show_progress=True,
        verify_binary=(args.platform == current_platform_key()),
    )
    print(f"ffmpeg を保存しました: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
