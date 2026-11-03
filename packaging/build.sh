#!/usr/bin/env bash
# video-concat の Linux 用ビルドスクリプト
# 使い方: ./packaging/build.sh
#         ./packaging/build.sh --fetch-ffmpeg
#         ./packaging/build.sh --skip-ffmpeg --no-zip
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${script_dir}/.."

if ! command -v uv >/dev/null 2>&1; then
    echo "エラー: uv が見つかりません。https://docs.astral.sh/uv/ からインストールしてください。" >&2
    exit 1
fi

echo "依存関係を同期します..."
uv sync

echo "ビルドを開始します..."
uv run python packaging/build.py "$@"

echo ""
echo "完了しました。dist/video-concat-linux-x86_64/ を確認してください。"
echo "ffmpeg が無い場合は、初回起動時に自動ダウンロードされます。"
