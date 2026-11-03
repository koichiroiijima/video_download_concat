"""セグメントの並列ダウンロード。"""

from __future__ import annotations

import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

from .m3u8 import Segment, apply_token
from .net import DownloadError, download_file

logger = logging.getLogger(__name__)


@dataclass
class DownloadConfig:
    concurrency: int = 8
    retries: int = 3
    timeout: float = 30.0
    headers: dict[str, str] = field(default_factory=dict)
    token_query: str | None = None
    token_header: str | None = None
    force: bool = False


def segment_filename(index: int, total: int) -> str:
    """list.cmd のゼロ埋めリネームに相当するファイル名を作る。"""
    width = max(4, len(str(max(total - 1, 0))))
    return f"{index:0{width}d}.ts"


class _Progress:
    """stderr に進捗を表示する。TTY なら 1 行を上書きする。"""

    def __init__(self, total: int, done: int = 0) -> None:
        self.total = total
        self.done = done
        self._tty = sys.stderr.isatty()
        self._step = max(1, total // 10)
        self._lock = threading.Lock()

    def advance(self) -> None:
        with self._lock:
            self.done += 1
            done = self.done

        percent = done * 100 // self.total
        message = f"ダウンロード {done}/{self.total} ({percent}%)"
        if self._tty:
            print(f"\r{message}", end="", file=sys.stderr, flush=True)
        elif done == self.total or done % self._step == 0:
            print(message, file=sys.stderr, flush=True)

    def finish(self) -> None:
        if self._tty:
            print(file=sys.stderr)


def download_segments(
    segments: list[Segment],
    work_dir: Path,
    config: DownloadConfig,
    *,
    show_progress: bool = True,
) -> list[Path]:
    """全セグメントを work_dir にダウンロードし、プレイリスト順のパスを返す。"""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)

    paths = [
        work_dir / segment_filename(segment.index, len(segments))
        for segment in segments
    ]

    pending: list[tuple[Segment, Path]] = []
    for segment, path in zip(segments, paths):
        if not config.force and path.is_file() and path.stat().st_size > 0:
            continue
        pending.append((segment, path))

    reused = len(segments) - len(pending)
    if reused and show_progress:
        print(f"既存の {reused} セグメントを再利用します", file=sys.stderr)

    if not pending:
        return paths

    if show_progress:
        print(
            f"セグメントをダウンロードします"
            f"（{len(pending)} 件、同時 {config.concurrency}）",
            file=sys.stderr,
        )

    progress = _Progress(len(segments), reused) if show_progress else None
    failures: list[tuple[Segment, Exception]] = []
    cancelled = False

    with ThreadPoolExecutor(max_workers=max(1, config.concurrency)) as executor:
        futures = {
            executor.submit(_download_one, segment, path, config): segment
            for segment, path in pending
        }

        for future in as_completed(futures):
            segment = futures[future]
            succeeded = False
            if not future.cancelled():
                try:
                    future.result()
                    succeeded = True
                    logger.debug("セグメント %d を取得しました", segment.index)
                except Exception as exc:  # noqa: BLE001 - 集約して最後に報告する
                    failures.append((segment, exc))
                    if not cancelled:
                        cancelled = True
                        # 失敗が判明したら残りの待機タスクはキャンセルする。
                        for other in futures:
                            other.cancel()

            if progress is not None and succeeded:
                progress.advance()

    if progress is not None:
        progress.finish()

    if failures:
        lines = [
            f"  - セグメント {segment.index}: {exc}"
            for segment, exc in failures[:5]
        ]
        if len(failures) > 5:
            lines.append(f"  ... ほか {len(failures) - 5} 件")
        raise DownloadError(
            "セグメントのダウンロードに失敗しました:\n" + "\n".join(lines)
        )

    return paths


def _download_one(segment: Segment, path: Path, config: DownloadConfig) -> int:
    headers = dict(config.headers)
    url = segment.url

    if segment.token:
        if config.token_header:
            headers[config.token_header] = segment.token
        if config.token_query:
            url = apply_token(url, segment.token, config.token_query)

    return download_file(
        url,
        path,
        headers=headers,
        timeout=config.timeout,
        retries=config.retries,
    )
