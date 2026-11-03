"""m3u8 / HLS プレイリストのパーサー。

標準のメディアプレイリストに加えて、次の実運用で遭遇しやすい形を扱う。

- マスタープレイリスト（``#EXT-X-STREAM-INF``）
- ``video0.jpeg`` のように拡張子が実体と異なるセグメント
- 途中に ``#EXT-X-ENDLIST`` が複数回現れる、壊れた/連結されたプレイリスト
- 非標準の ``#EXT-X-TOKEN``（トークンはセグメント単位で保持する）
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_ATTRIBUTE_RE = re.compile(r'([A-Za-z0-9-]+)=("[^"]*"|[^,]*)')
_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,8}$")


class PlaylistError(ValueError):
    """プレイリストが不正、または未対応のときに送出する。"""


@dataclass(frozen=True)
class Variant:
    """マスタープレイリスト内の 1 ストリーム。"""

    index: int
    url: str
    bandwidth: int | None = None
    resolution: str | None = None
    codecs: str | None = None

    def describe(self) -> str:
        parts = [f"[{self.index}]"]
        if self.bandwidth:
            parts.append(f"{self.bandwidth / 1000:.0f} kbps")
        if self.resolution:
            parts.append(self.resolution)
        if self.codecs:
            parts.append(self.codecs)
        return " ".join(parts)


@dataclass
class Segment:
    """メディアプレイリスト内の 1 セグメント。"""

    index: int
    uri: str
    url: str
    duration: float | None = None
    token: str | None = None


@dataclass
class Playlist:
    """解析結果。``variants`` があればマスター、``segments`` があればメディア。"""

    source: str
    variants: list[Variant] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    target_duration: float | None = None
    media_sequence: int = 0
    has_endlist: bool = False
    has_byterange: bool = False
    encryption: str | None = None
    init_uri: str | None = None
    tokens: list[str] = field(default_factory=list)

    @property
    def is_master(self) -> bool:
        return bool(self.variants) and not self.segments

    @property
    def total_duration(self) -> float:
        return sum(segment.duration or 0.0 for segment in self.segments)


def parse_attributes(value: str) -> dict[str, str]:
    """``BANDWIDTH=123,CODECS="..."`` 形式の属性リストを辞書にする。"""
    attributes: dict[str, str] = {}
    for key, raw_value in _ATTRIBUTE_RE.findall(value):
        attributes[key.upper()] = raw_value.strip().strip('"')
    return attributes


def _looks_like_uri(line: str) -> bool:
    """壊れたプレイリスト中の URI 行らしきものを判定する。

    ``4000,`` のような壊れた数値行をセグメントとして扱わないための保険。
    """
    if line.startswith(("/", "./", "../", "//")):
        return True
    if "?" in line or "#" in line:
        return True
    name = line.split("?", 1)[0].rsplit("/", 1)[-1]
    return bool(_EXTENSION_RE.search(name))


def _append_token(playlist: Playlist, token: str | None) -> None:
    if token and token not in playlist.tokens:
        playlist.tokens.append(token)


def parse_playlist(text: str, source: str, *, strict: bool = False) -> Playlist:
    """プレイリスト文字列を解析する。

    Args:
        text: m3u8 の内容。
        source: 相対 URI を解決する基準 URL（``file://`` でもよい）。
        strict: True なら ``#EXTM3U`` の欠落をエラーにする。
    """
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff")

    playlist = Playlist(source=source)
    lines = text.splitlines()

    if not lines or lines[0].strip() != "#EXTM3U":
        message = "プレイリストの先頭が #EXTM3U ではありません"
        if strict:
            raise PlaylistError(message)
        logger.warning(message)

    pending_duration: float | None = None
    pending_variant: dict[str, str] | None = None
    current_token: str | None = None

    for line_number, raw_line in enumerate(lines, 1):
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith("#"):
            upper = line.upper()

            # 非標準タグ。`#EXT-X-TOKEN=...` と `#EXT-X-TOKEN:...` の両方に対応する。
            if upper.startswith("#EXT-X-TOKEN="):
                current_token = line.split("=", 1)[1].strip() or None
                _append_token(playlist, current_token)
                continue

            tag, separator, value = line.partition(":")
            tag = tag.upper()
            value = value.strip()

            if tag == "#EXT-X-TOKEN":
                current_token = value or None
                _append_token(playlist, current_token)
            elif tag == "#EXT-X-STREAM-INF":
                pending_variant = parse_attributes(value)
            elif tag == "#EXTINF":
                duration_text = value.split(",", 1)[0].strip()
                try:
                    pending_duration = float(duration_text)
                except ValueError:
                    pending_duration = None
                    logger.warning("%d 行目: EXTINF の秒数を解釈できません: %r", line_number, value)
            elif tag == "#EXT-X-TARGETDURATION":
                try:
                    playlist.target_duration = float(value)
                except ValueError:
                    logger.warning("%d 行目: TARGETDURATION を解釈できません: %r", line_number, value)
            elif tag == "#EXT-X-MEDIA-SEQUENCE":
                try:
                    playlist.media_sequence = int(value)
                except ValueError:
                    logger.warning("%d 行目: MEDIA-SEQUENCE を解釈できません: %r", line_number, value)
            elif tag == "#EXT-X-ENDLIST":
                playlist.has_endlist = True
                # 壊れたプレイリストで ENDLIST の直後に URI が続くことがあるため、
                # 保留中の EXTINF はここで破棄する。
                pending_duration = None
            elif tag == "#EXT-X-BYTERANGE":
                playlist.has_byterange = True
            elif tag == "#EXT-X-KEY":
                attributes = parse_attributes(value)
                method = attributes.get("METHOD", "").upper()
                if method and method != "NONE":
                    playlist.encryption = method
            elif tag == "#EXT-X-MAP":
                attributes = parse_attributes(value)
                if attributes.get("URI"):
                    playlist.init_uri = attributes["URI"]
            elif tag == "#EXT-X-MEDIA":
                attributes = parse_attributes(value)
                if attributes.get("TYPE", "").upper() == "AUDIO" and attributes.get("URI"):
                    logger.warning(
                        "分離された音声トラック (EXT-X-MEDIA) には未対応です: %s",
                        attributes["URI"],
                    )
            # その他のタグは無視する。
            continue

        # URI 行
        if pending_variant is not None:
            playlist.variants.append(
                Variant(
                    index=len(playlist.variants),
                    url=urljoin(source, line),
                    bandwidth=_to_int(pending_variant.get("BANDWIDTH")),
                    resolution=pending_variant.get("RESOLUTION"),
                    codecs=pending_variant.get("CODECS"),
                )
            )
            pending_variant = None
            continue

        if pending_duration is None and not _looks_like_uri(line):
            logger.warning("%d 行目: セグメント URI ではない行を無視します: %r", line_number, line)
            continue

        if pending_duration is None:
            logger.warning("%d 行目: EXTINF のないセグメント URI を検出しました: %s", line_number, line)

        playlist.segments.append(
            Segment(
                index=len(playlist.segments),
                uri=line,
                url=urljoin(source, line),
                duration=pending_duration,
                token=current_token,
            )
        )
        pending_duration = None

    if pending_variant is not None:
        logger.warning("EXT-X-STREAM-INF に対応する URI がありません")

    if not playlist.variants and not playlist.segments:
        raise PlaylistError("プレイリストにセグメントがありません")

    return playlist


def select_variant(playlist: Playlist, choice: str = "best") -> Variant:
    """マスタープレイリストから 1 つ選ぶ。``choice`` は ``best`` か 0 始まりの番号。"""
    if not playlist.variants:
        raise PlaylistError("マスタープレイリストではありません")

    if choice in ("", "best", "BEST"):
        return max(
            playlist.variants,
            key=lambda variant: (
                variant.bandwidth or 0,
                _resolution_height(variant.resolution),
            ),
        )

    try:
        index = int(choice)
    except ValueError as exc:
        raise PlaylistError(
            f"--variant には best か 0 始まりの番号を指定してください: {choice!r}"
        ) from exc

    for variant in playlist.variants:
        if variant.index == index:
            return variant

    raise PlaylistError(
        f"Variant {index} は存在しません（0〜{len(playlist.variants) - 1}）"
    )


def apply_token(url: str, token: str, query_name: str) -> str:
    """``#EXT-X-TOKEN`` の値を URL のクエリパラメータとして付与する。"""
    if not token or not query_name:
        return url

    parts = urlsplit(url)
    query = parse_qsl(parts.query, keep_blank_values=True)
    replaced = False
    for index, (key, _) in enumerate(query):
        if key == query_name:
            query[index] = (key, token)
            replaced = True
            break
    if not replaced:
        query.append((query_name, token))

    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )


def _to_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _resolution_height(resolution: str | None) -> int:
    if not resolution or "x" not in resolution:
        return 0
    try:
        return int(resolution.lower().split("x", 1)[1])
    except ValueError:
        return 0
