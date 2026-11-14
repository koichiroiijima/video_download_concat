# 詳細設計 (design)

開発者向けの実装・データフロー・設計判断をまとめます。
外部仕様やテストは [details.md](details.md)、ビルドと配布は [build.md](build.md) を参照してください。

## 1. 全体アーキテクチャ

CLI が m3u8 を取得・解析し、セグメントを並列ダウンロードしたあと、
ffmpeg の concat デマルチプレクサで MP4 に結合します。
`--from-page` を指定した場合は、先に Webページから m3u8 の候補を探します。
配布版では PyInstaller の onedir 形式で Python ランタイムを同梱し、
ffmpeg は exe と同じフォルダに配置します。

```
設定ファイル video-concat.ini
   │
   ▼
cli.main
   ├─ config.find_config / load_config
   ├─ 引数と設定のマージ（CLI > 設定 > 既定）
   └─ cli.run
        │
        ├─ cli._discover_page（--from-page のとき）
        │    ├─ page_source.discover_static（HTML / JS / iframe の静的解析）
        │    └─ browser_cdp.discover_browser
        │    │    ├─ Chrome / Edge / Chromium を起動
        │    │    ├─ ws.WebSocket で CDP に接続
        │    │    └─ Network イベントから m3u8 を収集
        │    └─ page_source.select_candidate（番号指定 / 対話選択）
        │
        ├─ cli._load_playlist
        │    ├─ net.fetch_text_with_url / Path.read_text
        │    ├─ m3u8.parse_playlist
        │    └─ マスタープレイリストなら select_variant → 再帰取得
        │
        ├─ ffmpeg_bootstrap.resolve_ffmpeg
        │    ├─ --ffmpeg / 設定の path
        │    ├─ exe と同じフォルダ（_MEIPASS も対応）
        │    ├─ PATH
        │    └─ 見つからなければ install_ffmpeg（配布版のみ初回自動ダウンロード）
        │
        ├─ downloader.download_segments
        │    ├─ ThreadPoolExecutor で並列取得
        │    └─ net.download_file（.part → atomic rename、リトライ付き）
        │
        ├─ ffmpeg.write_concat_list → ffmpeg.concat_segments
        │
        └─ 出力 MP4（成功時はセグメントを削除）
```

## 2. ディレクトリ構成

```
.
├── README.md                 利用者向けの概要・配布版の使い方
├── requirement.md            要求事項
├── pyproject.toml            uv / hatchling / 開発依存の設定
├── uv.lock                   uv ロックファイル
├── list.cmd                  旧来の Windows 後処理スクリプト（既存ファイル）
├── docs/
│   ├── design.md             本ファイル
│   ├── details.md            外部仕様・テスト
│   └── build.md              ビルド・配布・ffmpeg 同梱の詳細
├── handover.md               次セッション向け引き継ぎ
├── AGENTS.md                 AI エージェント向け作業規約
├── opencode.json             OpenCode の instructions 設定
├── packaging/
│   ├── entry.py              PyInstaller のエントリポイント
│   ├── build.py              共通ビルド処理・zip 作成
│   ├── build.ps1             Windows 11 用ビルド
│   ├── build.sh              Linux 用ビルド
│   ├── fetch_ffmpeg.py       ビルド時の任意ダウンロード
│   ├── video-concat.ini      配布用設定ファイル
│   ├── README-dist.txt       配布用 README
│   └── LICENSES/             ffmpeg のライセンス
├── tools/mock-hls-server/
│   ├── server.mjs            Referer 検査付きモック HLS サーバー
│   ├── generate-media.mjs    ffmpeg でテスト用 TS を生成
│   ├── package.json          npm スクリプト（依存パッケージなし）
│   └── README.md             使い方
├── src/video_concat/
│   ├── __init__.py
│   ├── __main__.py           python -m video_concat の入口
│   ├── cli.py                argparse・設定マージ・全体フロー
│   ├── config.py             video-concat.ini の読み込み
│   ├── headers.py            HTTP ヘッダの解析・検証
│   ├── m3u8.py               プレイリストパーサー
│   ├── net.py                HTTP 取得・リトライ・保存
│   ├── downloader.py         並列ダウンロード・進捗表示
│   ├── ffmpeg.py             concat リスト作成・ffmpeg 実行
│   ├── ffmpeg_bootstrap.py   ffmpeg 探索・自動ダウンロード
│   ├── ffmpeg_sources.py     配布元 URL・SHA256 の定義
│   ├── page_source.py        Webページから m3u8 を探索（静的解析・候補選択）
│   ├── browser_cdp.py        Chrome / Edge を CDP で操作して m3u8 を検出
│   └── ws.py                 CDP 用の最小 WebSocket クライアント
└── tests/
    ├── test_cli.py
    ├── test_config.py
    ├── test_headers.py
    ├── test_ffmpeg_bootstrap.py
    ├── test_page_source.py
    ├── test_browser_cdp.py
    ├── test_ws.py
    └── test_e2e.py
```

実データの m3u8（`EXT-X-TOKEN` を含むもの）はリポジトリに含めない。
動作確認には `tools/mock-hls-server/` で生成したプレイリストや、
`--save-playlist` で保存したプレイリストを使う。

## 3. 実装詳細

### `config.py`

- `AppConfig` は設定ファイルの値を保持する frozen dataclass。未指定は `None`。
- `find_config()` の探索順:
  1. `--config PATH`（存在しなければエラー）
  2. 配布版では exe と同じフォルダの `video-concat.ini`
  3. カレントディレクトリの `video-concat.ini`
- `load_config()`:
  - `ConfigParser(interpolation=None)` を使う。Referer に `%20` などが含まれても壊れない。
  - UTF-8（BOM 付きも可）で読む。
  - 未知のセクション・キーは警告して無視する。
  - 数値・真偽値・URL スキーム・ヘッダ名を検証する。
  - 設定ファイル内の相対パスは、設定ファイルのあるフォルダ基準で解決する。

### `headers.py`

- `parse_header_items()` は `NAME: VALUE` 形式を辞書にする。
- ヘッダ名は RFC 7230 の token 文字に限定する。
- ヘッダ値の CR/LF を拒否し、ヘッダインジェクションを防ぐ。
- `canonical_header()` で `x-api-key` → `X-Api-Key` のように表記を揃える。

### `cli.py`

- `argparse` で全オプションを定義。値の既定は `None` にし、
  「CLI で指定されたか」を設定ファイルとのマージで判定する。
- `_validate_args()` は CLI で明示された値だけを検証する。
  設定ファイルの値は `load_config()` で検証済み。
- `run()` で実効値を決める。優先順位は **CLI > 設定ファイル > 既定値**。
- ヘッダのマージ:
  1. 設定ファイルの `referer` を `Referer` に設定
  2. `--user-agent` または設定の `user_agent` を `User-Agent` に設定
  3. `-H` のヘッダで上書き
- `--keep-segments` / `--force` は `BooleanOptionalAction` にし、
  設定ファイルの true を CLI の `--no-...` で打ち消せる。
- ffmpeg の解決はプレイリスト検証後、セグメントダウンロード前に行う。
  不正な URL で不要な自動ダウンロードをしないため。
- 引数なしの場合は `parser.print_help()` を表示して終了コード 0。
- Windows のコンソール向けに stdout / stderr を UTF-8 に再設定する。

### `ffmpeg_bootstrap.py`

- `locate_ffmpeg()` の探索順:
  1. 明示指定（`--ffmpeg` / 設定の `ffmpeg.path`）
  2. `_MEIPASS` 直下（onefile 形式に備える）
  3. exe と同じフォルダ、`ffmpeg/`、`bin/`
  4. `PATH`
- 明示指定が見つからない場合は自動ダウンロードしない。
- `resolve_ffmpeg()` は見つからない場合、`auto_download` が有効なら
  `install_ffmpeg()` を呼ぶ。自動ダウンロードは配布版（`sys.frozen`）のみ。
- `install_ffmpeg()`:
  - 配置先に書き込みできるか実際に一時ファイルを作って確認する。
    書き込み不可ならエラーにし、ユーザーデータ領域などへのフォールバックはしない。
  - `net.download_file()` で `.part` にダウンロードし、SHA256 を検証する。
  - zip / tar.xz から `ffmpeg` 本体だけを選んで取り出す。
    `extractall` は使わない（パストラバーサル対策）。
  - 一時ファイルに展開し、Linux では `0o755` を付与する。
  - `ffmpeg -version` で動作確認してから atomic にリネームする。
- 配布元 URL・SHA256・サイズは `ffmpeg_sources.py` に固定している。

### `net.py`

- 標準ライブラリ `urllib.request` のみ使用。
- `fetch_text()` / `fetch_text_with_url()` と `download_file()` は指数バックオフでリトライ。
- `fetch_text_with_url()` はリダイレクト後の最終 URL も返し、プレイリストの相対 URI はその URL を基準に解決する。
- リトライ対象: 408 / 425 / 429 / 500 / 502 / 503 / 504、URLError、タイムアウト、HTTPException。
- `download_file()` は `*.part` に書き、Content-Length と一致したら atomic にリネーム。
- `download_file()` は任意の進捗コールバックを受け取れる。ffmpeg 本体の
  ダウンロード進捗に使う。
- `fetch_text()` / `fetch_text_with_url()` は任意の `max_bytes` を受け取れる。
  指定すると `max_bytes + 1` バイトまで読み、超過したら
  `DownloadError`（`応答が大きすぎます（上限 N バイト）`）にして即座に失敗する。
  この例外はリトライ対象に含めない。未指定なら上限なしで全体を読む。
- `Accept-Encoding: identity` を送り、Content-Length と実バイト数を比較できるようにする。

### `downloader.py`

- `ThreadPoolExecutor` で `--concurrency` 件を並列取得。
- 出力名は `0000.ts`, `0001.ts`, ... のゼロ埋め連番。
  - プレイリスト順をそのまま使うため、元ファイル名の大小関係に依存しない。
  - 拡張子が `.jpeg` でも TS として `.ts` に保存する（`list.cmd` と同じ考え方）。
- 既存ファイルが非空なら再利用（再開）。`--force` で再取得。
- 1 件でも失敗したら待機中のタスクをキャンセルし、失敗内容をまとめて報告。
- 進捗は stderr。TTY なら 1 行を上書き、非 TTY なら 10% 刻み。

### `ffmpeg.py`

- `find_ffmpeg()`: 明示指定 → `PATH` の順で探索。見つからなければ日本語で案内。
- `write_concat_list()`: `file '/absolute/path'` 形式。
  - パス内の `'` は `'\''` にエスケープ。
  - Windows ではバックスラッシュを `/` に正規化する。
  - UTF-8 / LF で書き出す。
- `concat_segments()`:
  - `ffmpeg -hide_banner -loglevel warning -nostdin -y -safe 0 -f concat -i list.txt -c copy <一時ファイル>`
  - 成功時に一時ファイルを出力へ atomic にリネームする（失敗時に中途半端な MP4 を残さない）。
  - 失敗時は stderr の末尾 4000 文字を例外に含める。

### `m3u8.py`

- `Variant`: マスタープレイリスト内の 1 ストリーム。
- `Segment`: セグメント URI、解決済み URL、EXTINF、`EXT-X-TOKEN` を保持。
- `Playlist`: 解析結果。`variants` があればマスター、`segments` があればメディア。
- `parse_playlist()`:
  - `#EXT-X-STREAM-INF` の次の URI を Variant として扱う。
  - `#EXT-X-TOKEN` / `#EXT-X-TOKEN=` の両形式に対応。
  - 相対 URI は `urljoin` で m3u8 の URL 基準に解決。
  - `#EXT-X-ENDLIST` が複数回現れる壊れたプレイリストでも、URI 行を順に拾う。
  - `4000,` のような URI らしくない行は警告してスキップ。
  - `EXTINF` のない URI 行は警告しつつセグメントとして採用（欠損の救済）。
- `select_variant()`: `best` は BANDWIDTH → 解像度の順で最大を選ぶ。数値指定は 0 始まり。
- `apply_token()`: `EXT-X-TOKEN` をクエリパラメータとして付与する。

### `page_source.py`

- Webページ（HTML・インライン JavaScript・同一オリジンの外部スクリプト・iframe）を
  静的に走査し、m3u8 URL の候補を抽出する。JavaScript は実行しない。
- `cli._discover_page` から呼ばれる（`--page-mode static`、または `auto` の
  1 段目）。ブラウザ方式は `browser_cdp.py` が担当する。
- `extract_m3u8_urls()`:
  - `\/`・`\uXXXX`・`\xXX` と HTML エンティティを戻してから走査する。
  - 絶対 / プロトコル相対、引用符内の相対、ルート相対の 3 種類を正規表現で拾う。
  - `http://` / `https://` のみ残し、`#fragment` を落として重複排除、出現順を保つ。
  - 末尾の `),.;]}>` などは URL から除去する。
  - 難読化された JavaScript は先に展開してから走査する
    （`decode_obfuscated_javascript()`、後述）。
- `decode_obfuscated_javascript()`（`extract_m3u8_urls()` から利用）:
  - Dean Edwards の packer（`eval(function(p,a,c,k,e,d){...}('...',a,c,
    '...'.split('|'),0,{}))`）を静的に展開する。JavaScript は実行しない。
  - `_PACKER_CALL_RE` は引数リスト先頭の文字列だけを対象にし、
    関数本体の `new RegExp('\b'+...)` などを取り違えない。
  - `_unpack()` はキーワード表を基数 `a`（`0-9a-zA-Z`、最大 62）で引き、
    `\b\w+\b` の各トークンを置換する。
  - `_unescape_js()` は文字列リテラル内の `\'`・`\\`・`\xXX`・`\uXXXX`・
    `\u{...}`・8 進を戻す。
  - 多重化に備えて最大 3 段まで、1 つのテキストから最大 20 呼び出しまで展開する。
  - 展開はエスケープ解釈より先に行う（`\u0027` などでリテラルが壊れないように）。
- `_Collector`（内部）:
  - 検出元ラベルは `インラインJS`、`HTML`、`スクリプト: <URL>`。
  - 外部スクリプトは**同一オリジン**のみ取得する。iframe は `http` / `https` なら辿る。
  - リソース取得時は `Referer` を親文書の URL に既定設定し、`retries=1` にする。
  - 上限は文書 5・外部スクリプト 10・候補 50・リソース 2 MiB・iframe 深さ 2。
- `discover_static()`:
  - ページを取得し（`max_bytes=2 MiB`）、応答自体が `#EXTM3U` で始まる場合は
    検出元「ページ自体」として 1 件返す。
  - それ以外は収集した候補を `probe_candidates()` で検証する。
  - 候補の検証時は `Referer` を最終ページ URL に既定設定する。
- `probe_candidates()`:
  - 候補を実際に取得し（`timeout` は最大 `PROBE_TIMEOUT` = 10 秒、`retries=1`）、
    BOM・空白を除いた先頭が `#EXTM3U` か確認して `parse_playlist()` に渡す。
  - 失敗理由は `SourceCandidate.error` に残す。並列数は既定 8
    （`ThreadPoolExecutor`）。
- `select_candidate()`:
  - `--source-index` の値（`index`）があればそれを採用し、範囲外・取得失敗は
    `DiscoveryError`。
  - 有効な候補が 1 件なら自動選択する。
  - 複数かつ非対話（stdin が TTY でない）なら `DiscoveryError` にして
    `--source-index` を案内する。
  - 対話時は候補一覧を stderr に出し、番号入力を最大 3 回受け付ける。
    空 Enter は最初の有効候補。選択できなければ `DiscoveryError`。
- `format_candidate()`: 一覧表示用に、マスター（Variant 数）/ メディア
  （セグメント数・合計時間）/ 取得失敗を整形する。`--list-candidates` で使う。
- 候補を選んだあと、`cli.run` が候補に付いたヘッダ（ブラウザが送った
  Referer / Origin / Cookie / User-Agent）を明示指定の無い項目だけマージする。
  `Referer` が無ければページ URL で補う（優先順位は
  明示指定 > 候補ヘッダ > ページ URL）。`User-Agent` は明示指定が無ければ
  既定値よりブラウザの値を優先する。

### `browser_cdp.py`

- Chrome / Edge / Chromium を CDP（Chrome DevTools Protocol）で操作し、
  ページの通信から m3u8 を検出する。ブラウザ本体は利用者環境のものを使う。
- `ws.py` の WebSocket で CDP に接続するため、Python の実行時依存は増えない。
- `find_browser()` の探索順:
  1. `--browser-path`（ファイルパス、または `PATH` 上の名前）
  2. 環境変数 `VIDEO_CONCAT_BROWSER`
  3. Windows: `ProgramFiles(x86)` / `ProgramFiles` / `LOCALAPPDATA` 配下の
     Edge / Chrome / Chromium、次に `PATH` の `msedge` / `chrome`
  4. Linux: `microsoft-edge` / `microsoft-edge-stable` / `google-chrome` /
     `google-chrome-stable` / `chromium` / `chromium-browser` / `chrome`
  5. Playwright 同梱の Chromium（`_playwright_browser_path()`）
     - ルートは `PLAYWRIGHT_BROWSERS_PATH`（`0` 以外）、Windows は
       `%LOCALAPPDATA%\ms-playwright`、macOS は
       `~/Library/Caches/ms-playwright`、Linux は `~/.cache/ms-playwright`。
     - `chromium-<revision>` を新しい順に走査し、OS 別の実行ファイル
       （Linux: `chrome-linux64/chrome`、Windows: `chrome-win64/chrome.exe`、
       macOS: `chrome-mac*/Chromium.app/Contents/MacOS/Chromium`）を返す。
     - `chrome-headless-shell` は `--headless` を受け付けないため対象外。
  - 見つからなければ `BrowserError`（`--browser-path` を案内）。
- `BrowserSession`:
  - `--user-data-dir` 未指定なら一時ディレクトリを作り、終了時に削除する。
  - `--remote-debugging-port=0` で起動し、`DevToolsActivePort` から実際の
    ポートを読む（`startup_timeout` は既定 15 秒）。
  - ヘッドレスは `--headless=new` を試し、失敗したら `--headless` に切り替える。
    `--headed` 指定時は画面表示ありで起動する。
  - 自動再生・ミュート・ウィンドウサイズ・初回起動ダイアログ無効化などの
    フラグを付ける。root 実行時は `--no-sandbox`、Windows 以外では
    `--disable-dev-shm-usage`。
  - Windows のヘッドレスでは `CREATE_NO_WINDOW` を付ける。
- 検出:
  - `Page.enable` / `Network.enable` / `Runtime.enable` と
    `Network.setCacheDisabled` を送る。
  - `Network.responseReceived` で URL に `.m3u8` を含む、または MIME が
    `application/vnd.apple.mpegurl` などの HLS 形式のものを候補にする
    （`http` / `https` のみ、初出順）。
  - `Network.requestWillBeSent` / `...ExtraInfo` から Referer / Origin / Cookie /
    User-Agent を控え、Cookie が無ければ `Network.getCookies` で補う。
  - ページ読み込み後に `video.play()`・`video.click()`・一般的な再生ボタンの
    セレクタで再生を試み、`page_wait` 秒（既定 5）待つ。候補が無ければ
    もう一度だけ再生を試す。`--click-selector` の要素もクリックする。
  - 収集した候補は `page_source.probe_candidates()` で検証する（並列 4）。
- 後始末:
  - CDP 接続とブラウザプロセスを閉じ、一時ログと（自分で作った）一時
    ユーザーデータディレクトリを削除する。
- `discover_browser()` は `BrowserSession` をラップし、CDP / WebSocket /
  OS エラーを `BrowserError` に変換する。

### `ws.py`

- Chrome DevTools Protocol に接続するための最小 WebSocket クライアント。
  標準ライブラリのみで実装し、`browser_cdp.py` から使う。
- `ws://` のみ対応（`wss://` は未対応）。
- HTTP Upgrade でハンドシェイクし、`101` と `Sec-WebSocket-Accept` を検証する。
- 送信フレームは常にマスクする。受信は分割フレームを結合し、ping には pong を返し、
  close では `WebSocketError` を送出する。
- 1 メッセージの上限は 64 MiB、ハンドシェイク応答ヘッダは 64 KiB まで。
- コンテキストマネージャに対応し、`settimeout()` でソケットのタイムアウトを変更できる。

### `tools/mock-hls-server`

- 開発時のみ使う Node.js 製のテストサーバー。Python の配布物には含めない。
- `server.mjs` は標準モジュールだけで実装し、配信ディレクトリのファイルを返す。
  - `--referer` は完全一致。複数指定可。未指定なら検査しない。
  - `/`、`/index.html`、`/player.html`、`/health` は Referer 検査の対象外。
    `/` と `/index.html` は使い方の HTML、`/player.html` は `<video>` で
    `/master.m3u8` を読み込む再生ページ、`/health` は `{"ok":true}` を返す。
  - `/player.html` は `--from-page` の静的解析・ブラウザ方式の手動確認に使う。
  - `GET` / `HEAD` のみ対応し、それ以外は 405 を返す。
  - `decodeURIComponent` 後のパスを `path.resolve` で正規化し、
    配信ディレクトリ外への参照は 403 にする。
  - 403 時は理由（ヘッダーなし / 不一致）と許可値を返す。
- `generate-media.mjs` は ffmpeg で MPEG-TS を生成し、
  `master.m3u8` と `v0/playlist.m3u8` を書き出す。
- `media/` は `.gitignore` 対象で、リポジトリには含めない。

## 4. データフロー

1. 引数検証と設定ファイルの読み込み。
2. `--from-page` の場合は Webページから m3u8 の候補を探索する。
   `auto` は静的解析で有効な候補が見つかればそれを採用し、無ければブラウザ方式に
   フォールバックする。候補が 1 件なら自動選択、複数なら `--source-index` か
   対話で選ぶ。選んだ URL を取得元にし、候補ヘッダを明示指定の無い項目だけ補う。
   Referer が無ければ最終ページ URL で補う。
   `--list-candidates` は候補を一覧表示して終了する。
3. 出力名を決定（未指定なら `YYYYmmddHHMMSS.mp4`）。
4. m3u8 を取得して `Playlist` に変換。マスターなら Variant を選択して再取得。
5. 非対応機能（暗号化・fMP4・BYTERANGE）を検査。
6. ffmpeg を解決。見つからなければ配布版のみ自動ダウンロード。
7. `EXT-X-TOKEN` の扱いを警告またはオプションで指定。
8. セグメントを並列ダウンロード。
9. `list.txt` を作成し ffmpeg で結合。
10. 成功後、自分が作ったセグメント・`list.txt`・作業ディレクトリの
    `playlist.m3u8` を削除（`--keep-segments` を除く）。
    `--save-playlist` で指定したコピー先と、ローカル入力の m3u8 は削除しない。

## 5. 設計上の決定と制約

- **Python 標準ライブラリのみ**で実装し、実行時依存を ffmpeg だけに抑える。
- **配布版は onedir**。exe と同じフォルダに ffmpeg を置く形式にし、
  初回起動時の自動ダウンロードにも対応する。
- **自動ダウンロードは配布版のみ**。ソース実行では `--ffmpeg` または `PATH` を使う。
  `sys.frozen` が偽なら自動ダウンロードせず、手動配置を案内する。
- **書き込み不可時はエラー**。exe フォルダ以外へのフォールバックはしない。
- **プレイリスト順**を正とする。`list.cmd` のようなファイル名ソートは行わない。
- **MPEG-TS を前提**とし、AES-128 / SAMPLE-AES / fMP4 / BYTERANGE は明示的にエラーにする。
- **EXT-X-TOKEN は非標準**のため、URL への付与方法をユーザーが選ぶ。
- **再実行性**を重視し、セグメントは `.part` + atomic rename、既存ファイルは再利用。
- **安全な後始末**: 作業ディレクトリ全体を消さず、自分が作ったファイルだけ削除する。
- **LGPL 静的ビルド**を採用し、`-c copy` のみ使うため追加のコーデック依存を持たない。
- **Webページからの探索は静的解析 → ブラウザの順**（`auto`）。静的解析は
  標準ライブラリ（正規表現 + `html.parser`）のみで完結させ、JavaScript の実行が
  必要なページだけ CDP にフォールバックする。
- **ブラウザは利用者環境のものを使う**（Chrome / Edge / Chromium）。配布物には
  同梱せず、Python の実行時依存も増やさない。CDP の WebSocket は `ws.py` の
  最小実装で扱う。
- **ブラウザが送ったヘッダを再利用する**。Referer / Origin / Cookie / User-Agent を
  候補に付け、明示指定が無い項目だけ補う。Referer は未指定なら最終ページ URL でも補う。
- **定番の難読化（Dean Edwards の packer）は静的解析で展開する**。
  実行を伴わずに URL を取り出せるため、ブラウザ方式に落ちる前に候補を見つけられる。
  ただし独自の難読化や実行時の URL 生成までは追わない（ブラウザ方式の役割）。
- **ブラウザ方式の E2E テストは、ヘッドレスブラウザが見つかる環境でのみ実行する**。
  `VIDEO_CONCAT_TEST_BROWSER`、Playwright の Chromium キャッシュ、
  `PATH` 上の Chrome / Edge / Chromium の順に探し、無ければスキップする。

## 6. 変更履歴 (主要トピック)

- 2026-11-03: 初版。m3u8 取得、並列ダウンロード、ffmpeg 結合、CLI、ドキュメントを追加。
- 2026-11-03: 配布版対応。
  - `config.py` / `headers.py` / `ffmpeg_bootstrap.py` / `ffmpeg_sources.py` を追加。
  - Referer を含む設定ファイル、CLI との優先順位マージ。
  - ffmpeg の同梱探索と初回自動ダウンロード。
  - PyInstaller onedir ビルド、Windows / Linux ビルドスクリプト。
  - テストと配布ドキュメントを追加。
- 2026-11-03: 実装に合わせてドキュメントを更新。
  - README の ffmpeg 探索順に `ffmpeg/`・`bin/` を追記。
  - Linux 配布 zip 展開時の実行権限（`chmod +x`）を README / build に追記。
  - `--keep-segments` が残すファイル、`EXT-X-MEDIA` の警告動作を明記。
  - `FFMPEG-BUILD-INFO.txt` がビルド時生成であることを明記。
- 2026-11-11: テスト用モック HLS サーバーを追加。
  - `tools/mock-hls-server/server.mjs`（Referer 完全一致検査、403）。
  - `tools/mock-hls-server/generate-media.mjs`（ffmpeg で TS 生成）。
- 2026-11-14: モックサーバーの Referer 検査対象外に `/index.html` を追記。
- 2026-11-14: 実データのサンプル `video.m3u8` をリポジトリから削除。
  スモークテストの例は手元の m3u8 を使う形に変更。
- 2026-11-14: `net.fetch_text()` / `fetch_text_with_url()` に `max_bytes` を追加。
  応答サイズの上限を指定でき、超過時は `DownloadError` にしてリトライしない。
  既定は `None`（上限なし）で、現時点で CLI からの指定はない。
- 2026-11-14: Webページからの m3u8 探索を追加。
  - `page_source.py` — HTML・インライン JS・同一オリジンの外部スクリプト・iframe から
    m3u8 候補を抽出し、取得してプレイリストか検証。対話選択にも対応。
  - `browser_cdp.py` — Chrome / Edge / Chromium を CDP で操作し、通信から m3u8 を検出。
  - `ws.py` — CDP 用の最小 WebSocket クライアント（RFC 6455、標準ライブラリのみ）。
  - `cli.py` — `--from-page` / `--page-mode` / `--page-wait` / `--list-candidates` /
    `--source-index` / `--browser-path` / `--headed` / `--user-data-dir` /
    `--click-selector` を追加。`auto` は静的解析 → ブラウザの順。
  - テストを追加: `tests/test_page_source.py`（静的解析・候補選択）、
    `tests/test_ws.py`（WebSocket のハンドシェイク・分割フレーム・マスク）、
    `tests/test_e2e.py`（静的解析の E2E、ブラウザ方式の Cookie / Referer E2E）。
  - `CdpConnection.pump()` がイベント待ちのタイムアウトを例外にせず、
    待ち時間の経過として扱うよう修正。
- 2026-11-14: モック HLS サーバーに `/player.html` を追加。
  `<video>` で `/master.m3u8` を読み込み、`--from-page` の静的解析と
  ブラウザ方式を手動で確認できる。Referer 検査の対象外。
- 2026-11-14: `--from-page` のヘッダのマージを修正。
  - `Referer` は 明示指定 > ブラウザが送った値 > 最終ページ URL の順で補う。
  - `User-Agent` は `--user-agent` / 設定ファイルで明示していなければ、
    既定値ではなくブラウザが送った値を使う。
  - `tests/test_cli.py` に `_merge_candidate_headers()` のテストを追加。
- 2026-11-14: 難読化 JavaScript（Dean Edwards の packer）の展開に対応。
  - `page_source.decode_obfuscated_javascript()` / `_decode_packer_calls()` /
    `_unpack()` / `_unescape_js()` / `_to_base()` を追加。
  - `extract_m3u8_urls()` が展開結果も走査するように変更。
    `--page-mode static` でも定番の packer で隠された m3u8 を見つけられる。
  - `tests/test_page_source.py` に packer の展開・URL 抽出・往復のテストを追加。
- 2026-11-14: `find_browser()` が Playwright 同梱の Chromium を自動検出。
  - `_playwright_browsers_roots()` / `_playwright_chromium_relative_paths()` /
    `_playwright_revision()` / `_playwright_browser_path()` を追加。
  - 探索先は `PLAYWRIGHT_BROWSERS_PATH`（`0` 以外）と OS 標準のキャッシュ。
    `chromium-<revision>` を新しい順に見て、OS 別の実行ファイルを返す。
  - `tests/test_browser_cdp.py` に検出・優先順位・未検出のテストを追加。
