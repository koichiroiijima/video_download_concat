"""Chrome / Edge を CDP で操作して m3u8 の通信を検出する。

Python の実行時依存を増やさないため、WebSocket クライアントは
``ws.py`` の標準ライブラリ実装を使う。ブラウザ本体は利用者環境の
Edge / Chrome / Chromium、または Playwright 同梱の Chromium を起動する。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import quote

from .headers import canonical_header
from .page_source import (
    CandidateHint,
    DiscoveryResult,
    probe_candidates,
)
from .ws import WebSocket, WebSocketError

logger = logging.getLogger(__name__)

HLS_MIME_TYPES = {
    "application/vnd.apple.mpegurl",
    "application/x-mpegurl",
    "application/mpegurl",
    "audio/mpegurl",
    "audio/x-mpegurl",
}

_PLAY_JS = """
(() => {
  const done = [];
  const video = document.querySelector('video');
  if (video) {
    try { video.muted = true; } catch (error) {}
    try {
      const promise = video.play();
      if (promise && typeof promise.catch === 'function') {
        promise.catch(() => {});
      }
      done.push('video.play');
    } catch (error) {}
    try { video.click(); } catch (error) {}
  }
  const selectors = [
    '.vjs-big-play-button',
    'button[aria-label*="Play" i]',
    'button[aria-label*="\\u518d\\u751f"]',
    '[class*="play-button" i]',
    '[class*="PlayButton"]',
    'button[title*="Play" i]'
  ];
  for (const selector of selectors) {
    try {
      const element = document.querySelector(selector);
      if (element) {
        element.click();
        done.push(selector);
        break;
      }
    } catch (error) {}
  }
  return done;
})()
""".strip()


class BrowserError(RuntimeError):
    """ブラウザの起動・CDP 通信に失敗したときに送出する。"""


class CdpError(RuntimeError):
    """CDP コマンドが失敗したときに送出する。"""


# Playwright が ``npx playwright install`` で配置する Chromium の
# バージョンディレクトリ（``chromium-<revision>``）配下の実行ファイル。
# ``chrome-headless-shell`` は ``--headless`` を受け付けないため対象外。
_PLAYWRIGHT_CHROMIUM_PATHS = {
    "windows": (
        Path("chrome-win64") / "chrome.exe",
        Path("chrome-win") / "chrome.exe",
        Path("chrome-win32") / "chrome.exe",
    ),
    "macos": (
        Path("chrome-mac") / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
        Path("chrome-mac-arm64")
        / "Chromium.app"
        / "Contents"
        / "MacOS"
        / "Chromium",
        Path("chrome-mac-x64") / "Chromium.app" / "Contents" / "MacOS" / "Chromium",
    ),
    "linux": (
        Path("chrome-linux64") / "chrome",
        Path("chrome-linux") / "chrome",
    ),
}


def _playwright_browsers_roots() -> list[Path]:
    """Playwright のブラウザ配置ディレクトリの候補を返す。"""
    roots: list[Path] = []
    env = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if env and env != "0":
        roots.append(Path(env).expanduser())
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            roots.append(Path(local_app_data) / "ms-playwright")
    elif sys.platform == "darwin":
        roots.append(Path.home() / "Library" / "Caches" / "ms-playwright")
    else:
        roots.append(Path.home() / ".cache" / "ms-playwright")
    return roots


def _playwright_chromium_relative_paths() -> tuple[Path, ...]:
    """現在の OS で使う Playwright Chromium の相対パスを返す。"""
    if os.name == "nt":
        return _PLAYWRIGHT_CHROMIUM_PATHS["windows"]
    if sys.platform == "darwin":
        return _PLAYWRIGHT_CHROMIUM_PATHS["macos"]
    return _PLAYWRIGHT_CHROMIUM_PATHS["linux"]


def _playwright_revision(name: str) -> int:
    """``chromium-1248`` のような名前からリビジョン番号を取り出す。"""
    _, _, suffix = name.rpartition("-")
    try:
        return int(suffix)
    except ValueError:
        return -1


def _playwright_browser_path() -> str | None:
    """Playwright 同梱の Chromium の実行パスを探す。"""
    relatives = _playwright_chromium_relative_paths()
    for root in _playwright_browsers_roots():
        if not root.is_dir():
            continue
        try:
            directories = [
                child
                for child in root.iterdir()
                if child.is_dir() and child.name.startswith("chromium-")
            ]
        except OSError:
            continue
        directories.sort(
            key=lambda path: _playwright_revision(path.name), reverse=True
        )
        for directory in directories:
            for relative in relatives:
                path = directory / relative
                if path.is_file():
                    return str(path)
    return None


def find_browser(explicit: str | Path | None = None) -> str:
    """Edge / Chrome / Chromium の実行パスを探す。"""
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(str(explicit))
        if found:
            return found
        raise BrowserError(f"ブラウザが見つかりません: {explicit}")

    env = os.environ.get("VIDEO_CONCAT_BROWSER")
    if env:
        path = Path(env).expanduser()
        if path.is_file():
            return str(path)
        found = shutil.which(env)
        if found:
            return found
        raise BrowserError(
            f"VIDEO_CONCAT_BROWSER のブラウザが見つかりません: {env}"
        )

    if os.name == "nt":
        bases = [
            os.environ.get("ProgramFiles(x86)"),
            os.environ.get("ProgramFiles"),
            os.environ.get("LOCALAPPDATA"),
        ]
        relatives = (
            "Microsoft/Edge/Application/msedge.exe",
            "Google/Chrome/Application/chrome.exe",
            "Chromium/Application/chrome.exe",
        )
        for base in bases:
            if not base:
                continue
            for relative in relatives:
                path = Path(base) / relative
                if path.is_file():
                    return str(path)
        for name in ("msedge", "chrome"):
            found = shutil.which(name)
            if found:
                return found
    else:
        for name in (
            "microsoft-edge",
            "microsoft-edge-stable",
            "google-chrome",
            "google-chrome-stable",
            "chromium",
            "chromium-browser",
            "chrome",
        ):
            found = shutil.which(name)
            if found:
                return found

    playwright = _playwright_browser_path()
    if playwright:
        logger.debug("Playwright の Chromium を使います: %s", playwright)
        return playwright

    raise BrowserError(
        "Chrome / Edge / Chromium（または Playwright の Chromium）が"
        "見つかりません。インストールするか --browser-path で実行パスを"
        "指定してください。"
    )


def _local_json(
    port: int,
    path: str,
    *,
    method: str = "GET",
    timeout: float = 10.0,
) -> object:
    """ブラウザのローカル HTTP エンドポイントを読む（プロキシを使わない）。"""
    url = f"http://127.0.0.1:{port}{path}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(url, method=method)
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


class _Closed:
    def __init__(self, error: Exception) -> None:
        self.error = error


class CdpConnection:
    """CDP のコマンド送信とイベント処理を仲介する。"""

    def __init__(self, ws: WebSocket) -> None:
        self._ws = ws
        self._queue: queue.Queue[object] = queue.Queue()
        self._handlers: dict[str, Callable[[dict], None]] = {}
        self._responses: dict[object, dict] = {}
        self._next_id = 0
        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()

    def _reader(self) -> None:
        try:
            while True:
                self._queue.put(self._ws.recv_message())
        except Exception as exc:  # noqa: BLE001 - 終了を待ち側へ伝える
            self._queue.put(_Closed(exc))

    def on(self, method: str, handler: Callable[[dict], None]) -> None:
        self._handlers[method] = handler

    def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if method:
            handler = self._handlers.get(method)
            if handler is not None:
                handler(message.get("params") or {})
        elif "id" in message:
            self._responses[message.get("id")] = message

    def _next_message(self, timeout: float) -> dict | None:
        """次の CDP メッセージを返す。時間内に届かなければ ``None`` を返す。"""
        try:
            item = self._queue.get(timeout=timeout)
        except queue.Empty:
            return None
        if isinstance(item, _Closed):
            raise CdpError(f"ブラウザとの接続が切れました: {item.error}")
        if not isinstance(item, str):
            raise CdpError("CDP の応答が不正です")
        try:
            message = json.loads(item)
        except json.JSONDecodeError as exc:
            raise CdpError("CDP の応答を JSON として解釈できません") from exc
        if not isinstance(message, dict):
            raise CdpError("CDP の応答が不正です")
        return message

    def send(
        self,
        method: str,
        params: dict | None = None,
        *,
        timeout: float = 30.0,
    ) -> dict:
        self._next_id += 1
        command_id = self._next_id
        payload: dict[str, object] = {"id": command_id, "method": method}
        if params:
            payload["params"] = params
        self._ws.send_text(json.dumps(payload, ensure_ascii=False))

        deadline = time.monotonic() + timeout
        while True:
            if command_id in self._responses:
                response = self._responses.pop(command_id)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CdpError(f"CDP コマンドがタイムアウトしました: {method}")
            message = self._next_message(remaining)
            if message is None:
                raise CdpError(f"CDP コマンドがタイムアウトしました: {method}")
            if "method" in message:
                self._dispatch(message)
            else:
                self._responses[message.get("id")] = message

        if "error" in response:
            raise CdpError(f"CDP エラー ({method}): {response['error']}")
        result = response.get("result")
        return result if isinstance(result, dict) else {}

    def pump(self, duration: float) -> None:
        """指定秒数のあいだイベントを処理する。"""
        deadline = time.monotonic() + max(0.0, duration)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            message = self._next_message(remaining)
            if message is None:
                return
            if "method" in message:
                self._dispatch(message)
            else:
                self._responses[message.get("id")] = message

    def close(self) -> None:
        self._ws.close()
        self._thread.join(timeout=2)


class BrowserSession:
    """ブラウザを起動し、ページの通信から m3u8 を探す。"""

    def __init__(
        self,
        executable: str,
        *,
        headed: bool = False,
        user_data_dir: str | Path | None = None,
        user_agent: str | None = None,
        timeout: float = 30.0,
        startup_timeout: float = 15.0,
    ) -> None:
        self.executable = str(executable)
        self.headed = headed
        self.user_agent = user_agent
        self.timeout = timeout
        self.startup_timeout = startup_timeout

        self._owns_dir = user_data_dir is None
        if user_data_dir is None:
            self.user_data_dir = Path(
                tempfile.mkdtemp(prefix="video-concat-browser-")
            )
        else:
            self.user_data_dir = Path(user_data_dir).expanduser().resolve()
            self.user_data_dir.mkdir(parents=True, exist_ok=True)

        self.process: subprocess.Popen | None = None
        self.ws: WebSocket | None = None
        self.connection: CdpConnection | None = None
        self.port: int | None = None
        self._stderr_path: Path | None = None
        self._launched_at = 0.0

        self._request_urls: dict[str, str] = {}
        self._request_headers: dict[str, dict[str, str]] = {}
        self._extra_headers: dict[str, dict[str, str]] = {}
        self._candidate_urls: dict[str, str] = {}
        self._candidate_order: list[str] = []
        self._loaded = False
        self._navigated = False
        self.main_frame_url = ""

    def __enter__(self) -> "BrowserSession":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    def start(self) -> None:
        try:
            try:
                self._launch("--headless=new")
                self.port = self._wait_for_port()
            except BrowserError:
                if self.headed:
                    raise
                self._terminate_process()
                self._launch("--headless")
                self.port = self._wait_for_port()

            target = self._page_target()
            ws_url = target.get("webSocketDebuggerUrl")
            if not isinstance(ws_url, str):
                raise BrowserError(
                    "ブラウザのデバッグ用 WebSocket URL を取得できませんでした"
                )
            self.ws = WebSocket(ws_url, timeout=self.timeout)
            self.connection = CdpConnection(self.ws)
            self._register_handlers()
            self.connection.send("Page.enable")
            self.connection.send("Network.enable")
            self.connection.send("Runtime.enable")
            self.connection.send(
                "Network.setCacheDisabled", {"cacheDisabled": True}
            )
        except Exception:
            self.close()
            raise

    def _launch(self, headless_flag: str) -> None:
        arguments = [self.executable]
        if not self.headed:
            arguments.append(headless_flag)
        arguments.extend(
            [
                "--remote-debugging-port=0",
                f"--user-data-dir={self.user_data_dir}",
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-extensions",
                "--disable-default-apps",
                "--autoplay-policy=no-user-gesture-required",
                "--mute-audio",
                "--window-size=1280,720",
            ]
        )
        if os.name != "nt" and hasattr(os, "geteuid") and os.geteuid() == 0:
            arguments.append("--no-sandbox")
        if os.name != "nt":
            arguments.append("--disable-dev-shm-usage")
        if self.user_agent:
            arguments.append(f"--user-agent={self.user_agent}")
        arguments.append("about:blank")

        fd, log_path = tempfile.mkstemp(
            prefix="video-concat-browser-", suffix=".log"
        )
        os.close(fd)
        self._stderr_path = Path(log_path)
        creationflags = 0
        if os.name == "nt" and not self.headed:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        with self._stderr_path.open("wb") as stderr:
            self.process = subprocess.Popen(
                arguments,
                stdout=subprocess.DEVNULL,
                stderr=stderr,
                creationflags=creationflags,
            )
        self._launched_at = time.time()

    def _wait_for_port(self) -> int:
        port_path = self.user_data_dir / "DevToolsActivePort"
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                tail = self._stderr_tail()
                raise BrowserError(
                    "ブラウザが起動直後に終了しました"
                    f"（終了コード {self.process.returncode}）。{tail}"
                )
            try:
                lines = port_path.read_text(encoding="utf-8").splitlines()
                installed_at = port_path.stat().st_mtime
            except OSError:
                lines = []
                installed_at = 0.0
            if lines:
                try:
                    port = int(lines[0].strip())
                except ValueError:
                    port = 0
                if port > 0 and installed_at >= self._launched_at - 1.0:
                    return port
            time.sleep(0.1)

        tail = self._stderr_tail()
        raise BrowserError(
            "ブラウザのデバッグポートに接続できませんでした。"
            "ブラウザを終了してから再実行するか、--headed で原因を確認してください。"
            + tail
        )

    def _page_target(self) -> dict:
        assert self.port is not None
        targets = _local_json(self.port, "/json/list")
        if isinstance(targets, list):
            for target in targets:
                if (
                    isinstance(target, dict)
                    and target.get("type") == "page"
                    and target.get("webSocketDebuggerUrl")
                ):
                    return target

        path = "/json/new?" + quote("about:blank", safe="")
        try:
            created = _local_json(self.port, path, method="PUT")
        except urllib.error.HTTPError as exc:
            if exc.code != 405:
                raise
            created = _local_json(self.port, path, method="GET")
        if not isinstance(created, dict):
            raise BrowserError("ブラウザのページを作成できませんでした")
        return created

    def _register_handlers(self) -> None:
        if self.connection is None:
            raise BrowserError("CDP 接続がありません")
        self.connection.on("Network.requestWillBeSent", self._on_request)
        self.connection.on(
            "Network.requestWillBeSentExtraInfo", self._on_request_extra
        )
        self.connection.on("Network.responseReceived", self._on_response)
        self.connection.on("Page.frameNavigated", self._on_frame_navigated)
        self.connection.on("Page.loadEventFired", self._on_load)

    def _on_request(self, params: dict) -> None:
        request_id = str(params.get("requestId") or "")
        request = params.get("request") or {}
        if not isinstance(request, dict):
            return
        url = request.get("url")
        if isinstance(url, str):
            self._request_urls[request_id] = url
        headers = request.get("headers")
        if isinstance(headers, dict):
            self._request_headers[request_id] = {
                str(name).lower(): str(value)
                for name, value in headers.items()
            }

    def _on_request_extra(self, params: dict) -> None:
        request_id = str(params.get("requestId") or "")
        headers = params.get("headers")
        if isinstance(headers, dict):
            self._extra_headers[request_id] = {
                str(name).lower(): str(value)
                for name, value in headers.items()
            }

    def _on_response(self, params: dict) -> None:
        request_id = str(params.get("requestId") or "")
        response = params.get("response") or {}
        if not isinstance(response, dict):
            return
        url = response.get("url") or self._request_urls.get(request_id)
        if not isinstance(url, str) or not url:
            return
        mime = str(response.get("mimeType") or "").lower()
        if ".m3u8" not in url.lower() and mime not in HLS_MIME_TYPES:
            return
        if not url.startswith(("http://", "https://")):
            return
        key = request_id or url
        if key not in self._candidate_urls:
            self._candidate_urls[key] = url
            self._candidate_order.append(key)

    def _on_frame_navigated(self, params: dict) -> None:
        frame = params.get("frame") or {}
        if not isinstance(frame, dict) or frame.get("parentId"):
            return
        url = frame.get("url")
        if isinstance(url, str) and url and url != "about:blank":
            self.main_frame_url = url
            self._navigated = True

    def _on_load(self, params: dict) -> None:
        self._loaded = True

    def _evaluate(self, expression: str, *, user_gesture: bool) -> object:
        if self.connection is None:
            raise CdpError("CDP 接続がありません")
        result = self.connection.send(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": False,
                "userGesture": user_gesture,
            },
        )
        return (result.get("result") or {}).get("value")

    def _trigger_play(self, click_selector: str | None) -> None:
        try:
            self._evaluate(_PLAY_JS, user_gesture=True)
        except CdpError as exc:
            logger.debug("再生操作に失敗しました: %s", exc)
        if click_selector:
            self._click(click_selector)

    def _click(self, selector: str) -> None:
        expression = (
            "(() => { const element = document.querySelector("
            f"{json.dumps(selector)});"
            " if (!element) return false; element.click(); return true; })()"
        )
        try:
            self._evaluate(expression, user_gesture=True)
        except CdpError as exc:
            logger.debug("クリックに失敗しました: %s", exc)

    def _cookies_for(self, url: str) -> str:
        if self.connection is None:
            return ""
        try:
            result = self.connection.send(
                "Network.getCookies", {"urls": [url]}, timeout=10.0
            )
        except CdpError:
            return ""
        cookies = result.get("cookies") or []
        parts = []
        for cookie in cookies:
            if not isinstance(cookie, dict):
                continue
            name = cookie.get("name")
            value = cookie.get("value")
            if name:
                parts.append(f"{name}={value if value is not None else ''}")
        return "; ".join(parts)

    def _captured_headers(self, key: str, url: str) -> dict[str, str]:
        captured = self._extra_headers.get(key)
        if captured is None:
            captured = self._request_headers.get(key, {})
        headers: dict[str, str] = {}
        for name in ("referer", "origin", "cookie"):
            value = captured.get(name)
            if value:
                headers[canonical_header(name)] = value
        user_agent = captured.get("user-agent")
        if user_agent:
            headers["User-Agent"] = user_agent
        headers.setdefault("Referer", self.main_frame_url)
        if "Cookie" not in headers:
            cookie = self._cookies_for(url)
            if cookie:
                headers["Cookie"] = cookie
        return headers

    def discover(
        self,
        page_url: str,
        *,
        headers: dict[str, str] | None = None,
        page_wait: float = 5.0,
        click_selector: str | None = None,
    ) -> DiscoveryResult:
        if self.connection is None:
            raise BrowserError("CDP 接続がありません")
        self.main_frame_url = page_url
        result = self.connection.send(
            "Page.navigate", {"url": page_url}, timeout=self.timeout
        )
        error_text = result.get("errorText")
        if error_text:
            raise BrowserError(f"ページを開けませんでした: {error_text}")

        deadline = time.monotonic() + max(10.0, min(self.timeout, 60.0))
        while time.monotonic() < deadline:
            self.connection.pump(0.2)
            if self._loaded and self._navigated:
                break

        self._trigger_play(click_selector)
        self.connection.pump(page_wait)
        if not self._candidate_order:
            self._trigger_play(click_selector)
            self.connection.pump(page_wait)

        hints = []
        for key in self._candidate_order:
            url = self._candidate_urls[key]
            hints.append(
                CandidateHint(
                    url=url,
                    origin="ブラウザ通信",
                    headers=self._captured_headers(key, url),
                )
            )

        probe_headers = dict(headers or {})
        probe_headers.setdefault("Referer", self.main_frame_url)
        candidates = probe_candidates(
            hints,
            headers=probe_headers,
            timeout=self.timeout,
            workers=4,
        )
        return DiscoveryResult(
            candidates=candidates,
            page_url=self.main_frame_url,
        )

    def _stderr_tail(self, limit: int = 2000) -> str:
        if self._stderr_path is None or not self._stderr_path.is_file():
            return ""
        try:
            data = self._stderr_path.read_bytes()[-limit:]
        except OSError:
            return ""
        text = data.decode("utf-8", errors="replace").strip()
        return f"\n{text}" if text else ""

    def _terminate_process(self) -> None:
        process = self.process
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        self.process = None

    def close(self) -> None:
        if self.connection is not None:
            try:
                self.connection.close()
            except (OSError, CdpError, WebSocketError):
                pass
            self.connection = None
        elif self.ws is not None:
            try:
                self.ws.close()
            except (OSError, WebSocketError):
                pass
            self.ws = None

        self._terminate_process()

        if self._stderr_path is not None:
            self._stderr_path.unlink(missing_ok=True)
            self._stderr_path = None

        if self._owns_dir:
            shutil.rmtree(self.user_data_dir, ignore_errors=True)


def discover_browser(
    page_url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    page_wait: float = 5.0,
    browser_path: str | Path | None = None,
    headed: bool = False,
    user_data_dir: str | Path | None = None,
    click_selector: str | None = None,
    user_agent: str | None = None,
) -> DiscoveryResult:
    """ブラウザでページを開き、m3u8 の通信を検出する。"""
    executable = find_browser(browser_path)
    try:
        with BrowserSession(
            executable,
            headed=headed,
            user_data_dir=user_data_dir,
            user_agent=user_agent,
            timeout=timeout,
        ) as session:
            return session.discover(
                page_url,
                headers=headers,
                page_wait=page_wait,
                click_selector=click_selector,
            )
    except BrowserError:
        raise
    except (CdpError, WebSocketError, OSError) as exc:
        raise BrowserError(f"ブラウザ操作に失敗しました: {exc}") from exc
