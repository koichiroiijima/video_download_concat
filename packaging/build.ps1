# video-concat の Windows 11 用ビルドスクリプト
# 使い方: .\build.ps1
#         .\build.ps1 -FetchFfmpeg
#         .\build.ps1 -SkipFfmpeg -NoZip
param(
    [switch]$FetchFfmpeg,
    [switch]$SkipFfmpeg,
    [switch]$NoZip
)

$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $PSScriptRoot
Set-Location $scriptDir

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv が見つかりません。https://docs.astral.sh/uv/ からインストールしてください。"
    exit 1
}

Write-Host "依存関係を同期します..."
& uv sync
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$buildArgs = @()
if ($FetchFfmpeg) { $buildArgs += "--fetch-ffmpeg" }
if ($SkipFfmpeg) { $buildArgs += "--skip-ffmpeg" }
if ($NoZip) { $buildArgs += "--no-zip" }

Write-Host "ビルドを開始します..."
& uv run python packaging/build.py @buildArgs
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "完了しました。dist\video-concat-windows-x86_64\ を確認してください。"
Write-Host "ffmpeg.exe が無い場合は、初回起動時に自動ダウンロードされます。"
