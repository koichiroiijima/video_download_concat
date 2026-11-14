#!/usr/bin/env node
/**
 * video-concat の動作確認用モック HLS サーバー。
 *
 * Node.js の標準モジュールだけで動作する。配信ディレクトリのファイルを
 * そのまま返し、--referer が指定された場合は Referer が一致しない
 * リクエストを 403 で拒否する。
 */

import { createReadStream } from "node:fs";
import { stat } from "node:fs/promises";
import { createServer } from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_HOST = "127.0.0.1";
const DEFAULT_PORT = 8080;
const DEFAULT_MEDIA_DIR = path.join(__dirname, "media");

const HELP = `使い方: node server.mjs [オプション]

video-concat の動作確認用モック HLS サーバーです。
配信ディレクトリの .m3u8 / .ts を HTTP で返します。

オプション:
  --host HOST       待ち受けアドレス（既定: ${DEFAULT_HOST}）
  --port PORT       待ち受けポート。0 で空きポートを自動選択（既定: ${DEFAULT_PORT}）
  --dir DIR         配信ディレクトリ（既定: ${DEFAULT_MEDIA_DIR}）
  --referer URL     許可する Referer。複数指定可。未指定なら検査しない
  -q, --quiet       成功したリクエストのログを抑制する
  -h, --help        このヘルプを表示する

例:
  node server.mjs
  node server.mjs --port 8081
  node server.mjs --referer https://example.com/
  node server.mjs --referer https://example.com/ --referer https://example.com/watch/1

Referer の判定は完全一致です。Referer ヘッダーがない場合も 403 になります。
/ と /index.html と /health は Referer 検査の対象外です。
`;

function fail(message) {
  console.error(`エラー: ${message}`);
  process.exit(1);
}

function parseCli() {
  let values;
  try {
    ({ values } = parseArgs({
      options: {
        host: { type: "string", default: DEFAULT_HOST },
        port: { type: "string", default: String(DEFAULT_PORT) },
        dir: { type: "string" },
        referer: { type: "string", multiple: true },
        quiet: { type: "boolean", short: "q", default: false },
        help: { type: "boolean", short: "h", default: false },
      },
      allowPositionals: false,
    }));
  } catch (error) {
    fail(`${error instanceof Error ? error.message : String(error)}\n\n${HELP}`);
  }

  if (values.help) {
    console.log(HELP);
    process.exit(0);
  }

  const port = Number(values.port);
  if (!Number.isInteger(port) || port < 0 || port > 65535) {
    fail(`--port には 0〜65535 の整数を指定してください: ${values.port}`);
  }

  const allowedReferers = values.referer ?? [];
  for (const value of allowedReferers) {
    if (!value) {
      fail("--referer には空でない値を指定してください。");
    }
  }

  return {
    host: values.host,
    port,
    mediaDir: path.resolve(values.dir ?? DEFAULT_MEDIA_DIR),
    allowedReferers,
    quiet: values.quiet,
  };
}

function contentType(filePath) {
  switch (path.extname(filePath).toLowerCase()) {
    case ".m3u8":
      return "application/vnd.apple.mpegurl; charset=utf-8";
    case ".ts":
      return "video/mp2t";
    case ".mp4":
      return "video/mp4";
    case ".html":
      return "text/html; charset=utf-8";
    case ".json":
      return "application/json; charset=utf-8";
    default:
      return "application/octet-stream";
  }
}

function escapeHtml(value) {
  const replacements = {
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  };
  return String(value).replace(/[&<>"']/g, (ch) => replacements[ch]);
}

function displayHost(host) {
  if (host === "0.0.0.0") {
    return "127.0.0.1";
  }
  if (host === "::") {
    return "[::1]";
  }
  if (host.includes(":") && !host.startsWith("[")) {
    return `[${host}]`;
  }
  return host;
}

function buildIndexHtml(options, baseUrl) {
  const refererText =
    options.allowedReferers.length > 0
      ? options.allowedReferers.map(escapeHtml).join(", ")
      : "検査なし（すべて許可）";
  const refererNote =
    options.allowedReferers.length > 0
      ? `<p>ブラウザから直接開くと Referer が一致しないため 403 になります。
         動作確認は <code>curl</code> または video-concat から行ってください。</p>`
      : "";
  const refererHeader =
    options.allowedReferers.length > 0
      ? ` -H "Referer: ${escapeHtml(options.allowedReferers[0])}"`
      : "";
  const videoConcatHeader =
    options.allowedReferers.length > 0
      ? ` -H "Referer: ${escapeHtml(options.allowedReferers[0])}"`
      : "";

  return `<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <title>video-concat モック HLS サーバー</title>
  <style>
    body { font-family: sans-serif; margin: 2rem; line-height: 1.6; }
    code, pre { background: #f4f4f4; padding: 0.2rem 0.4rem; }
    pre { padding: 0.8rem; overflow-x: auto; }
  </style>
</head>
<body>
  <h1>video-concat モック HLS サーバー</h1>
  <p>配信ディレクトリ: <code>${escapeHtml(options.mediaDir)}</code></p>
  <p>Referer 検査: <code>${refererText}</code></p>
  ${refererNote}
  <h2>配信 URL</h2>
  <ul>
    <li><a href="/master.m3u8">/master.m3u8</a>（マスタープレイリスト）</li>
    <li><a href="/v0/playlist.m3u8">/v0/playlist.m3u8</a>（メディアプレイリスト）</li>
    <li><a href="/health">/health</a>（稼働確認）</li>
  </ul>
  <h2>動作確認</h2>
  <pre>curl${refererHeader} ${escapeHtml(baseUrl)}/v0/playlist.m3u8</pre>
  <pre>uv run video-concat "${escapeHtml(baseUrl)}/master.m3u8"${videoConcatHeader}</pre>
</body>
</html>
`;
}

const options = parseCli();

let mediaInfo;
try {
  mediaInfo = await stat(options.mediaDir);
} catch {
  console.error(`エラー: 配信ディレクトリが見つかりません: ${options.mediaDir}`);
  console.error("先にメディアを生成してください:");
  console.error("  node tools/mock-hls-server/generate-media.mjs");
  process.exit(1);
}
if (!mediaInfo.isDirectory()) {
  console.error(`エラー: --dir にはディレクトリを指定してください: ${options.mediaDir}`);
  process.exit(1);
}

function allowedRefererText() {
  const lines = options.allowedReferers.map((value) => `  - ${value}`);
  return `許可されている Referer:\n${lines.join("\n")}\n`;
}

async function handleRequest(req, res) {
  const started = process.hrtime.bigint();
  const method = req.method ?? "GET";
  const referer = req.headers.referer ?? "";
  let pathname = "/";
  let status = 500;
  let logged = false;

  const log = () => {
    if (logged) {
      return;
    }
    logged = true;
    const elapsed = Number(process.hrtime.bigint() - started) / 1e6;
    const line =
      `${new Date().toISOString()} ${status} ${method} ${pathname} ` +
      `referer=${referer || "(なし)"} ${elapsed.toFixed(1)}ms`;
    if (!options.quiet || status >= 400) {
      console.log(line);
    }
  };
  res.on("close", log);

  const sendText = (code, text, extraHeaders = {}) => {
    status = code;
    const body = Buffer.from(text, "utf-8");
    res.writeHead(code, {
      "Content-Type": "text/plain; charset=utf-8",
      "Content-Length": body.length,
      "Cache-Control": "no-store",
      ...extraHeaders,
    });
    if (method === "HEAD") {
      res.end();
    } else {
      res.end(body);
    }
  };

  const sendHtml = (html) => {
    status = 200;
    const body = Buffer.from(html, "utf-8");
    res.writeHead(200, {
      "Content-Type": "text/html; charset=utf-8",
      "Content-Length": body.length,
      "Cache-Control": "no-store",
    });
    if (method === "HEAD") {
      res.end();
    } else {
      res.end(body);
    }
  };

  try {
    try {
      pathname = decodeURIComponent(new URL(req.url ?? "/", "http://localhost").pathname);
    } catch {
      sendText(400, "400 Bad Request: URL が不正です。\n");
      return;
    }

    if (method !== "GET" && method !== "HEAD") {
      sendText(405, "405 Method Not Allowed: GET / HEAD のみ対応しています。\n", {
        Allow: "GET, HEAD",
      });
      return;
    }

    const baseUrl = `http://${displayHost(options.host)}:${req.socket.localPort}`;

    if (pathname === "/health") {
      status = 200;
      const body = Buffer.from(`${JSON.stringify({ ok: true })}\n`, "utf-8");
      res.writeHead(200, {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": body.length,
        "Cache-Control": "no-store",
      });
      res.end(method === "HEAD" ? undefined : body);
      return;
    }

    if (pathname === "/" || pathname === "/index.html") {
      sendHtml(buildIndexHtml(options, baseUrl));
      return;
    }

    if (options.allowedReferers.length > 0) {
      if (!referer) {
        sendText(403, `403 Forbidden: Referer ヘッダーがありません。\n${allowedRefererText()}`);
        return;
      }
      if (!options.allowedReferers.includes(referer)) {
        sendText(
          403,
          `403 Forbidden: Referer が許可されていません: ${referer}\n${allowedRefererText()}`,
        );
        return;
      }
    }

    const relative = pathname.replace(/^\/+/, "");
    const filePath = path.resolve(options.mediaDir, relative);
    if (
      filePath !== options.mediaDir &&
      !filePath.startsWith(options.mediaDir + path.sep)
    ) {
      sendText(403, "403 Forbidden: 配信ディレクトリ外のパスは参照できません。\n");
      return;
    }

    let info;
    try {
      info = await stat(filePath);
    } catch {
      sendText(404, `404 Not Found: ${pathname}\n`);
      return;
    }
    if (!info.isFile()) {
      sendText(404, `404 Not Found: ${pathname}\n`);
      return;
    }

    status = 200;
    res.writeHead(200, {
      "Content-Type": contentType(filePath),
      "Content-Length": info.size,
      "Cache-Control": "no-store",
      "Last-Modified": info.mtime.toUTCString(),
    });
    if (method === "HEAD") {
      res.end();
      return;
    }

    const stream = createReadStream(filePath);
    stream.on("error", (error) => {
      console.error(`読み込みエラー: ${filePath}: ${error.message}`);
      res.destroy(error);
    });
    stream.pipe(res);
  } catch (error) {
    console.error(
      `リクエスト処理エラー: ${error instanceof Error ? error.stack : error}`,
    );
    if (!res.headersSent) {
      try {
        sendText(500, "500 Internal Server Error\n");
      } catch {
        res.destroy();
      }
    } else {
      res.destroy();
    }
  }
}

const server = createServer((req, res) => {
  void handleRequest(req, res);
});

server.on("clientError", (error, socket) => {
  if (socket.writable) {
    socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
  }
  console.error(`クライアントエラー: ${error.message}`);
});

server.on("error", (error) => {
  if (error.code === "EADDRINUSE") {
    console.error(
      `エラー: ポート ${options.port} は使用中です。--port で別のポートを指定してください。`,
    );
  } else {
    console.error(`サーバーエラー: ${error.message}`);
  }
  process.exit(1);
});

server.listen(options.port, options.host, () => {
  const address = server.address();
  const actualPort =
    typeof address === "object" && address !== null ? address.port : options.port;
  const baseUrl = `http://${displayHost(options.host)}:${actualPort}`;

  console.log("モック HLS サーバーを起動しました");
  console.log(`  ベース URL: ${baseUrl}/`);
  console.log(`  マスター:   ${baseUrl}/master.m3u8`);
  console.log(`  メディア:   ${baseUrl}/v0/playlist.m3u8`);
  console.log(`  配信元:     ${options.mediaDir}`);
  if (options.allowedReferers.length > 0) {
    console.log("  Referer 検査: 有効（完全一致）");
    for (const value of options.allowedReferers) {
      console.log(`    許可: ${value}`);
    }
  } else {
    console.log("  Referer 検査: 無効（すべて許可）");
  }
  console.log("");
  console.log("video-concat の実行例:");
  const header =
    options.allowedReferers.length > 0
      ? ` -H "Referer: ${options.allowedReferers[0]}"`
      : "";
  console.log(`  uv run video-concat "${baseUrl}/master.m3u8"${header}`);
  console.log("");
  console.log("終了: Ctrl+C");
});

for (const signal of ["SIGINT", "SIGTERM"]) {
  process.on(signal, () => {
    console.log("\nサーバーを停止します。");
    server.close(() => process.exit(0));
    setTimeout(() => process.exit(0), 3000).unref();
  });
}
