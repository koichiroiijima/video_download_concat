# ビルドと配布 (build)

配布版は PyInstaller の **onedir** 形式で作成します。
利用者側に Python は不要ですが、ビルドする側には Python / uv が必要です。

## 1. 対象

| OS | アーキテクチャ | 成果物 |
| --- | --- | --- |
| Windows 11 | x86_64 | `video-concat-windows-x86_64/` と `.zip` |
| Linux | x86_64 | `video-concat-linux-x86_64/` と `.zip` |

PyInstaller はクロスコンパイルできません。
**Windows 版は Windows 11 実機で `build.ps1` を実行してください。**
Linux 版は Linux 上で `build.sh` を実行します。

## 2. 事前準備

### 共通

- Python 3.10 以上
- [uv](https://docs.astral.sh/uv/)
- インターネット接続（ffmpeg を取得する場合）

```bash
uv sync
```

`uv sync` で PyInstaller と pytest が開発依存として入ります。

### ffmpeg の配置（任意）

配布物に ffmpeg を同梱する場合は、次のどちらかに置きます。

```text
vendor/ffmpeg/<platform>/ffmpeg.exe    # Windows
vendor/ffmpeg/<platform>/ffmpeg        # Linux
```

`<platform>` は `windows-x86_64` または `linux-x86_64` です。

ビルド時に自動取得する場合:

```bash
# Linux
uv run python packaging/fetch_ffmpeg.py

# Windows
uv run python packaging/fetch_ffmpeg.py
```

特定のプラットフォーム向けに取得することもできます（ビルドはできません）。

```bash
uv run python packaging/fetch_ffmpeg.py --platform windows-x86_64
```

ffmpeg を置かなかった場合もビルドは成功します。
その場合、利用者の初回起動時に自動ダウンロードされます。

## 3. Windows 11 でのビルド

PowerShell でリポジトリのルートに移動し、次を実行します。

```powershell
.\packaging\build.ps1
```

ffmpeg をビルド時に取得する場合:

```powershell
.\packaging\build.ps1 -FetchFfmpeg
```

ffmpeg を同梱せず、zip も作らない場合:

```powershell
.\packaging\build.ps1 -SkipFfmpeg -NoZip
```

`build.ps1` は次を行います。

1. `uv sync`
2. `uv run python packaging/build.py`
3. PyInstaller で `dist/video-concat/` を作成
4. `dist/video-concat-windows-x86_64/` にリネーム
5. `vendor/ffmpeg/windows-x86_64/ffmpeg.exe` があればコピー
6. `video-concat.ini`、`README.txt`、`LICENSES/` をコピー
7. `dist/video-concat-windows-x86_64.zip` を作成

## 4. Linux でのビルド

```bash
./packaging/build.sh
```

ffmpeg をビルド時に取得する場合:

```bash
./packaging/build.sh --fetch-ffmpeg
```

ffmpeg を同梱しない場合:

```bash
./packaging/build.sh --skip-ffmpeg
```

ffmpeg を同梱せず、zip も作らない場合:

```bash
./packaging/build.sh --skip-ffmpeg --no-zip
```

## 5. 配布物の構成

```text
dist/video-concat-windows-x86_64/
├── video-concat.exe
├── ffmpeg.exe                  # 同梱した場合のみ
├── video-concat.ini
├── README.txt
├── _internal/                  # Python ランタイム
└── LICENSES/
    ├── FFmpeg-LGPL-2.1.txt
    ├── FFmpeg-NOTICE.txt
    └── FFMPEG-BUILD-INFO.txt
```

Linux では `video-concat` と `ffmpeg` になります。`_internal/` を含む構成は同じです。

- `_internal/` は削除しないでください。
- フォルダ全体を zip で配布してください。
- `video-concat.exe` だけを配布すると起動できません。

## 6. ffmpeg の配布元

`src/video_concat/ffmpeg_sources.py` に固定しています。

- 配布元: [BtbN/FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds)
- バージョン: FFmpeg 8.1.3（autobuild-2026-11-02-12-51）
- ライセンス: LGPL 静的ビルド
- Linux: `ffmpeg-n8.1.3-32-g760d10603e-linux64-lgpl-8.1.tar.xz`
- Windows: `ffmpeg-n8.1.3-32-g760d10603e-win64-lgpl-8.1.zip`

SHA256 は `ffmpeg_sources.py` に記載し、自動ダウンロード時に検証します。
バージョンを更新する場合は、URL・SHA256・サイズ・`packaging/LICENSES/FFmpeg-NOTICE.txt`
の記載を更新してください。`packaging/LICENSES/FFMPEG-BUILD-INFO.txt` は
`packaging/build.py` がビルド時に生成します。

### ライセンス

本プログラムは ffmpeg を外部プロセスとして呼び出します。
`-c copy` のみを使うため、LGPL の静的ビルドで足ります。
配布物には LGPL 全文と配布元・SHA256 を同梱してください。

## 7. 動作確認チェックリスト

### Linux

```bash
./packaging/build.sh
dist/video-concat-linux-x86_64/video-concat --help
dist/video-concat-linux-x86_64/video-concat      # 引数なし → ヘルプ、終了コード 0
```

配布 zip を展開した状態でも確認します。zip は実行権限を記録しており、
`unzip` は通常それを保持します。権限が外れた場合は
`chmod +x video-concat ffmpeg` で付与してください。

```bash
unzip -q dist/video-concat-linux-x86_64.zip -d /tmp/video-concat-dist
/tmp/video-concat-dist/video-concat-linux-x86_64/video-concat --help
```

E2E:

1. ローカル HTTP サーバーで m3u8 と合成 TS を配信する。
   `tools/mock-hls-server/` を使うと Referer 検査（403）も確認できる。
2. `video-concat.ini` に Referer を設定する。
3. 配布版で実行し、MP4 が作成されることを確認する。
4. サーバー側で m3u8 と各セグメントに Referer が届くことを確認する。
5. `ffmpeg` を削除して再実行し、自動ダウンロード → 結合を確認する。
6. 読み取り専用フォルダに置いて実行し、書き込み権限エラーになることを確認する。

### Windows 11

- [ ] `.\packaging\build.ps1` が成功する
- [ ] `dist\video-concat-windows-x86_64\video-concat.exe --help` が表示される
- [ ] 引数なしでヘルプが表示され、終了コード 0 になる
- [ ] `video-concat.ini` の Referer が m3u8 とセグメントに送られる
- [ ] スペースを含むパスで動作する
- [ ] 日本語を含むパスで動作する
- [ ] `ffmpeg.exe` を削除して再実行し、自動ダウンロードされる
- [ ] 読み取り専用フォルダでは書き込み権限エラーになる
- [ ] 出力 MP4 が再生できる

## 8. トラブルシューティング

| 症状 | 確認事項 |
| --- | --- |
| `PyInstaller が見つかりません` | `uv sync` を実行したか |
| `uv が見つかりません` | uv をインストールしたか |
| Windows でビルドできない | Windows 11 実機で実行しているか |
| ffmpeg の自動ダウンロードに失敗する | インターネット接続、プロキシ、GitHub へのアクセス |
| `SHA256 が一致しません` | 通信経路、配布元の更新、`ffmpeg_sources.py` の値 |
| `書き込み権限がありません` | exe を書き込み可能なフォルダに置く |
| `ffmpeg に実行権限がありません` | `chmod +x ffmpeg` を実行するか `--ffmpeg` で指定する |
| zip 展開後に実行できない（Linux） | `chmod +x video-concat ffmpeg` を実行する |
| SmartScreen の警告 | 配布元を確認し、必要ならコード署名を検討する |
| Linux で古い glibc エラー | 古いディストリビューション上でビルドする |

## 9. リリース手順

1. `uv sync`
2. `uv run pytest`
3. `uv run python packaging/fetch_ffmpeg.py`（同梱する場合）
4. `./packaging/build.sh` または `.\packaging\build.ps1`
5. `dist/*.zip` を配布
6. `LICENSES/` と `README.txt` が入っていることを確認
7. SHA256 を公開する場合は `sha256sum dist/*.zip` で生成
