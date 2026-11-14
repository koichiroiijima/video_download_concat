# 詳細設計 (design)

開発者向けの実装・データフロー・設計判断をまとめます。
外部仕様やテストは [details.md](details.md)、ビルドと配布は [build.md](build.md) を参照してください。

## 1. 全体アーキテクチャ

CLI が m3u8 を取得・解析し、セグメントを並列ダウンロードしたあと、
ffmpeg の concat デマルチプレクサで MP4 に結合します。
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
│   └── ffmpeg_sources.py     配布元 URL・SHA256 の定義
└── tests/
    ├── test_cli.py
    ├── test_config.py
    ├── test_headers.py
    ├── test_ffmpeg_bootstrap.py
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

### `tools/mock-hls-server`

- 開発時のみ使う Node.js 製のテストサーバー。Python の配布物には含めない。
- `server.mjs` は標準モジュールだけで実装し、配信ディレクトリのファイルを返す。
  - `--referer` は完全一致。複数指定可。未指定なら検査しない。
  - `/`、`/index.html`、`/health` は Referer 検査の対象外。
    `/` と `/index.html` は使い方の HTML、`/health` は `{"ok":true}` を返す。
  - `GET` / `HEAD` のみ対応し、それ以外は 405 を返す。
  - `decodeURIComponent` 後のパスを `path.resolve` で正規化し、
    配信ディレクトリ外への参照は 403 にする。
  - 403 時は理由（ヘッダーなし / 不一致）と許可値を返す。
- `generate-media.mjs` は ffmpeg で MPEG-TS を生成し、
  `master.m3u8` と `v0/playlist.m3u8` を書き出す。
- `media/` は `.gitignore` 対象で、リポジトリには含めない。

## 4. データフロー

1. 引数検証と設定ファイルの読み込み。
2. 出力名を決定（未指定なら `YYYYmmddHHMMSS.mp4`）。
3. m3u8 を取得して `Playlist` に変換。マスターなら Variant を選択して再取得。
4. 非対応機能（暗号化・fMP4・BYTERANGE）を検査。
5. ffmpeg を解決。見つからなければ配布版のみ自動ダウンロード。
6. `EXT-X-TOKEN` の扱いを警告またはオプションで指定。
7. セグメントを並列ダウンロード。
8. `list.txt` を作成し ffmpeg で結合。
9. 成功後、セグメント・リスト・保存したプレイリストを削除（`--keep-segments` を除く）。

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
