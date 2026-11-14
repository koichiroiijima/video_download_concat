# 引き継ぎ (handover)

次のセッション / モデルが最初に読むためのエントリポイントです。
まず本ファイルを読み、必要に応じて下記へ進んでください。

## 1. 最初に読む順番

1. 本ファイル (`handover.md`) — 現状と次の作業
2. [AGENTS.md](AGENTS.md) — 作業規約・コマンド・Git 方針
3. [docs/design.md](docs/design.md) — 実装とデータフロー
4. [docs/details.md](docs/details.md) — 外部仕様・設定・テスト
5. [docs/build.md](docs/build.md) — ビルドと配布
6. [README.md](README.md) — 利用者向け操作

## 2. プロジェクトの現状

`list.cmd` が手動で行っていた m3u8 セグメントの取得・結合を Python CLI に置き換え、
Python 不要の配布版（PyInstaller onedir）を追加した。

| 領域 | 状態 |
| --- | --- |
| プロジェクト初期化（git / `.gitignore` / `.venv`） | 完了 |
| m3u8 パーサー（マスター / メディア） | 実装済み |
| セグメント並列ダウンロード（リトライ / 再開 / 進捗） | 実装済み |
| ffmpeg concat 結合 | 実装済み |
| `EXT-X-TOKEN`（クエリ / ヘッダ） | 実装済み |
| 設定ファイル（Referer ほか） | 実装済み |
| ffmpeg の同梱探索・初回自動ダウンロード | 実装済み |
| モック HLS サーバー（HTTP / Referer 検査 / 再生ページ） | 実装済み・確認済み |
| Webページからの m3u8 探索（`--from-page`） | 実装済み（静的解析 + ブラウザ） |
| ブラウザ方式（`browser_cdp.py` / `ws.py`） | 実装済み（E2E はブラウザがある環境のみ） |
| PyInstaller ビルド（Linux / Windows スクリプト） | 実装済み |
| Linux 配布版のビルド・E2E | 確認済み（zip 展開後も確認） |
| Windows 11 配布版のビルド・E2E | **未確認（Windows 実機が必要）** |
| 自動テスト | 69 passed（ブラウザ E2E は環境によりスキップ） |
| AES-128 / fMP4 / BYTERANGE | 未対応（明示エラー） |

## 3. 現在の実行状態

常駐プロセスはありません。

```bash
uv sync
uv run pytest
uv run video-concat --help
```

Linux 配布版のビルド:

```bash
./packaging/build.sh
# または ffmpeg を取得して同梱
./packaging/build.sh --fetch-ffmpeg
```

Windows 11 では PowerShell から:

```powershell
.\packaging\build.ps1
# ffmpeg を取得して同梱する場合
.\packaging\build.ps1 -FetchFfmpeg
```

モック HLS サーバー:

```bash
node tools/mock-hls-server/generate-media.mjs
node tools/mock-hls-server/server.mjs --referer https://example.com/ \
  --referer http://127.0.0.1:8080/player.html
# 再生ページ: http://127.0.0.1:8080/player.html（--from-page の確認用）
```

## 4. 検証コマンド

```bash
uv sync
uv run pytest
uv run video-concat --help
uv run python -m video_concat --help
uv run python -m compileall -q src packaging tests
```

パーサーのスモークテスト:

```bash
uv run python - <<'PY'
from pathlib import Path
from video_concat.m3u8 import parse_playlist

playlist_path = Path("path/to/video.m3u8")  # 手元の m3u8 に置き換える
text = playlist_path.read_text(encoding="utf-8")
playlist = parse_playlist(text, playlist_path.resolve().as_uri())
print("segments:", len(playlist.segments))
print("first:", playlist.segments[0].uri)
print("last:", playlist.segments[-1].uri)
print("tokens:", len(playlist.tokens))
PY
```

E2E テスト用 ffmpeg:

```bash
uv run python packaging/fetch_ffmpeg.py
# vendor/ffmpeg/linux-x86_64/ffmpeg ができる
uv run pytest tests/test_e2e.py -q
```

ブラウザ方式の E2E はヘッドレスブラウザが必要です。見つからない場合はスキップされます。
実行パスは `VIDEO_CONCAT_TEST_BROWSER` で指定できます。

```bash
VIDEO_CONCAT_TEST_BROWSER=/path/to/chrome uv run pytest tests/test_e2e.py -q
```

## 5. 直近の変更履歴

- 2026-11-03: 初版実装。
  - `src/video_concat/`（cli / m3u8 / net / downloader / ffmpeg）
  - `pyproject.toml`（uv + hatchling、`video-concat` コマンド）
  - README / docs / AGENTS / opencode.json
- 2026-11-03: 配布版対応。
  - `config.py` — `video-concat.ini` の読み込み、Referer ほか。
  - `headers.py` — ヘッダ解析・検証。
  - `ffmpeg_bootstrap.py` — 同梱 ffmpeg の探索、初回自動ダウンロード。
  - `ffmpeg_sources.py` — LGPL 静的ビルドの URL・SHA256。
  - `packaging/` — PyInstaller ビルド、Windows / Linux スクリプト、設定例、ライセンス。
  - `tests/` — 設定、ヘッダ、ffmpeg 探索、E2E。
  - `docs/build.md` — ビルド・配布手順。
- 動作確認:
  - `uv run pytest` → 36 passed。
  - Linux 配布版をビルドし、`--help` と引数なしヘルプを確認。
  - 配布 zip を展開した状態で E2E を実行し、Referer 送信・結合・後始末を確認。
  - 自動ダウンロードと読み取り専用フォルダのエラーは、展開前の配布フォルダで確認。
  - 配布版で exe 隣の `video-concat.ini` を読み、Referer が m3u8 と各セグメントに送られることを確認。
  - `ffmpeg` を削除した配布版で自動ダウンロード → 結合まで確認。
  - 読み取り専用フォルダで「書き込み権限がありません」エラーになることを確認。
  - 実データのサンプル（1830 セグメント）でパーサーを確認。
    サンプルは現在リポジトリに含めていない。
- 2026-11-03: ドキュメントを実装に追従（オプション・探索順・zip 展開時の権限・
  ライセンスファイルの生成方法）。
- 2026-11-11: テスト用モック HLS サーバーを追加。
  - `tools/mock-hls-server/server.mjs` — Node.js 標準モジュールのみ。
    Referer 完全一致検査、不一致・未指定は 403。
  - `tools/mock-hls-server/generate-media.mjs` — ffmpeg で MPEG-TS を生成。
  - 生成物 (`tools/mock-hls-server/media/`) は `.gitignore` 対象。
  - 動作確認: Referer なし / 不一致 → 403、正しい Referer → 200。
    `video-concat` で 6 セグメントを取得し、6.16 秒の MP4 を生成。
- 2026-11-14: README の利用方法を最新化（実装の変更はなし）。
  - 「実行の流れ」を追加（取得 → 並列ダウンロード → 結合 → 後始末）。
  - ローカル m3u8 と `--base-url` の使い方、`-H` による複数ヘッダ指定を追記。
  - 403 / `EXT-X-TOKEN` / ffmpeg 未検出などのトラブル対処表を追加。
  - ボット対策・Cookie などの認証に関する制限事項を追記。
- 2026-11-14: モックサーバーの Referer 検査対象外に `/index.html` を追記
  （`tools/mock-hls-server/README.md` と usage コメント。振る舞いは変更なし）。
- 2026-11-14: 実データのサンプル `video.m3u8` をリポジトリから削除。
  - パーサーのスモークテスト例を手元の m3u8 に変更。
  - 実データのプレイリストは `EXT-X-TOKEN` を含む場合があるためコミットしない方針を明記。
- 2026-11-14: モックサーバーの案内ページと `file://` 入力をドキュメントに明記
  （実装の変更はなし）。
  - モックサーバーの `/` と `/index.html` は使い方の HTML、`/health` は
    `{"ok":true}` を返す。いずれも Referer 検査の対象外。
  - `SOURCE` にはローカルの `.m3u8` だけでなく `file://` URI も指定できる。
- 2026-11-14: `docs/build.md` に Linux ビルドの `--skip-ffmpeg --no-zip` を追記
  （実装の変更はなし。`packaging/build.py` は以前から対応済み）。
- 2026-11-14: 後始末の記述を実装に合わせて修正（実装の変更はなし）。
  `--save-playlist` のコピー先とローカル入力の m3u8 は成功後も残ることを
  `docs/design.md` / `docs/details.md` に明記。
- 2026-11-14: `src/video_concat/net.py` に `max_bytes` を追加（実装の変更）。
  - `fetch_text()` / `fetch_text_with_url()` が任意の `max_bytes` を受け取る。
  - 指定時は `max_bytes + 1` バイトまで読み、超過したら
    `応答が大きすぎます（上限 N バイト）` の `DownloadError` で即座に失敗する
    （リトライ対象外）。
  - 既定は `None`（上限なし）。CLI からの指定はまだ無いため、利用者向けの
    振る舞いに変化はない。テストも未追加。
- 2026-11-14: Webページからの m3u8 探索を追加。
  - `src/video_concat/page_source.py` — HTML・インライン JS・同一オリジンの
    外部スクリプト・iframe を静的に走査して m3u8 候補を抽出し、取得して
    プレイリストか検証する。候補の一覧表示・対話選択にも対応。
  - `src/video_concat/browser_cdp.py` — Chrome / Edge / Chromium を CDP で操作し、
    ページの通信から m3u8 を検出する。ヘッドレス起動（`--headed` で表示あり）、
    再生操作、Cookie / Referer の再利用に対応。
  - `src/video_concat/ws.py` — CDP 用の最小 WebSocket クライアント
    （RFC 6455、標準ライブラリのみ、`ws://` のみ）。
  - `cli.py` — `--from-page` / `--page-mode` / `--page-wait` / `--list-candidates` /
    `--source-index` / `--browser-path` / `--headed` / `--user-data-dir` /
    `--click-selector` を追加。`SOURCE` は任意引数になり、`--from-page` と排他。
    `auto` は静的解析 → ブラウザの順。
  - テストを追加: `tests/test_page_source.py`（静的解析・候補選択）、
    `tests/test_ws.py`（WebSocket のハンドシェイク・分割フレーム・マスク）、
    `tests/test_e2e.py`（静的解析 E2E、ブラウザ方式の Cookie / Referer E2E）。
    `uv run pytest` は 69 passed（ブラウザ E2E は環境によりスキップ）。
  - `CdpConnection.pump()` がイベント待ちのタイムアウトを例外にしていたため、
    待ち時間の経過として扱うよう修正。ブラウザ E2E はこれで通るようになった。
  - モック HLS サーバーに `/player.html` を追加。`<video>` で `/master.m3u8` を
    読み込み、`--from-page` の静的解析・ブラウザ方式を手動で確認できる。
  - `--from-page` のヘッダのマージを修正。`User-Agent` は明示指定が無ければ
    既定値ではなくブラウザが送った値を使う（テスト追加で 69 passed）。
- 2026-11-14: `/player.html` を使う `--from-page` の Referer 挙動をドキュメントに追記
  （実装の変更はなし）。
  - Referer を明示しなければ、候補検証と取得にページ URL が Referer として使われる。
  - Referer 検査を有効にしたモックサーバーでは、ページ URL も `--referer` に含めるか、
    許可済みの Referer を `-H` / `[http] referer` で明示する。
  - AGENTS / handover のモックサーバー起動例にもページ URL の `--referer` を追加した。

## 6. 既知の制約・注意点

- **Windows 11 の配布版は未ビルド・未検証**。Linux から Windows 用 exe は作れない。
  Windows 11 実機で `.\packaging\build.ps1` を実行し、docs/build.md のチェックリストを実施する。
- Linux の配布 zip は実行権限（`0755`）を記録している。`unzip` は通常それを保持するが、
  権限が外れた環境では `chmod +x video-concat ffmpeg` が必要。
- 自動ダウンロードは **配布版のみ**。ソース実行では `--ffmpeg` または `PATH` が必要。
- exe と同じフォルダに書き込めない場合、自動ダウンロードはエラーになる。
  ユーザーデータ領域へのフォールバックはしない方針。
- 自動ダウンロードする ffmpeg は LGPL 静的ビルド（FFmpeg 8.1.3）。
  `ffmpeg_sources.py` の URL・SHA256 を更新したらライセンス情報も更新する。
- 以前リポジトリに含めていた実データのサンプル（1830 セグメント）は、途中で
  `#EXT-X-ENDLIST` が重複し、`4000,` という壊れた行と、`video1757`〜`video1759` の
  欠番、`EXTINF` のない `video1760` を含んでいた。パーサーはこのような
  プレイリストも警告しつつ 1830 セグメントとして扱う。
- そのサンプルの `video1760` には、直前の `EXT-X-TOKEN`（1 個目）が適用された。
  元のプレイリストが壊れているため、トークンの対応は保証できない。
- 実データの m3u8 は `EXT-X-TOKEN` などのトークンを含む場合があるためコミットしない。
  動作確認には `tools/mock-hls-server/` の生成物か、`--save-playlist` で保存した
  プレイリストを使う。
- 削除した `video.m3u8` は git 履歴（初期コミット `430bf55`）に残っている。
  履歴の書き換えは行っていない。
- `EXT-X-TOKEN` の正しい使い方（クエリ / ヘッダ / Cookie）は配信元によって異なる。
  実データで確認するまでは `--token-query` / `--token-header` を指定して検証すること。
- Cloudflare などのボット対策が有効な配信元では、Referer やヘッダを指定しても
  取得できないことがある。本ツールはボット対策を回避する機能を持たない。
- Cookie やセッショントークンが必要な配信元は、`-H` や `--token-query` /
  `--token-header` で対応できる範囲に限られる。
- モック HLS サーバーは Node.js 18 以上が必要で、開発時のみ使用する。
  生成した `tools/mock-hls-server/media/` はコミットしない。
  `/player.html` は `--from-page` の確認用で、Referer 検査の対象外。
  Referer 検査を有効にする場合は、ページ URL も許可 Referer に含める
  （または許可済みの Referer を明示する）。
- ライブ配信は実行時点のスナップショットのみ。
- セグメント URL にトークンが含まれる場合、`-v` のログに URL が出る可能性がある。
- 設定ファイルに Cookie などの秘密情報を書く場合、配布物に含めないよう注意する。
- `list.cmd` は 4 桁ゼロ埋めのため 10,000 セグメント以上では破綻するが、
  本ツールはプレイリスト順を使うため影響しない。
- `page_source.py` / `ws.py` は単体テストあり。`browser_cdp.py` は E2E のみで、
  ヘッドレスブラウザが見つからない環境ではスキップされる。
- ブラウザ方式は利用者環境の Chrome / Edge / Chromium が必要。配布物には同梱しない。
  見つからない場合は `--browser-path` か環境変数 `VIDEO_CONCAT_BROWSER` で指定する。
- `ws.py` は `ws://` のみ対応（`wss://` は未対応）。CDP 用途の最小実装。
- ページ探索のオプションは CLI のみで、設定ファイルには項目がない。
- `--user-data-dir` を指定した場合、ブラウザのプロファイルは残る
  （指定しない場合の一時ディレクトリは終了時に削除される）。
- ブラウザが送った Cookie をセグメント取得に再利用する。`-v` のログに
  秘密情報を出さないよう注意する。

## 7. 次にやるとよさそうな作業 (候補)

- [ ] Windows 11 実機で `build.ps1` を実行し、配布版の E2E を確認する。
- [ ] Windows 11 でモック HLS サーバーを使い、Referer 付きダウンロードを確認する。
- [ ] Windows でスペース・日本語を含むパスの E2E を確認する。
- [ ] 実 URL で Referer 必須の配信元に対して E2E を確認する。
- [ ] 必要ならコード署名（SmartScreen 対策）を検討する。
- [ ] パーサー（マスター / 壊れた行 / トークン）のテストを追加する。
- [ ] `EXT-X-TOKEN` の実際の付与方法を確認し、必要なら Cookie 対応を追加する。
- [ ] AES-128 暗号化 HLS に対応するか判断する（`cryptography` などの依存追加）。
- [ ] fMP4（`EXT-X-MAP`）対応が必要か判断する。
- [ ] ライブ配信の継続取得（ポーリング）が必要か判断する。
- [ ] `browser_cdp.py` の単体テストを追加する（現状は E2E のみ）。
- [ ] Windows 11 で `--from-page` のブラウザ方式を確認する（Edge / Chrome。
      モックサーバーの `/player.html` を使える）。
- [ ] 実際の動画ページで `auto` の静的解析 → ブラウザのフォールバックを確認する。
- [ ] 必要ならページ探索の設定ファイル項目を追加する（現状は CLI のみ）。

## 8. 作業を始める前に

- 表示テキストは日本語（[AGENTS.md](AGENTS.md) の言語ポリシー）。
- コミットはタスク完了後に Conventional Commits 日本語で実行。push / ブランチ操作は要確認。
- 既存のスタイルに合わせる。
- `.venv/`、`*.mp4`、`*_segments/`、`list.txt`、`build/`、`dist/`、`vendor/ffmpeg/` はコミットしない。
- `packaging/LICENSES/` と `packaging/video-concat.ini` は配布物に必要なのでコミットする。
