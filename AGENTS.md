# AGENTS.md

このプロジェクトで AI エージェント（OpenCode）が作業する際の制約・方針・
よく使うコマンドをまとめます。仕様は [README.md](README.md)、
[docs/design.md](docs/design.md)、[docs/details.md](docs/details.md)、
[docs/build.md](docs/build.md) を参照してください。

## 1. 言語ポリシー (重要)

- ユーザー画面に表示されるテキストは**必ず日本語**にする。
- **中国語を表示に使わないこと。** コメントや説明文も日本語で書く。
- 識別子は既存コードのスタイル（Python の snake_case）に合わせる。

## 2. Git 運用

| 操作 | 方針 |
| --- | --- |
| `git init` | 未初期化ならユーザーに確認してから実行 |
| `git commit` | タスク完了後に Conventional Commits 形式の日本語で実行 |
| `git push` | 必ずユーザーに確認してから実行 |
| ブランチ操作 | 必ずユーザーに確認してから実行 |

## 3. よく使うコマンド

```bash
# 依存の同期（開発依存の PyInstaller / pytest も入る）
uv sync

# CLI の確認
uv run video-concat --help
uv run python -m video_concat --help

# テスト
uv run pytest
uv run python -m compileall -q src packaging tests

# ソース実行（ffmpeg のパス指定が必要）
uv run video-concat "https://example.com/video.m3u8" --ffmpeg /path/to/ffmpeg

# Webページから m3u8 を探す（--list-candidates で候補だけ確認）
uv run video-concat --from-page "https://example.com/watch/123" --list-candidates
uv run video-concat --from-page "https://example.com/watch/123" --ffmpeg /path/to/ffmpeg

# ffmpeg を vendor に取得（E2E / ビルド用）
uv run python packaging/fetch_ffmpeg.py

# Linux 配布版ビルド
./packaging/build.sh
./packaging/build.sh --fetch-ffmpeg

# Windows 11 配布版ビルド（Windows 実機）
.\packaging\build.ps1
.\packaging\build.ps1 -FetchFfmpeg

# モック HLS サーバー（Node.js 18 以上、npm 依存なし）
node tools/mock-hls-server/generate-media.mjs
node tools/mock-hls-server/server.mjs --referer https://example.com/ \
  --referer http://127.0.0.1:8080/player.html
# 再生ページ http://127.0.0.1:8080/player.html で --from-page を確認できる
```

パーサーのスモークテスト:

```bash
uv run python - <<'PY'
from pathlib import Path
from video_concat.m3u8 import parse_playlist

playlist_path = Path("path/to/video.m3u8")  # 手元の m3u8 に置き換える
text = playlist_path.read_text(encoding="utf-8")
playlist = parse_playlist(text, playlist_path.resolve().as_uri())
print(len(playlist.segments))
PY
```

## 4. 実装制約・規約

- 対象 Python は 3.10 以上。`from __future__ import annotations` を使う。
- **実行時依存は Python 標準ライブラリのみ**。ffmpeg は外部コマンドとして呼ぶ。
- 開発依存は `pyproject.toml` の `[dependency-groups] dev` に置く
  （PyInstaller / pytest）。新しい実行時依存を追加する場合は、必要性と代替案を先に検討する。
- セグメントの順序は**プレイリスト順**を維持する。ファイル名ソートに依存しない。
- `.jpeg` など拡張子が実体と異なるセグメントを除外しない。保存名は `.ts` とする。
- `EXT-X-TOKEN` は非標準タグとして扱い、付与方法を推測で実装しない。
- 失敗時に作業ディレクトリを削除しない（再開できるようにする）。
- 作業ディレクトリの後始末は、自分が作ったファイルだけを対象にする。
- ユーザー向けメッセージ・ヘルプ・ドキュメントは日本語で書く。
- 秘密情報（Cookie、トークン、認証ヘッダ）をログやドキュメントに書かない。
- 設定ファイル (`video-concat.ini`) は exe と同じフォルダを優先する。
  CLI > 設定ファイル > 既定値 の優先順位を守る。
- ffmpeg の自動ダウンロードは配布版のみ。ソース実行では `--ffmpeg` / `PATH` を使う。
- 自動ダウンロードの URL・SHA256 は `ffmpeg_sources.py` に固定する。
  変更時はライセンス情報も更新する。
- `--from-page` の探索は静的解析（`page_source.py`）→ ブラウザ（`browser_cdp.py`）の順。
  静的解析は Dean Edwards の packer（`eval(function(p,a,c,k,e,d){...})`）を
  `decode_obfuscated_javascript()` で展開してから URL を探す。
  ブラウザは利用者環境の Chrome / Edge / Chromium を CDP で操作する。
  Playwright 同梱の Chromium（`~/.cache/ms-playwright` など）も
  `find_browser()` が自動検出する。
- ブラウザ方式の WebSocket は `ws.py`（標準ライブラリのみ）。`wss://` は未対応。
- ブラウザ方式の E2E は `tests/test_e2e.py` にあり、ヘッドレスブラウザが見つかる
  環境でのみ実行する（無ければスキップ）。CI で回す場合はブラウザの導入が必要。
  `browser_cdp.py` の単体テストはない。
- ページ探索のオプションは CLI のみ（設定ファイル項目はない）。追加する場合は
  `config.py` と `docs/details.md` も更新する。
- exe フォルダに書き込めない場合はエラーにする（フォールバックしない）。
- Windows では `list.txt` のパスを `/` に正規化し、UTF-8 / LF で書く。
- 引数なし実行はヘルプ表示・終了コード 0。

## 5. コミットしてはいけないもの

- `.venv/`、`__pycache__/`、`*.py[cod]`
- `build/`、`dist/`、`.pytest_cache/`
- `vendor/ffmpeg/`（ダウンロードした ffmpeg 本体）
- `tools/mock-hls-server/media/`（モックサーバー用の生成物）
- `*.mp4`、`*_segments/`、`list.txt`、`*.part`
- 実データの m3u8 プレイリスト（`EXT-X-TOKEN` などのトークンを含む場合がある）
- ルートの `video-concat.ini`（利用者固有の設定。配布用は `packaging/video-concat.ini`）
- ダウンロードしたセグメント・生成物
- 秘密情報・API キー・Cookie

## 6. 利用できるツール・スキル

- `uv`（仮想環境・依存管理・実行）
- `ffmpeg`（結合・テスト用。配布版は同梱または自動ダウンロード）
- PyInstaller（配布版ビルド）
- Node.js 18 以上（モック HLS サーバー用。npm パッケージは不要）
- Chrome / Edge / Chromium（`--from-page` のブラウザ方式用。利用者環境のもの）
- スキル: `project-docs`（ドキュメント更新）、`commit`、`test`、`review`、
  `iniit-python-project`、`opencode`、`report`

## 7. 作業の進め方 (推奨フロー)

1. `handover.md` と関連ファイルを読み、現状と制約を把握する。
2. 変更を実装する（表示文言は日本語）。
3. `uv run pytest` と `uv run video-concat --help` で確認する。
4. 必要なら `packaging/fetch_ffmpeg.py` で ffmpeg を用意し、E2E を実行する。
   モック HLS サーバー（`tools/mock-hls-server/`）でも Referer と 403 を確認できる。
   `/player.html` を使うと `--from-page` の静的解析・ブラウザ方式も確認できる。
   Referer 検査を有効にしたまま `--from-page` を使う場合は、ページ URL も
   `--referer` に含める（または許可済みの Referer を明示する）。
5. Linux 配布版をビルドして `--help` / 引数なしヘルプ / E2E を確認する。
6. ドキュメント（README / docs / handover）を実装に追従させる。
7. 差分を確認しコミットする（push はユーザー確認後）。

## 8. ドキュメントの役割分担

| ファイル | 読者 | 内容 |
| --- | --- | --- |
| `README.md` | 利用者 | 配布版の起動方法・設定・操作手順 |
| `docs/details.md` | 保守・拡張担当 | 外部仕様・設定・エラー・テスト |
| `docs/design.md` | 開発者 | 実装・データフロー・設計判断 |
| `docs/build.md` | リリース担当 | ビルド・配布・ffmpeg 同梱 |
| `AGENTS.md` | AI エージェント | 作業制約・コマンド・規約 |
| `handover.md` | 次セッション | 現状・引き継ぎ事項 |

実装を変更したらドキュメントも追従させること（project-docs スキル）。
