"""設定ファイル (video-concat.ini) の読み込み。"""

from __future__ import annotations

import configparser
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .headers import validate_header_name, validate_header_value

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "video-concat.ini"

_KNOWN_KEYS: dict[str, set[str]] = {
    "http": {"referer", "user_agent"},
    "download": {"concurrency", "retries", "timeout"},
    "playlist": {"variant", "base_url", "token_query", "token_header"},
    "output": {"path", "work_dir", "save_playlist"},
    "behavior": {"keep_segments", "force"},
    "ffmpeg": {"path", "auto_download"},
}


class ConfigError(ValueError):
    """設定ファイルの内容が不正なときに送出する。"""


@dataclass(frozen=True)
class AppConfig:
    """設定ファイルから読み込んだ値。未指定は None。"""

    referer: str | None = None
    user_agent: str | None = None
    concurrency: int | None = None
    retries: int | None = None
    timeout: float | None = None
    variant: str | None = None
    base_url: str | None = None
    token_query: str | None = None
    token_header: str | None = None
    output: Path | None = None
    work_dir: Path | None = None
    save_playlist: Path | None = None
    keep_segments: bool | None = None
    force: bool | None = None
    ffmpeg_path: Path | None = None
    auto_download: bool | None = None


def _default_candidates() -> list[Path]:
    candidates: list[Path] = []

    # 配布版では exe と同じフォルダを最優先する。
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / CONFIG_FILENAME)

    candidates.append(Path.cwd() / CONFIG_FILENAME)

    # 重複を除く。
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(resolved)
    return unique


def find_config(explicit: str | Path | None = None) -> Path | None:
    """設定ファイルのパスを決定する。

    明示指定がなければ、exe と同じフォルダ、カレントディレクトリの順に探す。
    見つからない場合は None を返す。
    """
    if explicit is not None:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise ConfigError(f"設定ファイルが見つかりません: {path}")
        return path.resolve()

    for candidate in _default_candidates():
        if candidate.is_file():
            return candidate
    return None


def _get(parser: configparser.ConfigParser, section: str, key: str) -> str | None:
    if not parser.has_option(section, key):
        return None
    value = parser.get(section, key).strip()
    return value or None


def _get_int(
    parser: configparser.ConfigParser, section: str, key: str
) -> int | None:
    value = _get(parser, section, key)
    if value is None:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ConfigError(
            f"[{section}] {key} には整数を指定してください: {value!r}"
        ) from exc


def _get_float(
    parser: configparser.ConfigParser, section: str, key: str
) -> float | None:
    value = _get(parser, section, key)
    if value is None:
        return None
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(
            f"[{section}] {key} には数値を指定してください: {value!r}"
        ) from exc


def _get_bool(
    parser: configparser.ConfigParser, section: str, key: str
) -> bool | None:
    value = _get(parser, section, key)
    if value is None:
        return None
    try:
        return parser.getboolean(section, key)
    except ValueError as exc:
        raise ConfigError(
            f"[{section}] {key} には true / false を指定してください: {value!r}"
        ) from exc


def _resolve_path(value: str | None, base_dir: Path) -> Path | None:
    if value is None:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    return path.resolve()


def _validate_url(value: str, key: str, schemes: set[str]) -> str:
    validate_header_value(value)
    scheme = urlsplit(value).scheme.lower()
    if scheme not in schemes:
        allowed = " / ".join(f"{item}://" for item in sorted(schemes))
        raise ConfigError(f"{key} には {allowed} の URL を指定してください")
    return value


def load_config(path: Path | None) -> AppConfig:
    """設定ファイルを読み込む。None ならすべて既定値。"""
    if path is None:
        return AppConfig()

    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"設定ファイルが見つかりません: {path}")

    parser = configparser.ConfigParser(interpolation=None)
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"設定ファイルを読み込めません: {path}: {exc}") from exc
    except configparser.Error as exc:
        raise ConfigError(
            f"設定ファイルの形式が不正です: {path}: {exc}"
        ) from exc

    for section in parser.sections():
        if section not in _KNOWN_KEYS:
            logger.warning("設定ファイルの未知のセクションを無視します: [%s]", section)
            continue
        for key in parser.options(section):
            if key not in _KNOWN_KEYS[section]:
                logger.warning(
                    "設定ファイルの未知の項目を無視します: [%s] %s", section, key
                )

    base_dir = path.parent

    referer = _get(parser, "http", "referer")
    if referer is not None:
        referer = _validate_url(
            referer, "[http] referer", {"http", "https"}
        )

    user_agent = _get(parser, "http", "user_agent")
    if user_agent is not None:
        validate_header_value(user_agent)

    concurrency = _get_int(parser, "download", "concurrency")
    if concurrency is not None and concurrency < 1:
        raise ConfigError("[download] concurrency は 1 以上を指定してください")

    retries = _get_int(parser, "download", "retries")
    if retries is not None and retries < 1:
        raise ConfigError("[download] retries は 1 以上を指定してください")

    timeout = _get_float(parser, "download", "timeout")
    if timeout is not None and timeout <= 0:
        raise ConfigError("[download] timeout は 0 より大きい値を指定してください")

    variant = _get(parser, "playlist", "variant")
    if variant is not None and variant != "best":
        if not variant.isdigit():
            raise ConfigError(
                "[playlist] variant には best か 0 始まりの番号を指定してください"
            )
        if int(variant) < 0:
            raise ConfigError(
                "[playlist] variant には best か 0 始まりの番号を指定してください"
            )

    base_url = _get(parser, "playlist", "base_url")
    if base_url is not None:
        base_url = _validate_url(
            base_url, "[playlist] base_url", {"http", "https", "file"}
        )

    token_query = _get(parser, "playlist", "token_query")
    token_header = _get(parser, "playlist", "token_header")
    if token_header is not None:
        validate_header_name(token_header)

    output = _resolve_path(_get(parser, "output", "path"), base_dir)
    work_dir = _resolve_path(_get(parser, "output", "work_dir"), base_dir)
    save_playlist = _resolve_path(
        _get(parser, "output", "save_playlist"), base_dir
    )

    keep_segments = _get_bool(parser, "behavior", "keep_segments")
    force = _get_bool(parser, "behavior", "force")

    ffmpeg_path = _resolve_path(_get(parser, "ffmpeg", "path"), base_dir)
    auto_download = _get_bool(parser, "ffmpeg", "auto_download")

    return AppConfig(
        referer=referer,
        user_agent=user_agent,
        concurrency=concurrency,
        retries=retries,
        timeout=timeout,
        variant=variant,
        base_url=base_url,
        token_query=token_query,
        token_header=token_header,
        output=output,
        work_dir=work_dir,
        save_playlist=save_playlist,
        keep_segments=keep_segments,
        force=force,
        ffmpeg_path=ffmpeg_path,
        auto_download=auto_download,
    )
