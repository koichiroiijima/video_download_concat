# video-download-concat

m3u8（HLS）プレイリストを取得し、そこに記載されたセグメントを並列ダウンロードして、
ffmpeg の concat デマルチプレクサで 1 本の MP4 に結合する CLI です。
動画ページの URL から m3u8 を自動検出する `--from-page` にも対応しています。

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
- 成功するとセグメントと一時ファイルを削除し、MP4 だけを残します
  （`--save-playlist` を指定した場合は、そのコピーも残ります）。

```powershell
# 出力名と同時接続数を指定
video-concat.exe "https://example.com/video.m3u8" -o output.mp4 -c 16

# セグメントを残す
video-concat.exe "https://example.com/video.m3u8" --keep-segments

# 既存の出力を上書きする
video-concat.exe "https://example.com/video.m3u8" --force

# 取得したプレイリストを保存する
video-concat.exe "https://example.com/video.m3u8" --save-playlist playlist.m3u8
```

### 実行の流れ

1. `--from-page` を指定した場合は、Webページから m3u8 の候補を探し、1 つ選びます。
2. m3u8 を取得して解析します。マスタープレイリストの場合は `--variant` で 1 つ選びます
   （既定は `best`）。
3. セグメントを並列ダウンロードします（`--concurrency`、既定 8）。
4. `list.txt` を作成し、ffmpeg の concat デマルチプレクサで 1 本の MP4 に結合します。
5. 成功すると、セグメント・`list.txt`・`playlist.m3u8` を削除します。
   `--keep-segments` を指定すると残します。

出力 MP4 は一時ファイルに書き出してから置き換えるため、結合に失敗しても
中途半端な MP4 は残りません。

### ローカルの m3u8 を使う

ローカルの `.m3u8` ファイルを指定できます。相対 URI の取得先を配信元にしたい場合は
`--base-url` を指定します。

```bash
# Linux（Windows は ./video-concat を video-concat.exe に置き換えます）
./video-concat playlist.m3u8 \
  --base-url https://example.com/path/ \
  -o output.mp4
```

- `--base-url` を省略すると、m3u8 のあるフォルダを基準にします。
- `file://` の URL も指定できます。
- m3u8 に `EXT-X-TOKEN` が含まれる場合は、`--token-query` / `--token-header` も
  あわせて指定します。

### 動画ページから m3u8 を探す

`--from-page` に動画ページの URL を渡すと、ページの中から m3u8 を探して
ダウンロードします。

```powershell
# 見つかった候補を一覧表示（ダウンロードはしません）
video-concat.exe --from-page "https://example.com/watch/123" --list-candidates

# 候補が 1 件ならそのままダウンロード
video-concat.exe --from-page "https://example.com/watch/123" -o output.mp4

# 候補が複数あるときは番号で指定（0 始まり）
video-concat.exe --from-page "https://example.com/watch/123" --source-index 1
```

- 既定（`--page-mode auto`）は、まずページの HTML・JavaScript・iframe を
  静的に調べます。見つからない場合は Chrome / Edge / Chromium を起動して
  実際にページを開き、通信から m3u8 を探します。
- `--page-mode static` は静的解析だけ、`--page-mode browser` はブラウザだけを
  使います。
- ブラウザ方式には Chrome / Edge / Chromium のいずれかが必要です。
  見つからない場合は `--browser-path` で実行パスを指定してください。
- 候補が 1 件なら自動で選びます。複数あるときは `--source-index` で指定します。
  対話端末で実行している場合は番号を入力できます。
- Referer は自動でページの URL を設定します。ログインが必要なページでは
  `--user-data-dir` にログイン済みのブラウザプロファイルを指定すると
  開ける場合があります。
- 再生ボタンのクリックが必要な場合は `--click-selector` に CSS セレクタを
  指定します。ブラウザの画面を見ながら確認したいときは `--headed` を使います。
- `--page-wait` でページを開いてから待つ秒数を変更できます（既定 5 秒）。
- `--from-page` と `SOURCE` は同時に指定できません。`--base-url` とも併用できません。

### Referer と追加ヘッダ

配信元が Referer を要求する場合は、設定ファイルか `-H` で指定します。

配布フォルダの `video-concat.ini` を編集する場合:

```ini
[http]
referer = https://example.com/watch/123
user_agent = Mozilla/5.0 ...
```

Referer は **m3u8 本体、マスターの子プレイリスト、すべてのセグメント**に送られます。

コマンドラインで指定する場合は `-H` を使います。複数のヘッダを同時に指定できます。

```powershell
video-concat.exe "https://example.com/video.m3u8" `
  -H "Referer: https://example.com/watch/123" `
  -H "Accept: */*"
```

Linux の場合は行末のバッククォートではなく `\` で続けます。

```bash
./video-concat "https://example.com/video.m3u8" \
  -H "Referer: https://example.com/watch/123" \
  -H "Accept: */*"
```

- `-H` は設定ファイルの `referer` / `user_agent` より優先されます。
- `-H` は `NAME: VALUE` 形式です。`Cookie` なども指定できますが、秘密情報の扱いに
  注意してください。
- 配信元によっては `Referer` だけでなく `User-Agent` や `Accept` なども必要な場合が
  あります。
- `--user-agent` より `-H "User-Agent: ..."` が優先されます。

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
| `--from-page URL` | 動画ページから m3u8 を探す |
| `--page-mode MODE` | `auto` / `static` / `browser`（既定: `auto`） |
| `--page-wait SECONDS` | ブラウザでページを開いて待つ秒数（既定: 5） |
| `--list-candidates` | 見つかった候補を一覧表示して終了 |
| `--source-index N` | 使用する候補の番号（0 始まり） |
| `--browser-path PATH` | Chrome / Edge / Chromium の実行パス |
| `--headed` | ブラウザを画面表示ありで起動（確認用） |
| `--user-data-dir PATH` | ブラウザのユーザーデータディレクトリ |
| `--click-selector CSS` | クリックする要素の CSS セレクタ |
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

### うまくいかないとき

| 症状 | 確認すること |
| --- | --- |
| `HTTP 403 Forbidden` | Referer が配信元のページと一致しているか確認してください。`-H "Referer: ..."` または `[http] referer` で指定します。`User-Agent` や `Accept` が必要な場合もあります。Cloudflare などのボット対策が有効な配信元では、Referer を付けても取得できないことがあります。 |
| `Chrome / Edge / Chromium が見つかりません` | `--browser-path` で実行パスを指定するか、ブラウザをインストールしてください。 |
| ページから m3u8 が見つからない | `--list-candidates` で候補を確認してください。`--page-mode browser`、`--headed`、`--click-selector` も試してください。 |
| 候補が複数ある | `--source-index` で番号を指定してください（`--list-candidates` で番号を確認できます）。 |
| `EXT-X-TOKEN` の警告が出る | `--token-query NAME` または `--token-header NAME` で付与方法を指定してください。付与方法は配信元ごとに異なります。 |
| セグメントの一部が失敗する | `--retries` を増やす、`--concurrency` を下げる、時間をおいて再実行してください。取得済みのセグメントは再利用されます。 |
| `ffmpeg が見つかりません` | `--ffmpeg PATH` を指定するか、exe と同じフォルダに ffmpeg を置いてください。ソース実行では自動ダウンロードされません。 |
| `出力ファイルが既に存在します` | `--force` を付けるか、`-o` で別の名前を指定してください。 |
| 詳細なログを確認したい | `-v` を指定してください。 |
| 進捗や警告を表示したくない | `-q` を指定してください。 |

## 2. 開発者向けの実行

必要なもの:

- Python 3.10 以上
- [uv](https://docs.astral.sh/uv/)
- ffmpeg（ソース実行時は `PATH` 上にあるか `--ffmpeg` で指定）
- Chrome / Edge / Chromium（`--from-page` のブラウザ方式を使う場合のみ）
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

ブラウザ方式の E2E（`--from-page`）は、ヘッドレスブラウザが見つかる環境でのみ
実行されます。`VIDEO_CONCAT_TEST_BROWSER` で実行パスを指定できます（未指定なら
Playwright の Chromium キャッシュ、`PATH` 上の Chrome / Edge / Chromium を探します）。

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
- 配信元によっては Cloudflare などのボット対策により、Referer やヘッダを指定しても
  取得できない場合があります。本ツールはボット対策を回避する機能を持ちません。
- Cookie やセッショントークンが必要な配信元は、`-H` や `--token-query` /
  `--token-header` で対応できる範囲に限られます。
- `--from-page` の候補検出はページの作りに依存します。動画が別オリジンの
  iframe 内にある場合や、独自の難読化をしている場合は見つからないことがあります。
- ブラウザ方式は Chrome / Edge / Chromium が必要です（配布物には同梱していません）。
  ブラウザ方式の E2E テストは、ヘッドレスブラウザが見つかる環境でのみ実行されます
  （見つからない場合はスキップ）。

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
