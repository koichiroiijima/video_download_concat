"""Webページから m3u8 の候補を探す。

HTML・インライン JavaScript・同一オリジンの外部スクリプト・iframe を
静的に走査する。JavaScript は実行しないため、実行時に URL を生成する
ページでは ``browser_cdp`` のブラウザ方式を使う。
"""

from __future__ import annotations

import html
import logging
import re
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

from .m3u8 import Playlist, PlaylistError, parse_playlist
from .net import DownloadError, fetch_text_with_url

logger = logging.getLogger(__name__)

FetchText = Callable[..., tuple[str, str]]

MAX_DOCUMENTS = 5
MAX_SCRIPTS = 10
MAX_CANDIDATES = 50
MAX_RESOURCE_BYTES = 2 * 1024 * 1024
MAX_IFRAME_DEPTH = 2
PROBE_WORKERS = 8
PROBE_TIMEOUT = 10.0
HLS_PREFIX = "#EXTM3U"


class DiscoveryError(ValueError):
    """m3u8 の探索・選択に失敗したときに送出する。"""


@dataclass(frozen=True)
class CandidateHint:
    """探索で見つかった m3u8 URL の候補。"""

    url: str
    origin: str
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class SourceCandidate:
    """候補を取得してプレイリストとして検証した結果。"""

    url: str
    origin: str
    playlist: Playlist | None = None
    error: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.playlist is not None


@dataclass
class DiscoveryResult:
    """探索結果。``page_url`` はリダイレクト後の最終 URL。"""

    candidates: list[SourceCandidate]
    page_url: str


_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")
_HEX_ESCAPE_RE = re.compile(r"\\x([0-9a-fA-F]{2})")

# ``eval(function(p,a,c,k,e,d){...}('...',a,c,'...'.split('|'),0,{}))``
# 形式（Dean Edwards の packer）の引数を取り出す。
# 関数本体の ``new RegExp('\\b'+...)`` などを取り違えないよう、
# 引数リストの開始括弧 ``(`` の直後にある文字列だけを対象にする。
_JS_STRING = r"'(?:[^'\\]|\\.)*'|\"(?:[^\"\\]|\\.)*\""
_PACKER_CALL_RE = re.compile(
    r"\(\s*(?P<p>" + _JS_STRING + r")\s*,\s*"
    r"(?P<a>\d+)\s*,\s*(?P<c>\d+)\s*,\s*"
    r"(?P<k>" + _JS_STRING + r")\s*\.\s*split\s*\(\s*"
    r"['\"]\|['\"]\s*\)\s*,\s*\d+\s*,\s*\{\s*\}\s*\)",
    re.DOTALL,
)
_BASE_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_JS_ESCAPE_RE = re.compile(
    r"\\(u\{[0-9a-fA-F]+\}|u[0-9a-fA-F]{4}|x[0-9a-fA-F]{2}|[0-7]{1,3}|.)",
    re.DOTALL,
)
MAX_PACKER_DEPTH = 3
MAX_PACKER_CALLS = 20

# 絶対 URL / プロトコル相対 URL
_ABSOLUTE_RE = re.compile(
    r"(?:https?:)?//[^\s\"'<>\\]+?\.m3u8(?:[?#][^\s\"'<>\\]*)?",
    re.IGNORECASE,
)
# 引用符内の相対 URL（"video.m3u8"、"./video.m3u8" など）
_QUOTED_RE = re.compile(
    r"[\"']([^\"'\s<>]+?\.m3u8(?:[?#][^\"'\s<>]*)?)[\"']",
    re.IGNORECASE,
)
# 引用符のないルート相対 URL
_ROOT_RE = re.compile(
    r"(?<![\w.:/])/(?:[^\s\"'<>\\]+?\.m3u8(?:[?#][^\s\"'<>\\]*)?)",
    re.IGNORECASE,
)


def _decode_escapes(text: str) -> str:
    """JSON / JavaScript のエスケープと HTML エンティティを戻す。"""
    text = text.replace("\\/", "/")
    text = _UNICODE_ESCAPE_RE.sub(
        lambda match: chr(int(match.group(1), 16)), text
    )
    text = _HEX_ESCAPE_RE.sub(
        lambda match: chr(int(match.group(1), 16)), text
    )
    return html.unescape(text)


def _unescape_js(text: str) -> str:
    """JavaScript の文字列リテラル内のエスケープを戻す。"""

    def replace(match: re.Match[str]) -> str:
        escape = match.group(1)
        if escape.startswith("u{"):
            digits = escape[2:-1]
            try:
                return chr(int(digits, 16))
            except (ValueError, OverflowError):
                return match.group(0)
        if escape[0] == "u" and len(escape) == 5:
            return chr(int(escape[1:], 16))
        if escape[0] == "x" and len(escape) == 3:
            return chr(int(escape[1:], 16))
        if escape[0] in "01234567" and len(escape) <= 3:
            return chr(int(escape, 8))
        return {
            "n": "\n",
            "r": "\r",
            "t": "\t",
            "b": "\b",
            "f": "\f",
            "v": "\v",
            "0": "\0",
        }.get(escape, escape)

    return _JS_ESCAPE_RE.sub(replace, text)


def _to_base(value: int, base: int) -> str:
    """packer と同じ基数表現に変換する（0-9a-zA-Z）。"""
    if value == 0:
        return "0"
    digits: list[str] = []
    while value:
        value, remainder = divmod(value, base)
        digits.append(_BASE_ALPHABET[remainder])
    return "".join(reversed(digits))


def _unpack(packed: str, base: int, count: int, keys: list[str]) -> str:
    """packer の文字列を、キーワード表を使って展開する。"""
    if base < 2 or base > len(_BASE_ALPHABET) or count <= 0:
        return packed
    table: dict[str, str] = {}
    for index in range(count):
        if index >= len(keys):
            break
        keyword = keys[index]
        if keyword:
            table[_to_base(index, base)] = keyword
    if not table:
        return packed
    return re.sub(
        r"\b\w+\b", lambda match: table.get(match.group(0), match.group(0)), packed
    )


def _decode_packer_calls(text: str) -> list[str]:
    """Dean Edwards 形式の packer 呼び出しを展開した結果を返す。"""
    decoded: list[str] = []
    for match in _PACKER_CALL_RE.finditer(text):
        if len(decoded) >= MAX_PACKER_CALLS:
            break
        try:
            packed = _unescape_js(match.group("p")[1:-1])
            keys = _unescape_js(match.group("k")[1:-1]).split("|")
            base = int(match.group("a"))
            count = int(match.group("c"))
        except (ValueError, OverflowError):
            continue
        decoded.append(_unpack(packed, base, count, keys))
    return decoded


def decode_obfuscated_javascript(text: str) -> list[str]:
    """難読化された JavaScript を展開し、解読結果の一覧を返す。

    Dean Edwards の packer（``eval(function(p,a,c,k,e,d){...}(...))``）を
    対象にする。多重に難読化されている場合は数段まで追う。
    """
    results: list[str] = []
    current = _decode_packer_calls(text)
    for _ in range(MAX_PACKER_DEPTH):
        if not current:
            break
        results.extend(current)
        current = _decode_packer_calls("\n".join(current))
    return results


def _append_url(
    found: list[str], seen: set[str], raw: str, base_url: str
) -> None:
    raw = raw.strip().rstrip("),.;]}>")
    if not raw:
        return
    url = urljoin(base_url, raw)
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        return
    if ".m3u8" not in url.lower():
        return
    normalized = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, parts.query, "")
    )
    if normalized not in seen:
        seen.add(normalized)
        found.append(normalized)


def extract_m3u8_urls(text: str, base_url: str) -> list[str]:
    """テキストから m3u8 URL を抽出し、出現順の重複なしリストを返す。

    難読化された JavaScript（Dean Edwards の packer）が含まれる場合は
    展開してから走査する。エスケープの解釈で文字列リテラルが壊れないよう、
    展開は生のテキストに対して先に行う。
    """
    found: list[str] = []
    seen: set[str] = set()

    sources = [text, *decode_obfuscated_javascript(text)]
    for source in sources:
        normalized = _decode_escapes(source)
        for match in _ABSOLUTE_RE.finditer(normalized):
            _append_url(found, seen, match.group(0), base_url)
        for match in _QUOTED_RE.finditer(normalized):
            _append_url(found, seen, match.group(1), base_url)
        for match in _ROOT_RE.finditer(normalized):
            _append_url(found, seen, match.group(0), base_url)
    return found


def _is_http_url(url: str) -> bool:
    try:
        return urlsplit(url).scheme.lower() in {"http", "https"}
    except ValueError:
        return False


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    if port is None:
        port = 443 if parts.scheme.lower() == "https" else 80
    return parts.scheme.lower(), (parts.hostname or "").lower(), port


def _same_origin(first: str, second: str) -> bool:
    return _origin(first) is not None and _origin(first) == _origin(second)


class _PageParser(HTMLParser):
    """HTML からスクリプト・iframe・base を集める。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.base_href: str | None = None
        self.script_srcs: list[str] = []
        self.iframe_srcs: list[str] = []
        self.inline_scripts: list[str] = []
        self._script_parts: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        attributes: dict[str, str] = {}
        for name, value in attrs:
            if value is not None:
                attributes[name.lower()] = value

        if tag == "base":
            if self.base_href is None:
                self.base_href = attributes.get("href")
        elif tag == "script":
            src = attributes.get("src")
            if src:
                self.script_srcs.append(src)
            else:
                self._script_parts = []
        elif tag in ("iframe", "frame"):
            src = attributes.get("src")
            if src:
                self.iframe_srcs.append(src)

    def handle_data(self, data: str) -> None:
        if self._script_parts is not None:
            self._script_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._script_parts is not None:
            self.inline_scripts.append("".join(self._script_parts))
            self._script_parts = None


class _Collector:
    """HTML と関連リソースをたどって候補 URL を集める。"""

    def __init__(
        self,
        *,
        fetch: FetchText,
        headers: dict[str, str],
        timeout: float,
    ) -> None:
        self.fetch = fetch
        self.headers = dict(headers)
        self.timeout = timeout
        self.hints: dict[str, CandidateHint] = {}
        self.documents = 0
        self.scripts = 0
        self.visited: set[str] = set()
        self._limit_warned = False

    def collect_document(self, text: str, document_url: str, depth: int) -> None:
        if depth > MAX_IFRAME_DEPTH or document_url in self.visited:
            return
        if self.documents >= MAX_DOCUMENTS:
            return
        self.visited.add(document_url)
        self.documents += 1

        parser = _PageParser()
        try:
            parser.feed(text)
            parser.close()
        except Exception:  # noqa: BLE001 - 壊れた HTML でも走査を続ける
            logger.debug(
                "HTML の解析に失敗しました: %s", document_url, exc_info=True
            )

        page_base = document_url
        if parser.base_href:
            page_base = urljoin(document_url, parser.base_href)

        # インライン JS を先に走査し、検出元を「インラインJS」と表示する。
        for script in parser.inline_scripts:
            self._add_text(script, page_base, "インラインJS")
        self._add_text(text, page_base, "HTML")

        for src in parser.script_srcs:
            if self.scripts >= MAX_SCRIPTS:
                break
            script_url = urljoin(page_base, src)
            if not _same_origin(script_url, document_url):
                continue
            script_text = self._fetch_resource(script_url, document_url)
            if script_text is None:
                continue
            self.scripts += 1
            self._add_text(
                script_text, script_url, f"スクリプト: {script_url}"
            )

        if depth >= MAX_IFRAME_DEPTH:
            return
        for src in parser.iframe_srcs:
            iframe_url = urljoin(page_base, src)
            if not _is_http_url(iframe_url):
                continue
            iframe_text = self._fetch_resource(iframe_url, document_url)
            if iframe_text is None:
                continue
            self.collect_document(iframe_text, iframe_url, depth + 1)

    def _add_text(self, text: str, base_url: str, origin: str) -> None:
        for url in extract_m3u8_urls(text, base_url):
            if url in self.hints:
                continue
            if len(self.hints) >= MAX_CANDIDATES:
                if not self._limit_warned:
                    logger.warning(
                        "m3u8 の候補が多すぎるため、最初の %d 件だけを使用します",
                        MAX_CANDIDATES,
                    )
                    self._limit_warned = True
                return
            self.hints[url] = CandidateHint(url=url, origin=origin)

    def _fetch_resource(self, url: str, referer: str) -> str | None:
        resource_headers = dict(self.headers)
        resource_headers.setdefault("Referer", referer)
        try:
            text, _ = self.fetch(
                url,
                headers=resource_headers,
                timeout=self.timeout,
                retries=1,
                max_bytes=MAX_RESOURCE_BYTES,
            )
            return text
        except (DownloadError, OSError) as exc:
            logger.debug("リソースを取得できませんでした: %s (%s)", url, exc)
            return None


def _probe_one(
    hint: CandidateHint,
    headers: dict[str, str],
    timeout: float,
    fetch: FetchText,
) -> SourceCandidate:
    try:
        text, final_url = fetch(
            hint.url,
            headers=headers,
            timeout=min(timeout, PROBE_TIMEOUT),
            retries=1,
            max_bytes=MAX_RESOURCE_BYTES,
        )
    except (DownloadError, OSError) as exc:
        return SourceCandidate(
            url=hint.url,
            origin=hint.origin,
            error=str(exc),
            headers=dict(hint.headers),
        )

    stripped = text.lstrip("\ufeff \t\r\n")
    if not stripped.startswith(HLS_PREFIX):
        return SourceCandidate(
            url=hint.url,
            origin=hint.origin,
            error="m3u8 プレイリストではありません",
            headers=dict(hint.headers),
        )

    try:
        playlist = parse_playlist(text, final_url)
    except PlaylistError as exc:
        return SourceCandidate(
            url=hint.url,
            origin=hint.origin,
            error=str(exc),
            headers=dict(hint.headers),
        )

    return SourceCandidate(
        url=hint.url,
        origin=hint.origin,
        playlist=playlist,
        headers=dict(hint.headers),
    )


def probe_candidates(
    hints: list[CandidateHint],
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    fetch: FetchText | None = None,
    workers: int = PROBE_WORKERS,
) -> list[SourceCandidate]:
    """候補を実際に取得してプレイリストかどうかを確認する。"""
    if not hints:
        return []

    fetcher = fetch or fetch_text_with_url
    base_headers = dict(headers or {})

    def probe(hint: CandidateHint) -> SourceCandidate:
        merged = dict(hint.headers)
        merged.update(base_headers)
        return _probe_one(hint, merged, timeout, fetcher)

    if workers <= 1 or len(hints) == 1:
        return [probe(hint) for hint in hints]
    with ThreadPoolExecutor(max_workers=min(workers, len(hints))) as executor:
        return list(executor.map(probe, hints))


def discover_static(
    page_url: str,
    *,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
    retries: int = 3,
    fetch: FetchText | None = None,
    probe_workers: int = PROBE_WORKERS,
) -> DiscoveryResult:
    """HTML の静的解析で m3u8 の候補を探す。"""
    fetcher = fetch or fetch_text_with_url
    base_headers = dict(headers or {})

    text, final_url = fetcher(
        page_url,
        headers=base_headers,
        timeout=timeout,
        retries=retries,
        max_bytes=MAX_RESOURCE_BYTES,
    )

    if text.lstrip("\ufeff \t\r\n").startswith(HLS_PREFIX):
        playlist = parse_playlist(text, final_url)
        candidate = SourceCandidate(
            url=final_url,
            origin="ページ自体",
            playlist=playlist,
        )
        return DiscoveryResult(candidates=[candidate], page_url=final_url)

    collector = _Collector(
        fetch=fetcher, headers=base_headers, timeout=timeout
    )
    collector.collect_document(text, final_url, 0)

    probe_headers = dict(base_headers)
    probe_headers.setdefault("Referer", final_url)
    candidates = probe_candidates(
        list(collector.hints.values()),
        headers=probe_headers,
        timeout=timeout,
        fetch=fetcher,
        workers=probe_workers,
    )
    return DiscoveryResult(candidates=candidates, page_url=final_url)


def _format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def format_candidate(
    index: int,
    candidate: SourceCandidate,
    *,
    default: bool = False,
) -> str:
    """候補を一覧表示用の文字列にする。"""
    if candidate.playlist is None:
        status = f"取得失敗: {candidate.error}"
    elif candidate.playlist.is_master:
        status = f"マスター Variant={len(candidate.playlist.variants)}"
    else:
        status = (
            f"メディア セグメント={len(candidate.playlist.segments)}"
            f" 時間={_format_duration(candidate.playlist.total_duration)}"
        )
    if default:
        status += " (既定)"
    return (
        f"  [{index}] {status}\n"
        f"      検出元: {candidate.origin}\n"
        f"      {candidate.url}"
    )


VIDEO_PLAYLIST = "video.m3u8"


def _candidate_filename(url: str) -> str:
    """URL のパス末尾（ファイル名）を取り出す。クエリや断片は無視する。"""
    return urlsplit(url).path.rsplit("/", 1)[-1].lower()


def video_candidate_index(
    candidates: list[SourceCandidate],
) -> int | None:
    """ファイル名が ``video.m3u8`` の有効な候補の番号を返す。"""
    for candidate_index, candidate in enumerate(candidates):
        if not candidate.ok:
            continue
        if _candidate_filename(candidate.url) == VIDEO_PLAYLIST:
            return candidate_index
    return None


def default_candidate_index(
    candidates: list[SourceCandidate],
) -> int | None:
    """番号選択の既定値を返す。

    ファイル名が ``video.m3u8`` の候補を優先する（配信元でよく使われる
    メディアプレイリストで、マスターより目的の動画に近いため）。
    無ければ最初の有効な候補にする。
    """
    video_index = video_candidate_index(candidates)
    if video_index is not None:
        return video_index
    for candidate_index, candidate in enumerate(candidates):
        if candidate.ok:
            return candidate_index
    return None


def select_candidate(
    candidates: list[SourceCandidate],
    *,
    index: int | None = None,
    interactive: bool | None = None,
    prefer_video: bool = False,
) -> SourceCandidate:
    """一覧から使用する候補を選ぶ。"""
    if not candidates:
        raise DiscoveryError(
            "Webページから m3u8 の候補を検出できませんでした。"
            "JS で動的に URL を生成するページでは "
            "--page-mode browser を指定してください。"
        )

    if index is not None:
        if index < 0 or index >= len(candidates):
            raise DiscoveryError(
                "--source-index が範囲外です"
                f"（候補は {len(candidates)} 件、0 始まり）"
            )
        selected = candidates[index]
        if not selected.ok:
            raise DiscoveryError(
                f"候補 {index} は取得できませんでした: {selected.error}"
            )
        return selected

    if prefer_video:
        video_index = video_candidate_index(candidates)
        if video_index is None:
            raise DiscoveryError(
                f"ファイル名が {VIDEO_PLAYLIST} の候補が見つかりませんでした。"
                "--list-candidates で候補を確認し、"
                "--source-index で番号を指定してください。"
            )
        return candidates[video_index]

    valid = [candidate for candidate in candidates if candidate.ok]
    if len(valid) == 1:
        return valid[0]
    if not valid:
        details = "\n".join(
            f"  - {candidate.url}: {candidate.error}"
            for candidate in candidates[:5]
        )
        raise DiscoveryError(
            "m3u8 の候補は見つかりましたが、"
            "プレイリストとして取得できませんでした。\n" + details
        )

    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        raise DiscoveryError(
            "m3u8 の候補が複数あります。--source-index で番号を指定してください"
            f"（0〜{len(candidates) - 1}）"
        )

    default_index = default_candidate_index(candidates)
    assert default_index is not None  # 有効な候補が 2 件以上ある
    print("m3u8 の候補が複数あります。", file=sys.stderr)
    for candidate_index, candidate in enumerate(candidates):
        print(
            format_candidate(
                candidate_index,
                candidate,
                default=candidate_index == default_index,
            ),
            file=sys.stderr,
        )

    for _ in range(3):
        sys.stderr.write(f"番号を入力してください [{default_index}]: ")
        sys.stderr.flush()
        try:
            answer = input()
        except EOFError:
            break
        answer = answer.strip()
        if not answer:
            selected_index = default_index
        else:
            try:
                selected_index = int(answer)
            except ValueError:
                print("番号を入力してください。", file=sys.stderr)
                continue
        if (
            0 <= selected_index < len(candidates)
            and candidates[selected_index].ok
        ):
            return candidates[selected_index]
        print("有効な番号を入力してください。", file=sys.stderr)

    raise DiscoveryError("候補を選択できませんでした")
