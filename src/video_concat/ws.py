"""最小限の WebSocket クライアント（RFC 6455）。

Chrome DevTools Protocol に接続するためだけに使う。標準ライブラリのみで
ハンドシェイク、マスク、分割フレーム、ping/pong を扱う。
"""

from __future__ import annotations

import base64
import hashlib
import os
import socket
from urllib.parse import urlsplit

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_MESSAGE = 64 * 1024 * 1024


class WebSocketError(RuntimeError):
    """WebSocket の通信に失敗したときに送出する。"""


class WebSocket:
    """``ws://`` の WebSocket クライアント。"""

    def __init__(self, url: str, *, timeout: float = 30.0) -> None:
        parts = urlsplit(url)
        if parts.scheme != "ws":
            raise WebSocketError(f"ws:// の URL のみ対応しています: {url}")
        host = parts.hostname or "127.0.0.1"
        port = parts.port or 80
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"

        self._sock = socket.create_connection((host, port), timeout=timeout)
        self._sock.settimeout(timeout)
        self._buffer = bytearray()
        self._closed = False
        self._handshake(parts.netloc or f"{host}:{port}", path)

    def _handshake(self, host_header: str, path: str) -> None:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            "\r\n"
        )
        self._sock.sendall(request.encode("ascii"))
        response = self._read_until(b"\r\n\r\n").decode("latin-1")
        lines = response.split("\r\n")
        status = lines[0].split() if lines else []
        if len(status) < 2 or status[1] != "101":
            raise WebSocketError(
                "WebSocket のハンドシェイクに失敗しました: "
                f"{lines[0] if lines else '応答なし'}"
            )

        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()

        expected = base64.b64encode(
            hashlib.sha1((key + _GUID).encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise WebSocketError(
                "WebSocket の Sec-WebSocket-Accept が一致しません"
            )

    def settimeout(self, seconds: float | None) -> None:
        self._sock.settimeout(seconds)

    def send_text(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def send_pong(self, payload: bytes) -> None:
        self._send_frame(0xA, payload)

    def send_close(self) -> None:
        if self._closed:
            return
        try:
            self._send_frame(0x8, b"\x03\xe8")
        except (OSError, WebSocketError):
            pass

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        if self._closed:
            raise WebSocketError("WebSocket は閉じられています")
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))

        mask = os.urandom(4)
        header.extend(mask)
        masked = bytearray(payload)
        for index in range(len(masked)):
            masked[index] ^= mask[index % 4]
        self._sock.sendall(bytes(header) + bytes(masked))

    def recv_message(self) -> str:
        fragments: list[bytes] = []
        started = False
        while True:
            fin, opcode, payload = self._recv_frame()
            if opcode == 0x8:
                self._closed = True
                raise WebSocketError("WebSocket が閉じられました")
            if opcode == 0x9:
                self.send_pong(payload)
                continue
            if opcode == 0xA:
                continue

            if opcode in (0x1, 0x2):
                fragments = [payload]
                started = True
            elif opcode == 0x0 and started:
                fragments.append(payload)
            else:
                continue

            if fin:
                return b"".join(fragments).decode("utf-8", errors="replace")

    def _recv_frame(self) -> tuple[bool, int, bytes]:
        first, second = self._read_exact(2)
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        masked = bool(second & 0x80)
        length = second & 0x7F

        if length == 126:
            length = int.from_bytes(self._read_exact(2), "big")
        elif length == 127:
            length = int.from_bytes(self._read_exact(8), "big")
        if length > _MAX_MESSAGE:
            raise WebSocketError(
                f"WebSocket のメッセージが大きすぎます: {length} バイト"
            )

        mask = self._read_exact(4) if masked else None
        payload = self._read_exact(length) if length else b""
        if mask is not None:
            payload = bytes(
                value ^ mask[index % 4]
                for index, value in enumerate(payload)
            )
        return fin, opcode, payload

    def _read_exact(self, size: int) -> bytes:
        while len(self._buffer) < size:
            chunk = self._sock.recv(65536)
            if not chunk:
                self._closed = True
                raise WebSocketError("WebSocket 接続が切断されました")
            self._buffer.extend(chunk)
        data = bytes(self._buffer[:size])
        del self._buffer[:size]
        return data

    def _read_until(self, marker: bytes, limit: int = 65536) -> bytes:
        while marker not in self._buffer:
            if len(self._buffer) > limit:
                raise WebSocketError("WebSocket の応答ヘッダが大きすぎます")
            chunk = self._sock.recv(65536)
            if not chunk:
                raise WebSocketError("WebSocket の接続が切断されました")
            self._buffer.extend(chunk)
        index = self._buffer.index(marker) + len(marker)
        data = bytes(self._buffer[:index])
        del self._buffer[:index]
        return data

    def close(self) -> None:
        if self._closed:
            return
        self.send_close()
        self._closed = True
        try:
            self._sock.close()
        except OSError:
            pass

    def __enter__(self) -> "WebSocket":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()
