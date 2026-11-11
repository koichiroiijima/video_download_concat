#!/usr/bin/env node
/**
 * モック HLS サーバー用のメディアを生成する。
 *
 * ffmpeg で 1 秒程度の MPEG-TS セグメントを複数作り、
 * master.m3u8 と v0/playlist.m3u8 を書き出す。
 */

import { spawnSync } from "node:child_process";
import { mkdir, readdir, stat, unlink, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const DEFAULT_OUTPUT = path.join(__dirname, "media");

const HELP = `使い方: node generate-media.mjs [オプション]

ffmpeg でモック HLS 用の MPEG-TS セグメントとプレイリストを生成します。

オプション:
  -o, --output DIR  出力先（既定: ${DEFAULT_OUTPUT}）
  --segments N      セグメント数（既定: 6）
  --duration SEC    1 セグメントの秒数（既定: 1）
  --width PX        映像幅（既定: 160）
  --height PX       映像高さ（既定: 120）
  --fps N           フレームレート（既定: 10）
  --ffmpeg PATH     ffmpeg の実行パス
                    （未指定なら VIDEO_CONCAT_TEST_FFMPEG、vendor、PATH の順）
  -h, --help        このヘルプを表示する

例:
  node tools/mock-hls-server/generate-media.mjs
  node tools/mock-hls-server/generate-media.mjs --segments 10 --duration 2
`;

function fail(message) {
  console.error(`エラー: ${message}`);
  process.exit(1);
}

function parsePositiveInt(name, raw, { min = 1, max = Number.MAX_SAFE_INTEGER } = {}) {
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    fail(`${name} には ${min}〜${max} の整数を指定してください: ${raw}`);
  }
  return value;
}

function parsePositiveNumber(name, raw) {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) {
    fail(`${name} には 0 より大きい数値を指定してください: ${raw}`);
  }
  return value;
}

function parseCli() {
  let values;
  try {
    ({ values } = parseArgs({
      options: {
        output: { type: "string", short: "o" },
        segments: { type: "string", default: "6" },
        duration: { type: "string", default: "1" },
        width: { type: "string", default: "160" },
        height: { type: "string", default: "120" },
        fps: { type: "string", default: "10" },
        ffmpeg: { type: "string" },
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

  return {
    output: path.resolve(values.output ?? DEFAULT_OUTPUT),
    segments: parsePositiveInt("--segments", values.segments, { max: 1000 }),
    duration: parsePositiveNumber("--duration", values.duration),
    width: parsePositiveInt("--width", values.width, { max: 7680 }),
    height: parsePositiveInt("--height", values.height, { max: 4320 }),
    fps: parsePositiveInt("--fps", values.fps, { max: 120 }),
    ffmpeg: values.ffmpeg,
  };
}

function works(candidate) {
  const result = spawnSync(candidate, ["-version"], {
    stdio: "ignore",
    windowsHide: true,
  });
  return !result.error && result.status === 0;
}

function resolveFfmpeg(explicit) {
  if (explicit) {
    if (!works(explicit)) {
      fail(`ffmpeg を実行できません: ${explicit}`);
    }
    return explicit;
  }

  const candidates = [];
  if (process.env.VIDEO_CONCAT_TEST_FFMPEG) {
    candidates.push(process.env.VIDEO_CONCAT_TEST_FFMPEG);
  }
  if (process.platform === "win32") {
    candidates.push(
      path.join(ROOT, "vendor", "ffmpeg", "windows-x86_64", "ffmpeg.exe"),
      path.join(ROOT, "vendor", "ffmpeg", "ffmpeg.exe"),
      "ffmpeg.exe",
      "ffmpeg",
    );
  } else {
    candidates.push(
      path.join(ROOT, "vendor", "ffmpeg", "linux-x86_64", "ffmpeg"),
      path.join(ROOT, "vendor", "ffmpeg", "ffmpeg"),
      "ffmpeg",
    );
  }

  for (const candidate of candidates) {
    if (works(candidate)) {
      return candidate;
    }
  }

  fail(
    "ffmpeg が見つかりません。--ffmpeg で指定するか、PATH に ffmpeg を入れてください。\n" +
      "  Windows: .\\packaging\\build.ps1 -FetchFfmpeg\n" +
      "  Linux:   uv run python packaging/fetch_ffmpeg.py",
  );
}

const options = parseCli();
const ffmpeg = resolveFfmpeg(options.ffmpeg);
const segmentDir = path.join(options.output, "v0");

await mkdir(segmentDir, { recursive: true });

// 以前に生成したセグメントだけを削除する（無関係なファイルは残す）。
for (const name of await readdir(segmentDir)) {
  if (/^segment\d+\.ts$/.test(name)) {
    await unlink(path.join(segmentDir, name));
  }
}

console.log(`ffmpeg: ${ffmpeg}`);
console.log(`出力先: ${options.output}`);
console.log(`${options.segments} 個のセグメントを生成します。`);

const segmentNames = [];
for (let index = 0; index < options.segments; index += 1) {
  const name = `segment${String(index).padStart(3, "0")}.ts`;
  const target = path.join(segmentDir, name);
  const frequency = 440 + index * 40;
  const args = [
    "-hide_banner",
    "-loglevel",
    "error",
    "-y",
    "-f",
    "lavfi",
    "-i",
    `testsrc=size=${options.width}x${options.height}:rate=${options.fps}`,
    "-f",
    "lavfi",
    "-i",
    `sine=frequency=${frequency}:sample_rate=44100`,
    "-t",
    String(options.duration),
    "-c:v",
    "mpeg4",
    "-q:v",
    "5",
    "-g",
    String(options.fps),
    "-c:a",
    "aac",
    "-f",
    "mpegts",
    target,
  ];

  const result = spawnSync(ffmpeg, args, {
    stdio: ["ignore", "ignore", "inherit"],
    windowsHide: true,
  });
  if (result.error) {
    fail(`ffmpeg を実行できません: ${result.error.message}`);
  }
  if (result.status !== 0) {
    fail(`ffmpeg が終了コード ${result.status} で失敗しました: ${name}`);
  }

  const info = await stat(target);
  if (info.size === 0) {
    fail(`生成したセグメントが空です: ${target}`);
  }

  segmentNames.push(name);
  console.log(`  ${name} (${info.size} バイト)`);
}

const targetDuration = Math.max(1, Math.ceil(options.duration));
const playlistLines = [
  "#EXTM3U",
  "#EXT-X-VERSION:3",
  `#EXT-X-TARGETDURATION:${targetDuration}`,
  "#EXT-X-MEDIA-SEQUENCE:0",
  "#EXT-X-PLAYLIST-TYPE:VOD",
];
for (const name of segmentNames) {
  playlistLines.push(`#EXTINF:${options.duration.toFixed(3)},`);
  playlistLines.push(name);
}
playlistLines.push("#EXT-X-ENDLIST", "");
await writeFile(
  path.join(segmentDir, "playlist.m3u8"),
  playlistLines.join("\n"),
  "utf-8",
);

const bandwidth = Math.max(
  200000,
  Math.round(options.width * options.height * options.fps * 1.5),
);
const masterLines = [
  "#EXTM3U",
  "#EXT-X-VERSION:3",
  `#EXT-X-STREAM-INF:BANDWIDTH=${bandwidth},RESOLUTION=${options.width}x${options.height}`,
  "v0/playlist.m3u8",
  "",
];
await writeFile(
  path.join(options.output, "master.m3u8"),
  masterLines.join("\n"),
  "utf-8",
);

console.log("");
console.log("生成しました:");
console.log(`  ${path.join(options.output, "master.m3u8")}`);
console.log(`  ${path.join(segmentDir, "playlist.m3u8")}`);
console.log(`  ${segmentDir}${path.sep}${segmentNames[0]} ... (${segmentNames.length} 個)`);
console.log("");
console.log("次のコマンドでサーバーを起動できます:");
console.log(
  "  node tools/mock-hls-server/server.mjs --referer https://example.com/",
);
