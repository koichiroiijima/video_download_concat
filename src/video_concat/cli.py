"""video-concat の CLI。"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import url2pathname

from . import __version__
from .browser_cdp import BrowserError, discover_browser
from .config import AppConfig, find_config, load_config
from .downloader import DownloadConfig, download_segments
from .ffmpeg import FFmpegError, concat_segments
from .ffmpeg_bootstrap import resolve_ffmpeg
from .headers import canonical_header, parse_header_items
from .m3u8 import Playlist, PlaylistError, parse_playlist, select_variant
from .net import DEFAULT_USER_AGENT, DownloadError, fetch_text_with_url
from .page_source import (
    DiscoveryError,
    DiscoveryResult,
    discover_static,
    format_candidate,
    select_candidate,
)

logger = logging.getLogger(__name__)


def _configure_stdio() -> None:
    """Windows のコンソールでも日本語を文字化けさせない。"""
    for stream in (sys.stdout, sys.stderr):
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def _is_http_url(value: str) -> bool:
    return urlsplit(value).scheme.lower() in {"http", "https"}


def _local_path(source: str) -> Path | None:
    """ローカルパスまたは file:// URI なら Path を返す。"""
    parts = urlsplit(source)
    if parts.scheme == "file":
        path = url2pathname(parts.path)
        if parts.netloc and parts.netloc != "localhost":
            path = f"//{parts.netloc}{path}"
        return Path(path)
    if parts.scheme == "":
        return Path(source).expanduser()
    return None


def _load_playlist(
    source: str,
    *,
    headers: dict[str, str],
    timeout: float,
    retries: int,
    variant_choice: str,
    save_path: Path | None,
    base_url: str | None = None,
    depth: int = 0,
) -> tuple[Playlist, Path]:
    if depth > 3:
        raise PlaylistError("マスタープレイリストのネストが深すぎます")

    local_path: Path | None = None
    if _is_http_url(source):
        text, base = fetch_text_with_url(
            source,
            headers=headers,
            timeout=timeout,
            retries=retries,
        )
        if base != source:
            logger.debug("リダイレクト先を基準にします: %s", base)
    else:
        local_path = _local_path(source)
        if local_path is None or not local_path.is_file():
            raise PlaylistError(f"m3u8 ファイルが見つかりません: {source}")
        text = local_path.read_text(encoding="utf-8-sig")
        base = local_path.resolve().as_uri()

    if base_url is not None:
        base = base_url

    playlist = parse_playlist(text, base)

    if playlist.is_master:
        print("マスタープレイリストを検出しました:", file=sys.stderr)
        for variant in playlist.variants:
            print(f"  {variant.describe()}", file=sys.stderr)

        selected = select_variant(playlist, variant_choice)
        print(f"Variant を選択しました: {selected.describe()}", file=sys.stderr)
        return _load_playlist(
            selected.url,
            headers=headers,
            timeout=timeout,
            retries=retries,
            variant_choice=variant_choice,
            save_path=save_path,
            # 子プレイリストは、選択した Variant の URL を基準に解決する。
            base_url=None,
            depth=depth + 1,
        )

    available_path = local_path
    if save_path is not None:
        resolved_save = save_path.resolve()
        if local_path is None or resolved_save != local_path.resolve():
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(text, encoding="utf-8")
            print(f"m3u8 を保存しました: {save_path}", file=sys.stderr)
            available_path = save_path

    if available_path is None:
        raise PlaylistError("プレイリストの保存先を決定できませんでした")

    return playlist, available_path


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _cleanup_work_dir(
    work_dir: Path,
    segment_paths: list[Path],
    list_path: Path,
    playlist_path: Path,
    protected_paths: set[Path],
) -> None:
    """自分が作ったファイルだけを削除する。既存の無関係なファイルは残す。"""
    for path in segment_paths:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".part").unlink(missing_ok=True)

    list_path.unlink(missing_ok=True)
    if playlist_path.resolve() not in protected_paths:
        playlist_path.unlink(missing_ok=True)

    try:
        work_dir.rmdir()
    except OSError:
        # 他のファイルが残っている場合は削除しない。
        pass


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video-concat",
        description=(
            "m3u8 を取得し、記載されたセグメントを並列ダウンロードして、"
            "ffmpeg で 1 本の MP4 に結合します。"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "例:\n"
            "  video-concat https://example.com/video.m3u8\n"
            '  video-concat https://example.com/video.m3u8 -o output.mp4 -c 16\n'
            '  video-concat video.m3u8 -H "Referer: https://example.com/"\n'
            "  video-concat URL --token-query token\n"
            "  video-concat --from-page https://example.com/watch/123 -o output.mp4\n"
            "  video-concat --from-page https://example.com/watch/123 --list-candidates\n"
            "  video-concat URL --config C:\\path\\video-concat.ini\n"
        ),
    )
    parser.add_argument(
        "source",
        nargs="?",
        help="m3u8 の URL、またはローカルの .m3u8 ファイル",
    )
    parser.add_argument(
        "--from-page",
        metavar="URL",
        help=(
            "Webページの URL から m3u8 を探す。"
            "静的解析で見つからない場合はブラウザで JS を実行する"
        ),
    )
    parser.add_argument(
        "--page-mode",
        metavar="MODE",
        help="m3u8 の探索方法: auto / static / browser（既定: auto）",
    )
    parser.add_argument(
        "--page-wait",
        type=float,
        metavar="SECONDS",
        help="ブラウザでページを開いてから待つ秒数（既定: 5）",
    )
    parser.add_argument(
        "--list-candidates",
        action="store_true",
        help="見つかった m3u8 の候補を一覧表示して終了する",
    )
    parser.add_argument(
        "--source-index",
        type=int,
        metavar="N",
        help="使用する候補の番号（0 始まり）",
    )
    parser.add_argument(
        "--browser-path",
        metavar="PATH",
        help="Chrome / Edge / Chromium の実行パス（既定: 自動探索）",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="ブラウザを画面表示ありで起動する（デバッグ用）",
    )
    parser.add_argument(
        "--user-data-dir",
        metavar="PATH",
        help="ブラウザのユーザーデータディレクトリ（ログイン済みプロファイルを使う場合）",
    )
    parser.add_argument(
        "--click-selector",
        metavar="CSS",
        help="再生ボタンなどをクリックする CSS セレクタ",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help="設定ファイル（既定: exe と同じフォルダ、またはカレントディレクトリの video-concat.ini）",
    )
    parser.add_argument(
        "-o",
        "--output",
        help="出力する MP4 ファイル（既定: YYYYmmddHHMMSS.mp4）",
    )
    parser.add_argument(
        "--work-dir",
        help="セグメントの保存先（既定: <出力名>_segments）",
    )
    parser.add_argument(
        "--base-url",
        metavar="URL",
        help=(
            "相対 URI を解決する基準 URL を上書きする"
            "（ローカル m3u8 を元の配信元から取得する場合など）"
        ),
    )
    parser.add_argument(
        "-H",
        "--header",
        action="append",
        default=[],
        metavar="NAME: VALUE",
        help="追加 HTTP ヘッダ（複数指定可）",
    )
    parser.add_argument(
        "--user-agent",
        help="User-Agent（既定: ブラウザ相当）",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        metavar="N",
        help="同時ダウンロード数（既定: 8）",
    )
    parser.add_argument(
        "--retries",
        type=int,
        metavar="N",
        help="1 セグメントあたりの試行回数（既定: 3）",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        metavar="SECONDS",
        help="HTTP タイムアウト秒（既定: 30）",
    )
    parser.add_argument(
        "--token-query",
        metavar="NAME",
        help="非標準の EXT-X-TOKEN をクエリパラメータ NAME として各セグメント URL に付与",
    )
    parser.add_argument(
        "--token-header",
        metavar="NAME",
        help="非標準の EXT-X-TOKEN を HTTP ヘッダ NAME として各セグメントに送信",
    )
    parser.add_argument(
        "--variant",
        metavar="INDEX|best",
        help="マスタープレイリストの Variant（0 始まり、既定: best）",
    )
    parser.add_argument(
        "--ffmpeg",
        metavar="PATH",
        help="ffmpeg の実行パス（既定: 同梱の ffmpeg、または PATH から探索）",
    )
    parser.add_argument(
        "--auto-download",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="ffmpeg が見つからない場合に自動ダウンロードする（既定: 有効）",
    )
    parser.add_argument(
        "--keep-segments",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="結合後にセグメントとプレイリストを削除しない",
    )
    parser.add_argument(
        "--force",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="既存の出力とセグメントを上書きする",
    )
    parser.add_argument(
        "--save-playlist",
        metavar="PATH",
        help="取得したメディアプレイリストのコピー先",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="デバッグログを表示する",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="進捗と警告を抑制する",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    return parser


def _validate_args(args: argparse.Namespace) -> None:
    if args.source is not None and args.from_page is not None:
        raise ValueError("SOURCE と --from-page は同時に指定できません")
    if args.list_candidates and args.from_page is None:
        raise ValueError("--list-candidates は --from-page と一緒に指定してください")
    if args.source_index is not None:
        if args.from_page is None:
            raise ValueError("--source-index は --from-page と一緒に指定してください")
        if args.source_index < 0:
            raise ValueError("--source-index は 0 以上を指定してください")
    if args.source is None and args.from_page is None:
        raise ValueError(
            "m3u8 の URL（SOURCE）か --from-page のどちらかを指定してください"
        )
    if args.from_page is not None:
        if not _is_http_url(args.from_page):
            raise ValueError(
                "--from-page には http:// / https:// の URL を指定してください"
            )
        if args.base_url is not None:
            raise ValueError(
                "--from-page と --base-url は同時に指定できません"
            )
    if args.page_mode is not None and args.page_mode not in {
        "auto",
        "static",
        "browser",
    }:
        raise ValueError(
            "--page-mode には auto / static / browser のいずれかを指定してください"
        )
    if args.page_wait is not None and args.page_wait < 0:
        raise ValueError("--page-wait は 0 以上を指定してください")
    if args.page_mode == "static":
        for option, value in (
            ("--page-wait", args.page_wait),
            ("--browser-path", args.browser_path),
            ("--headed", args.headed),
            ("--user-data-dir", args.user_data_dir),
            ("--click-selector", args.click_selector),
        ):
            if value:
                raise ValueError(
                    f"{option} は --page-mode static と同時に指定できません"
                )
    if args.click_selector is not None and not args.click_selector:
        raise ValueError("--click-selector には空でない値を指定してください")
    if args.browser_path is not None and not args.browser_path:
        raise ValueError("--browser-path には空でないパスを指定してください")
    if args.user_data_dir is not None and not args.user_data_dir:
        raise ValueError("--user-data-dir には空でないパスを指定してください")
    if args.concurrency is not None and args.concurrency < 1:
        raise ValueError("--concurrency は 1 以上を指定してください")
    if args.retries is not None and args.retries < 1:
        raise ValueError("--retries は 1 以上を指定してください")
    if args.timeout is not None and args.timeout <= 0:
        raise ValueError("--timeout は 0 より大きい値を指定してください")
    if args.token_query is not None and not args.token_query:
        raise ValueError("--token-query には空でない名前を指定してください")
    if args.token_header is not None and not args.token_header:
        raise ValueError("--token-header には空でない名前を指定してください")
    if args.user_agent is not None and not args.user_agent:
        raise ValueError("--user-agent には空でない値を指定してください")
    if args.ffmpeg is not None and not args.ffmpeg:
        raise ValueError("--ffmpeg には空でないパスを指定してください")
    if args.variant is not None and args.variant != "best":
        if not args.variant.isdigit() or int(args.variant) < 0:
            raise ValueError(
                "--variant には best か 0 始まりの番号を指定してください"
            )
    if args.base_url is not None:
        scheme = urlsplit(args.base_url).scheme.lower()
        if scheme not in {"http", "https", "file"}:
            raise ValueError(
                "--base-url には http:// / https:// / file:// の URL を指定してください"
            )


def _pick(cli_value, config_value, default):
    if cli_value is not None:
        return cli_value
    if config_value is not None:
        return config_value
    return default


def _to_path(value: str | Path | None) -> Path | None:
    if value is None:
        return None
    return Path(value).expanduser()


def _merge_candidate_headers(
    headers: dict[str, str],
    candidate_headers: dict[str, str],
    *,
    allow_user_agent: bool,
) -> None:
    """ブラウザが実際に送ったヘッダを、明示指定がない項目だけ補う。

    ``allow_user_agent`` が真なら、既定の User-Agent をブラウザの値で置き換える。
    """
    for name, value in candidate_headers.items():
        canonical = canonical_header(name)
        if canonical == "User-Agent":
            if allow_user_agent:
                headers["User-Agent"] = value
            continue
        headers.setdefault(canonical, value)


def _discover_page(
    args: argparse.Namespace,
    headers: dict[str, str],
    *,
    timeout: float,
    retries: int,
) -> DiscoveryResult:
    mode = args.page_mode or "auto"
    page_wait = args.page_wait if args.page_wait is not None else 5.0

    if mode == "static":
        return discover_static(
            args.from_page,
            headers=headers,
            timeout=timeout,
            retries=retries,
        )

    browser_kwargs = {
        "headers": headers,
        "timeout": timeout,
        "page_wait": page_wait,
        "browser_path": args.browser_path,
        "headed": args.headed,
        "user_data_dir": args.user_data_dir,
        "click_selector": args.click_selector,
        "user_agent": args.user_agent,
    }

    if mode == "browser":
        try:
            return discover_browser(args.from_page, **browser_kwargs)
        except BrowserError as exc:
            raise DiscoveryError(
                f"ブラウザ方式で m3u8 を探索できませんでした: {exc}"
            ) from exc

    static_result: DiscoveryResult | None = None
    static_error: Exception | None = None
    try:
        static_result = discover_static(
            args.from_page,
            headers=headers,
            timeout=timeout,
            retries=retries,
        )
        if any(candidate.ok for candidate in static_result.candidates):
            return static_result
    except (DownloadError, OSError) as exc:
        static_error = exc

    try:
        browser_result = discover_browser(args.from_page, **browser_kwargs)
    except BrowserError as exc:
        if static_result is not None:
            return static_result
        detail = f"（静的解析: {static_error}）" if static_error else ""
        raise DiscoveryError(
            "Webページから m3u8 を検出できませんでした。"
            f"ブラウザ方式も利用できません: {exc}{detail}"
        ) from exc

    if browser_result.candidates:
        return browser_result
    return static_result or browser_result


def _print_candidates(discovery: DiscoveryResult) -> None:
    print(f"m3u8 の候補: {len(discovery.candidates)} 件")
    for index, candidate in enumerate(discovery.candidates):
        print(format_candidate(index, candidate))


def run(args: argparse.Namespace, app_config: AppConfig) -> None:
    user_agent = _pick(
        args.user_agent, app_config.user_agent, DEFAULT_USER_AGENT
    )
    concurrency = _pick(args.concurrency, app_config.concurrency, 8)
    retries = _pick(args.retries, app_config.retries, 3)
    timeout = _pick(args.timeout, app_config.timeout, 30.0)
    variant = _pick(args.variant, app_config.variant, "best")
    base_url = _pick(args.base_url, app_config.base_url, None)
    token_query = _pick(args.token_query, app_config.token_query, None)
    token_header = _pick(args.token_header, app_config.token_header, None)
    keep_segments = _pick(args.keep_segments, app_config.keep_segments, False)
    force = _pick(args.force, app_config.force, False)
    auto_download = _pick(
        args.auto_download, app_config.auto_download, True
    )

    headers = parse_header_items(args.header)
    if app_config.referer is not None and "Referer" not in headers:
        headers["Referer"] = app_config.referer
    if "User-Agent" not in headers:
        headers["User-Agent"] = user_agent

    source = args.source
    discovery: DiscoveryResult | None = None
    if args.from_page is not None:
        try:
            discovery = _discover_page(
                args, headers, timeout=timeout, retries=retries
            )
        except DownloadError as exc:
            raise DiscoveryError(
                f"Webページを取得できませんでした: {exc}"
            ) from exc

        if args.list_candidates:
            _print_candidates(discovery)
            if not discovery.candidates:
                raise DiscoveryError(
                    "Webページから m3u8 の候補を検出できませんでした。"
                )
            return

        selected = select_candidate(
            discovery.candidates,
            index=args.source_index,
        )
        source = selected.url
        explicit_user_agent = (
            args.user_agent is not None or app_config.user_agent is not None
        )
        _merge_candidate_headers(
            headers,
            selected.headers,
            allow_user_agent=not explicit_user_agent,
        )
        headers.setdefault("Referer", discovery.page_url)
        if not args.quiet:
            print(f"m3u8 を検出しました: {selected.url}", file=sys.stderr)

    if source is None:
        raise DiscoveryError("m3u8 の取得元を決定できませんでした")

    output = _to_path(_pick(args.output, app_config.output, None))
    if output is None:
        output = Path(datetime.now().strftime("%Y%m%d%H%M%S") + ".mp4")
    if not output.suffix:
        output = output.with_suffix(".mp4")

    if output.exists() and not force:
        raise PlaylistError(
            f"出力ファイルが既に存在します: {output}（上書きするには --force）"
        )

    work_dir = _to_path(_pick(args.work_dir, app_config.work_dir, None))
    if work_dir is None:
        work_dir = output.with_name(output.stem + "_segments")
    work_dir.mkdir(parents=True, exist_ok=True)

    save_playlist = _to_path(
        _pick(args.save_playlist, app_config.save_playlist, None)
    )
    playlist_save_path = work_dir / "playlist.m3u8"

    playlist, available_playlist = _load_playlist(
        source,
        headers=headers,
        timeout=timeout,
        retries=retries,
        variant_choice=variant,
        save_path=playlist_save_path,
        base_url=base_url,
    )

    if playlist.encryption:
        raise PlaylistError(
            f"暗号化された HLS（{playlist.encryption}）には未対応です"
        )
    if playlist.init_uri:
        raise PlaylistError(
            "fMP4（EXT-X-MAP）のプレイリストには未対応です。"
            "MPEG-TS のプレイリストを指定してください"
        )
    if playlist.has_byterange:
        raise PlaylistError(
            "EXT-X-BYTERANGE を使うプレイリストには未対応です"
        )
    if not playlist.has_endlist and not args.quiet:
        print(
            "警告: EXT-X-ENDLIST がないため、ライブプレイリストの現在の内容だけを取得します",
            file=sys.stderr,
        )

    if (
        playlist.tokens
        and not (token_query or token_header)
        and not args.quiet
    ):
        print(
            "警告: EXT-X-TOKEN が含まれていますが、"
            "--token-query / --token-header が未指定のため無視します",
            file=sys.stderr,
        )

    if save_playlist is not None:
        save_playlist.parent.mkdir(parents=True, exist_ok=True)
        if save_playlist.resolve() != available_playlist.resolve():
            shutil.copyfile(available_playlist, save_playlist)
            print(f"m3u8 をコピーしました: {save_playlist}", file=sys.stderr)

    print(f"セグメント数: {len(playlist.segments)}", file=sys.stderr)
    if playlist.total_duration:
        print(
            f"合計時間（EXTINF ベース）: {_format_duration(playlist.total_duration)}",
            file=sys.stderr,
        )

    ffmpeg = resolve_ffmpeg(
        explicit=args.ffmpeg or app_config.ffmpeg_path,
        auto_download=auto_download,
        timeout=timeout,
        retries=retries,
        quiet=args.quiet,
    )

    config = DownloadConfig(
        concurrency=concurrency,
        retries=retries,
        timeout=timeout,
        headers=headers,
        token_query=token_query,
        token_header=(
            canonical_header(token_header) if token_header else None
        ),
        force=force,
    )

    segment_paths = download_segments(
        playlist.segments,
        work_dir,
        config,
        show_progress=not args.quiet,
    )

    list_path = work_dir / "list.txt"
    concat_segments(segment_paths, output, ffmpeg, list_path)

    if keep_segments:
        print(f"セグメントを保持しました: {work_dir}", file=sys.stderr)
    else:
        protected_paths: set[Path] = set()
        local_source = _local_path(source)
        if local_source is not None:
            protected_paths.add(local_source.resolve())
        if save_playlist is not None:
            protected_paths.add(save_playlist.resolve())
        _cleanup_work_dir(
            work_dir,
            segment_paths,
            list_path,
            available_playlist,
            protected_paths,
        )

    size = output.stat().st_size / (1024 * 1024)
    print(f"完成: {output} ({size:.1f} MB)")


def main(argv: list[str] | None = None) -> int:
    _configure_stdio()
    parser = _build_parser()
    raw_args = list(sys.argv[1:] if argv is None else argv)

    if not raw_args:
        parser.print_help()
        return 0

    args = parser.parse_args(raw_args)

    if args.verbose:
        level = logging.DEBUG
    elif args.quiet:
        level = logging.ERROR
    else:
        level = logging.WARNING
    logging.basicConfig(
        level=level, format="%(levelname)s: %(message)s", force=True
    )

    try:
        _validate_args(args)
        config_path = find_config(args.config)
        app_config = load_config(config_path)
        if config_path is not None:
            logger.debug("設定ファイルを使用します: %s", config_path)
        run(args, app_config)
    except KeyboardInterrupt:
        print(
            "\n中断しました。途中までのセグメントは作業ディレクトリに残っています。",
            file=sys.stderr,
        )
        return 130
    except (PlaylistError, DownloadError, FFmpegError, ValueError, OSError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
