# video-download-concat

m3u8（HLS）プレイリストを取得し、そこに記載されたセグメントを並列ダウンロードして、
ffmpeg の concat デマルチプレクサで 1 本の MP4 に結合する CLI です。

`list.cmd` が手動で行っていた「`.jpeg` → `.ts` リネーム、連番ゼロ埋め、`list.txt` 作成、
ffmpeg 結合、セグメント削除」をまとめて自動化します。

配布版は **Python のインストール不要**のフォルダ型実行ファイルです。
Windows 11 と Linux（x86_64）向けの配布版を用意しています
（Windows 11 実機での検証は未実施です）。

## 1. 配布版の使い方

配布フォルダには次のファイルが入っています。

```text
video-concat-windows-x86_64/
├── video-concat.exe        # プログラム本体（Linux では video-concat）
├── ffmpeg.exe              # ffmpeg（Linux では ffmpeg。無い場合は初回起動時に自動ダウンロード）
├── video-concat.ini        # 設定ファイル
├── README.txt
├── _internal/              # Python ランタイム（削除しないでください）
└── LICENSES/               # ffmpeg のライセンス情報
```

> フォルダ全体を移動してください。`video-concat.exe` だけを移動すると起動できません。

Linux の配布 zip は実行権限を記録しています。`unzip` で展開すると通常は保持されますが、
権限が外れた場合は `chmod +x video-concat ffmpeg` を実行してください。

### 基本操作

Windows（コマンドプロンプト / PowerShell）:

```powershell
video-concat.exe "https://example.com/video.m3u8"
```

Linux:

```bash
./video-concat "https://example.com/video.m3u8"
```

- 出力名を省略すると `YYYYmmddHHMMSS.mp4` になります。
- 引数なしで実行するとヘルプを表示します。
- 成功するとセグメントと一時ファイルを削除し、MP4 だけを残します。

```powershell
# 出力名と同時接続数を指定
video-concat.exe "https://example.com/video.m3u8" -o output.mp4 -c 16

# セグメントを残す
video-concat.exe "https://example.com/video.m3u8" --keep-segments

# 既存の出力を上書きする
video-concat.exe "https://example.com/video.m3u8" --force
```

### Referer の設定

配布フォルダの `video-concat.ini` を編集します。

```ini
[http]
referer = https://example.com/watch/123
```

Referer は **m3u8 本体、マスターの子プレイリスト、すべてのセグメント**に送られます。
コマンドラインで上書きする場合は `-H` を使います。

```powershell
video-concat.exe "https://example.com/video.m3u8" -H "Referer: https://example.com/"
```

### 設定ファイル

`video-concat.ini` は exe と同じフォルダに置きます。
`--config PATH` で別のファイルを指定することもできます。

```ini
[http]
referer = https://example.com/
user_agent = Mozilla/5.0 ...

[download]
concurrency = 8
retries = 3
timeout = 30

[playlist]
variant = best
base_url =
token_query =
token_header =

[output]
path =
work_dir =
save_playlist =

[behavior]
keep_segments = false
force = false

[ffmpeg]
path =
auto_download = true
```

- 空欄の項目は既定値を使います。
- 設定ファイル内の相対パスは、設定ファイルのあるフォルダ基準です。
- コマンドライン引数 > 設定ファイル > 既定値 の順で優先されます。
- 設定ファイルがない場合は既定値で動作します。

### ffmpeg の配置と自動ダウンロード

1. `--ffmpeg PATH` または `ffmpeg.path` が指定されていれば、それを使います。
2. 指定がなければ、**exe と同じフォルダ**の `ffmpeg.exe` / `ffmpeg` を探します
   （`ffmpeg/`、`bin/` サブフォルダも対象です）。
3. 見つからなければ `PATH` 上の ffmpeg を探します。
4. それでも見つからず `auto_download = true` なら、初回起動時に自動ダウンロードします。

自動ダウンロードの内容:

- 固定バージョンの LGPL 静的ビルドを取得
- SHA256 を検証
- `ffmpeg.exe` / `ffmpeg` を exe と同じフォルダに配置
- Linux では実行権限を付与
- `ffmpeg -version` で動作確認

自動ダウンロードを無効にする場合:

```ini
[ffmpeg]
auto_download = false
```

またはコマンドラインで `--no-auto-download` を指定します。

> exe と同じフォルダに書き込み権限がない場合（`Program Files` 配下など）は、
> 自動配置できずエラーになります。書き込み可能なフォルダに置くか、
> `ffmpeg.exe` を手動で配置してください。

### 主なオプション

| オプション | 説明 |
| --- | --- |
| `--config PATH` | 設定ファイルを指定 |
| `-o, --output` | 出力ファイル名（既定: `YYYYmmddHHMMSS.mp4`） |
| `--work-dir` | セグメントの保存先（既定: `<出力名>_segments`） |
| `--base-url` | 相対 URI を解決する基準 URL（ローカル m3u8 用） |
| `-c, --concurrency` | 同時ダウンロード数（既定: 8） |
| `--retries` | 1 セグメントあたりの試行回数（既定: 3） |
| `--timeout` | HTTP タイムアウト秒（既定: 30） |
| `-H, --header` | 追加 HTTP ヘッダ（複数指定可） |
| `--user-agent` | User-Agent（既定: ブラウザ相当） |
| `--token-query` | `EXT-X-TOKEN` をクエリパラメータとして付与 |
| `--token-header` | `EXT-X-TOKEN` を HTTP ヘッダとして送信 |
| `--variant` | マスタープレイリストの Variant（0 始まり、既定: `best`） |
| `--ffmpeg` | ffmpeg の実行パス |
| `--auto-download / --no-auto-download` | ffmpeg の自動ダウンロードを切り替え |
| `--keep-segments / --no-keep-segments` | 結合後にセグメント・`list.txt`・`playlist.m3u8` を残す |
| `--force / --no-force` | 既存の出力・セグメントを上書き |
| `--save-playlist` | 取得したメディアプレイリストのコピー先 |
| `-v, --verbose` | デバッグログを表示 |
| `-q, --quiet` | 進捗と警告を抑制 |
| `--version` | バージョンを表示 |

## 2. 開発者向けの実行

必要なもの:

- Python 3.10 以上
- [uv](https://docs.astral.sh/uv/)
- ffmpeg（ソース実行時は `PATH` 上にあるか `--ffmpeg` で指定）
- Node.js 18 以上（モック HLS サーバーを使う場合のみ。npm パッケージは不要）

```bash
uv sync
uv run video-concat --help
uv run video-concat "https://example.com/video.m3u8" --ffmpeg /path/to/ffmpeg
```

ソース実行では ffmpeg の自動ダウンロードは行いません。
`--ffmpeg` を指定するか、`PATH` に ffmpeg を入れてください。

### テスト

```bash
uv sync
uv run pytest
```

E2E テストは ffmpeg が必要です。`vendor/ffmpeg/linux-x86_64/ffmpeg` があるか、
`VIDEO_CONCAT_TEST_FFMPEG` でパスを指定するか、`PATH` に ffmpeg を入れてください。

### モック HLS サーバー

実配信元を使わずに、HTTP ダウンロードと Referer 検査を確認できます。
Node.js 18 以上が必要です（npm パッケージのインストールは不要）。

```bash
# 1. テスト用の m3u8 と TS を生成（ffmpeg が必要）
node tools/mock-hls-server/generate-media.mjs

# 2. Referer を指定して起動
node tools/mock-hls-server/server.mjs --referer https://example.com/

# 3. 別のターミナルで実行
uv run video-concat "http://127.0.0.1:8080/master.m3u8" \
  -H "Referer: https://example.com/" \
  --ffmpeg vendor/ffmpeg/linux-x86_64/ffmpeg \
  -o /tmp/mock-out.mp4
```

Referer がない場合・一致しない場合は 403 になり、`video-concat` もエラー終了します。
詳細は [tools/mock-hls-server/README.md](tools/mock-hls-server/README.md) を参照してください。

## 3. ビルド

ビルドには Python / uv が必要です（利用者側は不要）。

### Windows 11

Windows 11 実機で PowerShell から実行します。

```powershell
.\packaging\build.ps1
```

ffmpeg をビルド時に取得する場合:

```powershell
.\packaging\build.ps1 -FetchFfmpeg
```

### Linux

```bash
./packaging/build.sh
```

ビルド時に ffmpeg を取得する場合:

```bash
./packaging/build.sh --fetch-ffmpeg
```

### 配布物

```text
dist/video-concat-windows-x86_64/
dist/video-concat-windows-x86_64.zip
# または
dist/video-concat-linux-x86_64/
dist/video-concat-linux-x86_64.zip
```

`vendor/ffmpeg/<platform>/ffmpeg[.exe]` がある場合は配布フォルダに同梱します。
ない場合は初回起動時の自動ダウンロードに任せます。

Windows 版は Linux からクロスビルドできません。必ず Windows 11 実機で
`build.ps1` を実行してください。

## 4. `list.cmd` との対応

| list.cmd | video-concat |
| --- | --- |
| `*.jpeg` を `*.ts` にリネーム | 拡張子に関係なく、実体を `0000.ts` として保存 |
| `video1.ts` → `video0001.ts` のゼロ埋め | プレイリスト順のゼロ埋め連番で保存 |
| 名前順に `list.txt` を作成 | `list.txt` をプレイリスト順で自動生成 |
| `ffmpeg -safe 0 -f concat -i list.txt -c copy` | 同じコマンドで結合 |
| 出力名 `yyyyMMddHHmmss.mp4` | 既定で同じ形式のファイル名 |
| `del *.ts` | 成功後にセグメントを削除（`--keep-segments` で保持） |

`list.cmd` はファイル名のソートに依存しているため、ゼロ埋めが重要でした。
本ツールはプレイリストに書かれた順番をそのまま使うので、名前の衝突や欠番があっても順序が崩れません。

## 5. 制限事項

- AES-128 / SAMPLE-AES などの暗号化 HLS には未対応です。
- fMP4（`EXT-X-MAP`）には未対応です。MPEG-TS のプレイリストを対象にしています。
- `EXT-X-BYTERANGE` には未対応です。
- 分離された音声トラック（`EXT-X-MEDIA:TYPE=AUDIO`）には未対応です。
  該当するタグを検出すると警告を表示し、そのまま処理を続けます。
- `EXT-X-TOKEN` は非標準タグです。値の使い方は配信元によって異なるため、
  `--token-query` または `--token-header` で指定してください。未指定の場合は無視されます。
- ライブプレイリスト（`EXT-X-ENDLIST` なし）は、実行時点の内容だけをダウンロードします。
- 自動ダウンロードにはインターネット接続が必要です。遮断される環境では
  `ffmpeg.exe` / `ffmpeg` を手動で配置してください。

## 6. 注意

- ダウンロードする権利のあるコンテンツにのみ使用してください。
- 配信元の利用規約や著作権を確認し、過度な同時接続は避けてください。
- Windows の SmartScreen が警告を表示する場合があります。配布元を確認して実行してください。
- ffmpeg は LGPL の静的ビルドを使用しています。詳細は `packaging/LICENSES/` を参照してください。

## 7. ドキュメント

- [docs/design.md](docs/design.md) — 実装とデータフロー
- [docs/details.md](docs/details.md) — 外部仕様とテスト
- [docs/build.md](docs/build.md) — ビルドと配布の詳細
- [handover.md](handover.md) — 現状と次の作業
