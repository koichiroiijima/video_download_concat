"""HTTP 取得、リトライ、ストリーミング保存。"""

from __future__ import annotations

import http.client
import logging
import random
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int | None], None]

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
CHUNK_SIZE = 256 * 1024


class DownloadError(RuntimeError):
    """ダウンロードに失敗したときに送出する。"""


def _backoff(attempt: int) -> float:
    return min(2 ** (attempt - 1), 30) + random.uniform(0, 0.5)


def fetch_text(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    max_bytes: int | None = None,
) -> str:
    """URL からテキストを取得する。一時的な失敗はリトライする。"""
    text, _ = fetch_text_with_url(
        url,
        headers=headers,
        timeout=timeout,
        retries=retries,
        max_bytes=max_bytes,
    )
    return text


def fetch_text_with_url(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    max_bytes: int | None = None,
) -> tuple[str, str]:
    """URL からテキストと、リダイレクト後の最終 URL を取得する。

    プレイリスト内の相対 URI は、リダイレクト後の URL を基準に解決する必要がある。
    """
    request_headers = dict(headers or {})
    request_headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    request_headers.setdefault("Accept-Encoding", "identity")

    last_error: DownloadError | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                final_url = response.geturl()
                charset = response.headers.get_content_charset() or "utf-8"
                if max_bytes is None:
                    data = response.read()
                else:
                    data = response.read(max_bytes + 1)
                    if len(data) > max_bytes:
                        raise DownloadError(
                            f"応答が大きすぎます（上限 {max_bytes} バイト）: {url}"
                        )
                text = data.decode(charset, errors="replace")
                return text, final_url
        except urllib.error.HTTPError as exc:
            last_error = DownloadError(f"{url}: HTTP {exc.code} {exc.reason}")
            if exc.code not in RETRYABLE_STATUS or attempt >= retries:
                raise last_error from exc
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            last_error = DownloadError(f"{url}: {exc}")
            if attempt >= retries:
                raise last_error from exc

        delay = _backoff(attempt)
        logger.debug(
            "取得に失敗しました（%d/%d 回目）。%.1f 秒後に再試行します: %s",
            attempt,
            retries,
            delay,
            last_error,
        )
        time.sleep(delay)

    raise last_error or DownloadError(url)


def download_file(
    url: str,
    destination: Path,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    progress: ProgressCallback | None = None,
) -> int:
    """URL をファイルに保存する。成功したら書き込んだバイト数を返す。

    途中まで書いたデータは ``.part`` に置き、完了後に atomic にリネームする。
    ``progress`` を渡すと、書き込み量と Content-Length（不明なら None）を通知する。
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    part = destination.with_name(destination.name + ".part")

    request_headers = dict(headers or {})
    request_headers.setdefault("User-Agent", DEFAULT_USER_AGENT)
    request_headers.setdefault("Accept", "*/*")
    request_headers.setdefault("Accept-Encoding", "identity")

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            request = urllib.request.Request(url, headers=request_headers)
            with urllib.request.urlopen(request, timeout=timeout) as response:
                content_length = response.headers.get("Content-Length")
                expected = (
                    int(content_length)
                    if content_length and content_length.isdigit()
                    else None
                )

                written = 0
                if progress is not None:
                    progress(0, expected)
                with part.open("wb") as handle:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        handle.write(chunk)
                        written += len(chunk)
                        if progress is not None:
                            progress(written, expected)

            if expected is not None and written != expected:
                raise DownloadError(
                    f"サイズが一致しません（期待 {expected} バイト、実際 {written} バイト）: {url}"
                )

            part.replace(destination)
            return written
        except urllib.error.HTTPError as exc:
            part.unlink(missing_ok=True)
            last_error = DownloadError(f"{url}: HTTP {exc.code} {exc.reason}")
            if exc.code not in RETRYABLE_STATUS or attempt >= retries:
                raise last_error from exc
        except DownloadError as exc:
            part.unlink(missing_ok=True)
            last_error = exc
            if attempt >= retries:
                raise
        except (urllib.error.URLError, OSError, http.client.HTTPException) as exc:
            part.unlink(missing_ok=True)
            last_error = DownloadError(f"{url}: {exc}")
            if attempt >= retries:
                raise last_error from exc

        delay = _backoff(attempt)
        logger.debug(
            "ダウンロードに失敗しました（%d/%d 回目）。%.1f 秒後に再試行します: %s",
            attempt,
            retries,
            delay,
            last_error,
        )
        time.sleep(delay)

    raise DownloadError(f"{url}: {last_error or '不明なエラー'}")
