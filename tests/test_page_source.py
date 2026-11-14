from __future__ import annotations

import threading

import pytest

from video_concat.net import DownloadError
from video_concat.page_source import (
    CandidateHint,
    DiscoveryError,
    SourceCandidate,
    discover_static,
    extract_m3u8_urls,
    probe_candidates,
    select_candidate,
)

PLAYLIST = "\n".join(
    [
        "#EXTM3U",
        "#EXT-X-VERSION:3",
        "#EXT-X-TARGETDURATION:1",
        "#EXTINF:1.0,",
        "seg0.ts",
        "#EXTINF:1.0,",
        "seg1.ts",
        "#EXT-X-ENDLIST",
        "",
    ]
)

MASTER = "\n".join(
    [
        "#EXTM3U",
        '#EXT-X-STREAM-INF:BANDWIDTH=1000000,RESOLUTION=1280x720',
        "720p/playlist.m3u8",
        "",
    ]
)


class FakeFetch:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.calls: list[str] = []
        self.lock = threading.Lock()

    def __call__(
        self,
        url: str,
        *,
        headers=None,
        timeout: float = 30.0,
        retries: int = 3,
        max_bytes: int | None = None,
    ) -> tuple[str, str]:
        with self.lock:
            self.calls.append(url)
        if url not in self.pages:
            raise DownloadError(f"{url}: HTTP 404 Not Found")
        return self.pages[url], url


def _discover(fetch: FakeFetch, page_url: str = "https://example.com/watch"):
    return discover_static(
        page_url,
        fetch=fetch,
        probe_workers=1,
        retries=1,
    )


def test_absolute_url_in_html() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": (
                '<video src="https://cdn.example.com/video.m3u8?token=abc">'
            ),
            "https://cdn.example.com/video.m3u8?token=abc": PLAYLIST,
        }
    )

    result = _discover(fetch)

    assert len(result.candidates) == 1
    candidate = result.candidates[0]
    assert candidate.ok
    assert candidate.url == "https://cdn.example.com/video.m3u8?token=abc"
    assert len(candidate.playlist.segments) == 2


def test_protocol_relative_and_root_relative() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": (
                '<source src="//cdn.example.com/a.m3u8">'
                '<source src="/media/b.m3u8">'
            ),
            "https://cdn.example.com/a.m3u8": PLAYLIST,
            "https://example.com/media/b.m3u8": PLAYLIST,
        }
    )

    urls = [candidate.url for candidate in _discover(fetch).candidates]

    assert urls == [
        "https://cdn.example.com/a.m3u8",
        "https://example.com/media/b.m3u8",
    ]


def test_quoted_relative_and_base_href() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": (
                '<base href="/media/">'
                '<script>const source = "video.m3u8";</script>'
            ),
            "https://example.com/media/video.m3u8": PLAYLIST,
        }
    )

    result = _discover(fetch)

    assert [candidate.url for candidate in result.candidates] == [
        "https://example.com/media/video.m3u8"
    ]
    assert result.candidates[0].origin == "インラインJS"


def test_json_escapes_and_html_entity() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": (
                '<script>var a = "https:\\/\\/cdn.example.com\\/a.m3u8";'
                'var b = "https:\\u002F\\u002Fcdn.example.com\\u002Fb.m3u8";</script>'
                '<video src="https://cdn.example.com/c.m3u8?x=1&amp;y=2">'
            ),
            "https://cdn.example.com/a.m3u8": PLAYLIST,
            "https://cdn.example.com/b.m3u8": PLAYLIST,
            "https://cdn.example.com/c.m3u8?x=1&y=2": PLAYLIST,
        }
    )

    urls = [candidate.url for candidate in _discover(fetch).candidates]

    assert urls == [
        "https://cdn.example.com/a.m3u8",
        "https://cdn.example.com/b.m3u8",
        "https://cdn.example.com/c.m3u8?x=1&y=2",
    ]


def test_same_origin_script_is_scanned() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": '<script src="/player.js"></script>',
            "https://example.com/player.js": (
                'var source = "/media/video.m3u8";'
            ),
            "https://example.com/media/video.m3u8": PLAYLIST,
        }
    )

    result = _discover(fetch)

    assert [candidate.url for candidate in result.candidates] == [
        "https://example.com/media/video.m3u8"
    ]
    assert result.candidates[0].origin.startswith("スクリプト:")


def test_cross_origin_script_is_not_fetched() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": (
                '<script src="https://other.example/player.js"></script>'
            ),
        }
    )

    _discover(fetch)

    assert "https://other.example/player.js" not in fetch.calls


def test_iframe_is_scanned() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": '<iframe src="/frame.html"></iframe>',
            "https://example.com/frame.html": (
                '<video src="/media/video.m3u8"></video>'
            ),
            "https://example.com/media/video.m3u8": PLAYLIST,
        }
    )

    result = _discover(fetch)

    assert [candidate.url for candidate in result.candidates] == [
        "https://example.com/media/video.m3u8"
    ]
    assert "https://example.com/frame.html" in fetch.calls


def test_iframe_depth_limit() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": '<iframe src="/f1.html"></iframe>',
            "https://example.com/f1.html": '<iframe src="/f2.html"></iframe>',
            "https://example.com/f2.html": '<iframe src="/f3.html"></iframe>',
            "https://example.com/f3.html": '<video src="/deep.m3u8"></video>',
            "https://example.com/deep.m3u8": PLAYLIST,
        }
    )

    _discover(fetch)

    assert "https://example.com/f3.html" not in fetch.calls


def test_duplicates_are_removed_in_order() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": (
                '<video src="/a.m3u8"></video>'
                '<video src="https://example.com/a.m3u8"></video>'
                '<video src="/b.m3u8"></video>'
            ),
            "https://example.com/a.m3u8": PLAYLIST,
            "https://example.com/b.m3u8": PLAYLIST,
        }
    )

    urls = [candidate.url for candidate in _discover(fetch).candidates]

    assert urls == [
        "https://example.com/a.m3u8",
        "https://example.com/b.m3u8",
    ]


def test_page_itself_is_playlist() -> None:
    fetch = FakeFetch({"https://example.com/video.m3u8": PLAYLIST})

    result = _discover(fetch, page_url="https://example.com/video.m3u8")

    assert len(result.candidates) == 1
    assert result.candidates[0].origin == "ページ自体"
    assert result.candidates[0].ok


def test_probe_failure_is_recorded() -> None:
    fetch = FakeFetch(
        {
            "https://example.com/watch": '<video src="/not-playlist.m3u8">',
            "https://example.com/not-playlist.m3u8": "<html>error</html>",
        }
    )

    result = _discover(fetch)

    assert len(result.candidates) == 1
    assert not result.candidates[0].ok
    assert "プレイリストではありません" in result.candidates[0].error


def test_extract_urls_strips_fragment_and_punctuation() -> None:
    urls = extract_m3u8_urls(
        '(url = "https://example.com/a.m3u8#fragment"),'
        " https://example.com/b.m3u8;",
        "https://example.com/",
    )

    assert urls == [
        "https://example.com/a.m3u8",
        "https://example.com/b.m3u8",
    ]


def test_select_candidate_single_valid() -> None:
    valid = SourceCandidate(
        url="https://example.com/a.m3u8",
        origin="HTML",
        playlist=object(),
    )
    failed = SourceCandidate(
        url="https://example.com/b.m3u8",
        origin="HTML",
        error="404",
    )

    assert select_candidate([failed, valid], interactive=False) is valid


def test_select_candidate_index() -> None:
    first = SourceCandidate(url="a", origin="HTML", playlist=object())
    second = SourceCandidate(url="b", origin="HTML", playlist=object())

    assert select_candidate([first, second], index=1) is second
    with pytest.raises(DiscoveryError):
        select_candidate([first, second], index=2)


def test_select_candidate_multiple_requires_index() -> None:
    candidates = [
        SourceCandidate(url="a", origin="HTML", playlist=object()),
        SourceCandidate(url="b", origin="HTML", playlist=object()),
    ]

    with pytest.raises(DiscoveryError, match="--source-index"):
        select_candidate(candidates, interactive=False)


def test_select_candidate_no_candidates() -> None:
    with pytest.raises(DiscoveryError, match="検出できませんでした"):
        select_candidate([], interactive=False)


def test_probe_candidates_uses_referer() -> None:
    seen: dict[str, str | None] = {}

    def fetch(url, *, headers=None, timeout=30.0, retries=3, max_bytes=None):
        seen["referer"] = (headers or {}).get("Referer")
        return PLAYLIST, url

    candidates = probe_candidates(
        [CandidateHint(url="https://example.com/a.m3u8", origin="HTML")],
        headers={"Referer": "https://example.com/watch"},
        fetch=fetch,
        workers=1,
    )

    assert candidates[0].ok
    assert seen["referer"] == "https://example.com/watch"
