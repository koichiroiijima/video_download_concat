# モック HLS サーバー

`video-concat` の HTTP ダウンロードと Referer 検査を、実際の配信元を使わずに
確認するためのテスト用サーバーです。Node.js の標準モジュールだけで動作し、
npm パッケージのインストールは不要です。

## 1. 必要環境

- Node.js 18 以上
- ffmpeg（テスト用の TS セグメントを生成するときだけ使用）

ffmpeg は次の順で探します。

1. `--ffmpeg PATH`
2. 環境変数 `VIDEO_CONCAT_TEST_FFMPEG`
3. `vendor/ffmpeg/linux-x86_64/ffmpeg`（Windows は `vendor/ffmpeg/windows-x86_64/ffmpeg.exe`）
4. `PATH` 上の `ffmpeg`

## 2. 使い方

### メディアを生成する

```bash
node tools/mock-hls-server/generate-media.mjs
```

既定では `tools/mock-hls-server/media/` に次のファイルを作ります。

```text
media/
├── master.m3u8          マスタープレイリスト
└── v0/
    ├── playlist.m3u8    メディアプレイリスト
    ├── segment000.ts
    ├── segment001.ts
    └── ...              （既定 6 個、各 1 秒）
```

ffmpeg の場所を明示する場合:

```bash
node tools/mock-hls-server/generate-media.mjs \
  --ffmpeg vendor/ffmpeg/linux-x86_64/ffmpeg
```

主なオプション:

| オプション | 既定値 | 内容 |
| --- | --- | --- |
| `-o, --output DIR` | `tools/mock-hls-server/media` | 出力先 |
| `--segments N` | `6` | セグメント数 |
| `--duration SEC` | `1` | 1 セグメントの秒数 |
| `--width PX` / `--height PX` | `160` / `120` | 映像サイズ |
| `--fps N` | `10` | フレームレート |
| `--ffmpeg PATH` | 自動探索 | ffmpeg の実行パス |

### サーバーを起動する

```bash
# Referer 検査なし（すべて許可）
node tools/mock-hls-server/server.mjs

# https://example.com/ に完全一致する Referer だけ許可
node tools/mock-hls-server/server.mjs --referer https://example.com/
```

起動すると、次の URL が表示されます。

- `http://127.0.0.1:8080/master.m3u8`
- `http://127.0.0.1:8080/v0/playlist.m3u8`
- `http://127.0.0.1:8080/health`

`--port 0` を指定すると空きポートを自動選択します。

### Referer の動作

`--referer` を 1 つ以上指定すると、`/`、`/index.html`、`/health` 以外のすべての
リクエストで Referer を検査します。

| リクエスト | 結果 |
| --- | --- |
| Referer が許可値と完全一致 | 200 |
| Referer が未指定 | 403（`Referer ヘッダーがありません`） |
| Referer が不一致 | 403（`Referer が許可されていません`） |

複数の Referer を許可する場合:

```bash
node tools/mock-hls-server/server.mjs \
  --referer https://example.com/ \
  --referer https://example.com/watch/1
```

### video-concat から実行する

```bash
uv run video-concat "http://127.0.0.1:8080/master.m3u8" \
  -H "Referer: https://example.com/" \
  --ffmpeg vendor/ffmpeg/linux-x86_64/ffmpeg \
  -o /tmp/mock-out.mp4
```

設定ファイルで Referer を指定する場合は、作業ディレクトリに
`video-concat.ini` を置きます。

```ini
[http]
referer = https://example.com/
```

Referer を付けずに実行すると、配信元が 403 を返すため
`video-concat` はエラー終了します。拒否の動作確認に使えます。

## 3. サーバーのオプション

| オプション | 既定値 | 内容 |
| --- | --- | --- |
| `--host HOST` | `127.0.0.1` | 待ち受けアドレス |
| `--port PORT` | `8080` | 待ち受けポート（0 で自動選択） |
| `--dir DIR` | `tools/mock-hls-server/media` | 配信ディレクトリ |
| `--referer URL` | なし | 許可する Referer。複数指定可 |
| `-q, --quiet` | 無効 | 成功時のアクセスログを抑制 |
| `-h, --help` | — | ヘルプを表示 |

## 4. 確認例

```bash
# Referer なし → 403
curl -i http://127.0.0.1:8080/v0/playlist.m3u8

# 正しい Referer → 200
curl -i -H "Referer: https://example.com/" \
  http://127.0.0.1:8080/v0/playlist.m3u8

# 間違った Referer → 403
curl -i -H "Referer: https://other.example/" \
  http://127.0.0.1:8080/v0/playlist.m3u8
```

## 5. 注意

- テスト専用です。既定では `127.0.0.1` でのみ待ち受けます。
- 配信ディレクトリ外へのパス参照は 403 で拒否します。
- 生成した `media/` は `.gitignore` の対象です。コミットしないでください。
- `master.m3u8` と `v0/playlist.m3u8` は `generate-media.mjs` が上書きします。
  再生成時は、以前に生成した `segment*.ts` だけを削除します。
