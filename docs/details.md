# 機能詳細・外部仕様・テスト (details)

保守・拡張担当向けに、CLI の外部仕様とテスト観点をまとめます。
内部実装は [design.md](design.md)、ビルドと配布は [build.md](build.md) を参照してください。

## 1. 外部仕様 (CLI)

実行方法:

```bash
# 配布版
video-concat SOURCE [OPTIONS]
video-concat --from-page URL [OPTIONS]
video-concat.exe --from-page URL [OPTIONS]

# ソース実行
uv run video-concat SOURCE [OPTIONS]
uv run python -m video_concat --from-page URL [OPTIONS]
```

引数なしで実行した場合は、ヘルプを表示して終了コード 0 で終了します。
`SOURCE` と `--from-page` はどちらか一方が必須で、同時には指定できません。

### 入力

| 項目 | 内容 |
| --- | --- |
| `SOURCE` | `http://` / `https://` の m3u8 URL、ローカルの `.m3u8` ファイル、または `file://` URI |
| `--from-page URL` | Webページから m3u8 を探す。`http://` / `https://` のみ。`--base-url` とは併用不可 |
| マスタープレイリスト | `EXT-X-STREAM-INF` を解釈し、`--variant` で選択 |
| メディアプレイリスト | `EXTINF` と URI 行を順に取得 |
| 相対 URI | m3u8 の URL（ローカルなら `file://`）を基準に解決 |
| `--base-url` | 相対 URI の基準を上書き（ローカル m3u8 を元の配信元から取得する場合など） |
| `EXT-X-TOKEN` | 非標準。`--token-query` または `--token-header` で利用 |
| Referer | 設定ファイルの `[http] referer`、または `-H "Referer: ..."` |

### 出力

| 項目 | 内容 |
| --- | --- |
| 既定の出力名 | `YYYYmmddHHMMSS.mp4` |
| 既定の作業ディレクトリ | `<出力名>_segments` |
| セグメント名 | `0000.ts`, `0001.ts`, ...（プレイリスト順のゼロ埋め連番） |
| 成功時 | 出力 MP4 を残し、セグメント・`list.txt`・作業ディレクトリの `playlist.m3u8` を削除。`--save-playlist` のコピー先とローカル入力の m3u8 は残す |
| `--keep-segments` | セグメント一式と `list.txt`・`playlist.m3u8` を残す |
| 再実行 | 既存の非空セグメントを再利用（`--force` で再取得） |

### オプション

| オプション | 既定値 | 内容 |
| --- | --- | --- |
| `--from-page URL` | なし | Webページから m3u8 を探す（`http://` / `https://`） |
| `--page-mode MODE` | `auto` | `auto` / `static` / `browser` |
| `--page-wait SECONDS` | `5` | ブラウザでページを開いてから待つ秒数（0 以上） |
| `--list-candidates` | 無効 | 見つかった候補を一覧表示して終了（`--from-page` 必須） |
| `--source-index N` | なし | 使用する候補の番号（0 始まり、`--from-page` 必須） |
| `--browser-path PATH` | 自動探索 | Chrome / Edge / Chromium の実行パス |
| `--headed` | 無効 | ブラウザを画面表示ありで起動（デバッグ用） |
| `--user-data-dir PATH` | 一時ディレクトリ | ブラウザのユーザーデータディレクトリ |
| `--click-selector CSS` | なし | 再生ボタンなどをクリックする CSS セレクタ |
| `--config PATH` | 自動探索 | 設定ファイルのパス |
| `-o, --output` | `YYYYmmddHHMMSS.mp4` | 出力ファイル。拡張子がなければ `.mp4` を補う |
| `--work-dir` | `<出力名>_segments` | セグメント保存先 |
| `--base-url` | なし | 相対 URI を解決する基準 URL を上書き |
| `-H, --header` | なし | 追加 HTTP ヘッダ。複数指定可 |
| `--user-agent` | ブラウザ相当 | User-Agent |
| `-c, --concurrency` | `8` | 同時ダウンロード数（1 以上） |
| `--retries` | `3` | 1 セグメントあたりの試行回数（1 以上） |
| `--timeout` | `30` | HTTP タイムアウト秒（0 より大きい） |
| `--token-query` | なし | `EXT-X-TOKEN` をクエリパラメータとして付与 |
| `--token-header` | なし | `EXT-X-TOKEN` を HTTP ヘッダとして送信 |
| `--variant` | `best` | マスターの Variant（0 始まりの番号 or `best`） |
| `--ffmpeg` | 同梱 → `PATH` | ffmpeg の実行パス |
| `--auto-download / --no-auto-download` | 有効 | ffmpeg が見つからない場合の自動ダウンロード |
| `--keep-segments / --no-keep-segments` | 無効 | 結合後にセグメント・`list.txt`・`playlist.m3u8` を削除しない |
| `--force / --no-force` | 無効 | 既存の出力・セグメントを上書き |
| `--save-playlist` | なし | 取得したメディアプレイリストのコピー先 |
| `-v, --verbose` | 無効 | デバッグログを表示 |
| `-q, --quiet` | 無効 | 進捗と警告を抑制 |
| `--version` | — | バージョンを表示 |

### Webページから m3u8 を探す（`--from-page`）

```bash
# 候補を一覧表示
video-concat --from-page https://example.com/watch/123 --list-candidates

# 自動探索（静的解析 → ブラウザ）で 1 件に絞ってダウンロード
video-concat --from-page https://example.com/watch/123 -o output.mp4

# 候補を番号で指定
video-concat --from-page https://example.com/watch/123 --source-index 1
```

- `--page-mode`:
  - `auto`（既定）: 静的解析で有効な候補が見つかればそれを採用し、
    無ければブラウザ方式にフォールバックします。
  - `static`: HTML・インライン JS・同一オリジンの外部スクリプト・iframe の
    静的解析のみ行います。`--page-wait` / `--browser-path` / `--headed` /
    `--user-data-dir` / `--click-selector` とは併用できません。
  - `browser`: Chrome / Edge / Chromium を CDP で操作し、ページの通信から
    m3u8 を検出します。JavaScript で URL を生成するページ向けです。
- 候補の選択:
  - 有効な候補が 1 件なら自動選択します。
  - 複数ある場合は `--source-index`（0 始まり）で指定します。
  - 標準入力が対話端末なら番号入力もできます（空 Enter は最初の有効候補、
    最大 3 回）。非対話で複数ある場合はエラーにします。
- `--list-candidates`: 候補を一覧表示して終了します。取得失敗した候補も
  理由付きで表示します。候補が 0 件の場合はエラー（終了コード 1）です。
- ヘッダ:
  - 検出後、`Referer` を最終ページ URL で補います。
  - ブラウザ方式で実際に送られた `Referer` / `Origin` / `Cookie` / `User-Agent` は、
    `-H` や設定ファイルで明示していない項目だけ補います。
- ブラウザの探索順: `--browser-path` → 環境変数 `VIDEO_CONCAT_BROWSER` →
  OS の既定インストール先 / `PATH`。Linux は `microsoft-edge` / `google-chrome` /
  `chromium` など、Windows は Edge / Chrome / Chromium の標準的な場所を見ます。
- `--user-data-dir` にログイン済みプロファイルを指定すると、認証が必要なページも
  開ける場合があります（普段使いのプロファイルを汚さないよう注意）。
- `--headed` は画面表示ありで起動します。原因調査用です。
- `--click-selector` は再生ボタンなどをクリックする CSS セレクタです。
- `--page-wait` はブラウザ方式でページを開いたあとの待ち時間（既定 5 秒）です。
- ページ探索のオプションは CLI のみで、設定ファイルには項目がありません。

## 2. 設定ファイル

ファイル名は `video-concat.ini`。UTF-8（BOM 付きも可）で読み込みます。

### 探索順

1. `--config PATH`
2. 配布版: exe と同じフォルダの `video-concat.ini`
3. カレントディレクトリの `video-concat.ini`

見つからなくても動作します。`--config` を明示したのに存在しない場合はエラーです。

### 項目

```ini
[http]
referer =
user_agent =

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

| セクション / キー | 内容 |
| --- | --- |
| `[http] referer` | Referer。`http://` / `https://` のみ |
| `[http] user_agent` | User-Agent |
| `[download] concurrency` | 同時ダウンロード数（1 以上） |
| `[download] retries` | 試行回数（1 以上） |
| `[download] timeout` | タイムアウト秒（0 より大きい） |
| `[playlist] variant` | `best` または 0 始まりの番号 |
| `[playlist] base_url` | 相対 URI の基準 URL（`http://` / `https://` / `file://`） |
| `[playlist] token_query` | `EXT-X-TOKEN` を付与するクエリ名 |
| `[playlist] token_header` | `EXT-X-TOKEN` を送る HTTP ヘッダ名 |
| `[output] path` | 出力ファイル |
| `[output] work_dir` | セグメント保存先 |
| `[output] save_playlist` | プレイリストのコピー先 |
| `[behavior] keep_segments` | セグメントを残す（true / false） |
| `[behavior] force` | 上書きする（true / false） |
| `[ffmpeg] path` | ffmpeg の実行パス |
| `[ffmpeg] auto_download` | 自動ダウンロード（true / false） |

- 空欄は既定値。
- 設定ファイル内の相対パスは、設定ファイルのあるフォルダ基準。
- コマンドライン引数 > 設定ファイル > 既定値。
- ブール値は `true` / `false` / `yes` / `no` / `on` / `off` / `1` / `0` を受け付けます。
- `%` を含む Referer もそのまま扱えます（補間なし）。
- コメントは行頭の `;` または `#`。行末コメントには対応していません。

### ヘッダの優先順位

1. `-H "Referer: ..."` / `-H "User-Agent: ..."`（CLI）
2. 設定ファイルの `referer` / `user_agent`
3. 既定値

Referer は m3u8 本体、マスターの子プレイリスト、すべてのセグメントに送られます。

## 3. ffmpeg の探索と自動ダウンロード

### 探索順

1. `--ffmpeg PATH`
2. 設定ファイルの `[ffmpeg] path`
3. `_MEIPASS` 直下（onefile 形式に備える）
4. exe と同じフォルダ、`ffmpeg/`、`bin/`
5. `PATH`

明示指定（1 / 2）が見つからない場合は、自動ダウンロードせずエラーにします。

### 自動ダウンロード

- 配布版（PyInstaller でビルドした実行ファイル）でのみ動作します。
- `auto_download = true`（既定）で、上記のどこにも ffmpeg がない場合に実行します。
- 固定バージョンの LGPL 静的ビルドを取得します。
  - Linux x86_64 / Windows x86_64
  - FFmpeg 8.1.3（BtbN autobuild-2026-11-02-12-51）
- SHA256 を検証し、zip / tar.xz から ffmpeg 本体だけを展開します。
- exe と同じフォルダに配置し、Linux では実行権限を付与します。
- `ffmpeg -version` で動作確認します。
- 配置先に書き込み権限がない場合はエラーにします。ユーザーデータ領域へのフォールバックはしません。

### ソース実行時

ソース実行では自動ダウンロードしません。`--ffmpeg` を指定するか、`PATH` に ffmpeg を入れてください。

## 4. エラー・例外

| 条件 | 結果 / メッセージ |
| --- | --- |
| 引数なし | ヘルプを表示して終了コード 0 |
| `SOURCE` と `--from-page` の同時指定 | `SOURCE と --from-page は同時に指定できません` |
| `SOURCE` も `--from-page` も未指定 | `m3u8 の URL（SOURCE）か --from-page のどちらかを指定してください` |
| `--from-page` が http/https 以外 | `--from-page には http:// / https:// の URL を指定してください` |
| `--from-page` と `--base-url` の併用 | `--from-page と --base-url は同時に指定できません` |
| `--list-candidates` / `--source-index` を `--from-page` なしで指定 | `<option> は --from-page と一緒に指定してください` |
| `--page-mode` の不正値 | `--page-mode には auto / static / browser のいずれかを指定してください` |
| `--page-mode static` とブラウザ系オプションの併用 | `<option> は --page-mode static と同時に指定できません` |
| `--page-wait` が負の値 | `--page-wait は 0 以上を指定してください` |
| ページから候補が見つからない | `Webページから m3u8 の候補を検出できませんでした。...--page-mode browser...` |
| 候補が複数・非対話 | `m3u8 の候補が複数あります。--source-index で番号を指定してください（0〜N-1）` |
| `--source-index` が範囲外 | `--source-index が範囲外です（候補は N 件、0 始まり）` |
| 候補がプレイリストとして取得できない | `候補 N は取得できませんでした: ...` / `m3u8 の候補は見つかりましたが、プレイリストとして取得できませんでした。` |
| ブラウザが見つからない | `Chrome / Edge / Chromium が見つかりません。...--browser-path...` |
| ブラウザ方式の失敗（`--page-mode browser`） | `ブラウザ方式で m3u8 を探索できませんでした: ...` |
| `auto` で静的解析・ブラウザとも失敗 | `Webページから m3u8 を検出できませんでした。ブラウザ方式も利用できません: ...` |
| ページの取得に失敗 | `Webページを取得できませんでした: ...` |
| `ffmpeg` が見つからない（自動ダウンロード無効） | 手動配置と `--ffmpeg` を案内して終了コード 1 |
| `ffmpeg` の自動ダウンロード失敗 | 手動配置と `--ffmpeg` を案内して終了コード 1 |
| ffmpeg の SHA256 不一致 | `SHA256 が一致しません` を含むエラー |
| exe フォルダに書き込めない | `書き込み権限がありません` を含むエラー |
| 出力ファイルが既に存在 | `--force` を促して終了コード 1 |
| 設定ファイルが見つからない（`--config`） | `設定ファイルが見つかりません` |
| 設定ファイルの形式・値が不正 | 該当セクション・キーを含むエラー |
| m3u8 が存在しない | `m3u8 ファイルが見つかりません` |
| m3u8 にセグメントがない | `プレイリストにセグメントがありません` |
| HTTP 4xx（408 / 425 / 429 を除く） | `HTTP <code> <reason>` を含むエラー |
| HTTP 500 / 502 / 503 / 504 / タイムアウト / 接続失敗 | 指数バックオフでリトライ後、失敗を報告 |
| Content-Length 不一致 | リトライ対象。最終的に失敗したらエラー |
| セグメントの一部が失敗 | 失敗セグメント番号と理由を最大 5 件表示し、終了コード 1 |
| 暗号化 HLS | `暗号化された HLS（...）には未対応です` |
| fMP4 | `fMP4（EXT-X-MAP）のプレイリストには未対応です` |
| `EXT-X-BYTERANGE` | `EXT-X-BYTERANGE を使うプレイリストには未対応です` |
| 不正な `--header` | `NAME: VALUE` 形式を案内 |
| 不正な `--variant` | `best` か 0 始まりの番号を案内 |
| 不正な `--base-url` | `http://` / `https://` / `file://` の URL を案内 |
| Ctrl+C | 終了コード 130。途中セグメントは作業ディレクトリに残る |

## 5. テスト構成

```bash
uv sync
uv run pytest
```

| ファイル | 対象 |
| --- | --- |
| `tests/test_config.py` | 設定ファイルの読み込み、BOM、`%`、不正値、未知キー |
| `tests/test_headers.py` | ヘッダ解析、正規化、インジェクション対策 |
| `tests/test_ffmpeg_bootstrap.py` | ffmpeg 探索、tar.xz / zip 展開、SHA256、既存ファイル |
| `tests/test_cli.py` | 引数なしヘルプ、バージョン、設定エラー |
| `tests/test_e2e.py` | 合成 TS、HTTP 配信、Referer、ffmpeg 結合 |

E2E は ffmpeg が必要です。`vendor/ffmpeg/linux-x86_64/ffmpeg` があるか、
`VIDEO_CONCAT_TEST_FFMPEG` でパスを指定するか、`PATH` に ffmpeg を入れてください。
ない場合はスキップされます。

`page_source.py` / `browser_cdp.py` / `ws.py`（Webページ探索・CDP・WebSocket）は
テスト未追加です。ブラウザ方式はヘッドレスブラウザに依存するため、手動または
モックページで確認します。

### 実施済みの確認

- `uv run pytest` → 36 passed
- Linux 配布版のビルドと `--help` / 引数なしヘルプ
- 配布 zip を展開した状態でも E2E を実行し、Referer 送信・結合・後始末を確認
- 配布 zip が実行ファイルの権限（`0755`）を記録していることを確認
- 配布版で exe 隣の設定ファイルを読む E2E
- 配布版で ffmpeg を削除して再実行し、自動ダウンロード → 結合まで確認
- 読み取り専用フォルダで自動配置がエラーになることを確認
- 実データのサンプル（1830 セグメント）のパーサー確認
  （サンプルは現在リポジトリに含めていません）

## 6. モック HLS サーバー

実配信元を使わずに、HTTP ダウンロードと Referer 検査を確認するための
テスト用サーバーです。Node.js 18 以上で動作し、npm パッケージは不要です。

```bash
node tools/mock-hls-server/generate-media.mjs   # media/ を生成
node tools/mock-hls-server/server.mjs --referer https://example.com/
```

| 項目 | 内容 |
| --- | --- |
| 配信 URL | `/master.m3u8`, `/v0/playlist.m3u8`, `/v0/segmentNNN.ts` |
| 既定の待ち受け | `127.0.0.1:8080` |
| Referer 検査 | `--referer` 指定時のみ。完全一致 |
| Referer なし / 不一致 | 403（`Referer ヘッダーがありません` / `Referer が許可されていません`） |
| 検査対象外 | `/`、`/index.html`、`/health` |
| 案内ページ | `/` と `/index.html` は使い方の HTML、`/health` は `{"ok":true}` を返す |
| パストラバーサル | 配信ディレクトリ外は 403 |
| 対応メソッド | `GET` / `HEAD`（それ以外は 405） |
| メディア生成 | ffmpeg で 1 秒の MPEG-TS を 6 個（既定） |

生成物 (`tools/mock-hls-server/media/`) は `.gitignore` の対象です。
サーバーのオプション（`--host` / `--port` / `--dir` / `--referer` / `-q`）と
動作確認の手順は `tools/mock-hls-server/README.md` を参照してください。

## 7. 変更時に確認するコマンド

```bash
uv sync
uv run pytest
uv run video-concat --help
uv run python -m video_concat --help
uv run python -m compileall -q src packaging tests
```

配布版のビルド:

```bash
# Linux
./packaging/build.sh

# Windows 11
.\packaging\build.ps1
```

## 8. 拡張時の注意点

- セグメントの順序は必ずプレイリスト順を維持する。ファイル名ソートに変更しない。
- `.jpeg` など拡張子が異なるセグメントを除外しない。
- `EXT-X-TOKEN` の付与方法は配信元ごとに異なる。推測で URL を書き換えない。
- リトライ回数を増やすと、期限切れトークンで全セグメントが長時間失敗し続ける点に注意。
- 失敗時に作業ディレクトリを消さない（再開できるようにする）。
- `--work-dir` にはユーザーの既存ファイルが存在し得る。削除は自分が作ったファイルに限定する。
- ログにトークンや Cookie を含む URL を出さないよう、`-v` の扱いに注意する。
- 実データの m3u8 はリポジトリにコミットしない（`EXT-X-TOKEN` などのトークンを
  含む場合がある）。解析の確認にはモックサーバーの生成物か
  `--save-playlist` で保存したプレイリストを使う。
- 設定ファイルに秘密情報を書く運用をする場合、配布物に含めないよう注意する。
- `net.fetch_text()` / `net.fetch_text_with_url()` は `max_bytes` で応答サイズの
  上限を指定できる（超過時は `応答が大きすぎます` の `DownloadError`、リトライしない）。
  CLI からは現時点で未指定のため、上限なしで取得する。
- ffmpeg の配布元 URL・SHA256 を更新したら、`ffmpeg_sources.py` と
  `packaging/LICENSES/FFmpeg-NOTICE.txt` を更新する。
  `packaging/LICENSES/FFMPEG-BUILD-INFO.txt` はビルド時に自動生成される。
- Windows では `list.txt` のパスにバックスラッシュを使わない（`/` に正規化する）。
- Webページの探索結果は「候補」であり、目的の動画とは限らない。複数候補がある場合は
  `--list-candidates` で確認し、`--source-index` で選ぶ。
- ブラウザ方式は利用者環境の Chrome / Edge / Chromium に依存する。見つからない場合は
  `--browser-path` か環境変数 `VIDEO_CONCAT_BROWSER` で指定する。
- ブラウザ方式の自動テストは未追加。CI で回す場合はヘッドレスブラウザの導入が必要。
- ブラウザの一時ユーザーデータディレクトリとログは終了時に削除する。
  `--user-data-dir` を指定した場合は削除しない（利用者のプロファイルを消さない）。
- ブラウザが送った Cookie をセグメント取得に再利用する。`-v` のログに Cookie を
  出さないよう注意する。
- `ws.py` は `ws://` のみ対応（`wss://` は未対応）。CDP 用途を想定した最小実装で、
  汎用の WebSocket ライブラリではない。
