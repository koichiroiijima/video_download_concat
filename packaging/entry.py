"""PyInstaller 用のエントリポイント。"""

from video_concat.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
