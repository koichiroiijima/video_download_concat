from __future__ import annotations

import base64
import hashlib
import socket
import threading

import pytest

from video_concat.ws import WebSocket, WebSocketError

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    first = (0x80 if fin else 0) | opcode
    length = len(payload)
    if length < 126:
        header = bytes([first, length])
    elif length < 65536:
        header = bytes([first, 126]) + length.to_bytes(2, "big")
    else:
        header = bytes([first, 127]) + length.to_bytes(8, "big")
    return header + payload


def _read_exact(connection: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = connection.recv(size - len(data))
        if not chunk:
            raise ConnectionError("closed")
        data += chunk
    return data


def _read_frame(connection: socket.socket) -> tuple[int, bytes, bool]:
    first, second = _read_exact(connection, 2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = int.from_bytes(_read_exact(connection, 2), "big")
    elif length == 127:
        length = int.from_bytes(_read_exact(connection, 8), "big")
    mask = _read_exact(connection, 4) if masked else None
    payload = _read_exact(connection, length)
    if mask is not None:
        payload = bytes(
            value ^ mask[index % 4] for index, value in enumerate(payload)
        )
    return opcode, payload, masked


class _Server(threading.Thread):
    def __init__(self) -> None:
        super().__init__(daemon=True)
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.frames: list[tuple[int, bytes, bool]] = []
        self.error: Exception | None = None

    def run(self) -> None:
        try:
            connection, _ = self.sock.accept()
            request = b""
            while b"\r\n\r\n" not in request:
                request += connection.recv(4096)
            key = ""
            for line in request.decode("latin-1").split("\r\n"):
                name, _, value = line.partition(":")
                if name.strip().lower() == "sec-websocket-key":
                    key = value.strip()
            accept = base64.b64encode(
                hashlib.sha1((key + _GUID).encode("ascii")).digest()
            ).decode("ascii")
            response = (
                "HTTP/1.1 101 Switching Protocols\r\n"
                "Upgrade: websocket\r\n"
                "Connection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept}\r\n"
                "\r\n"
            )
            connection.sendall(response.encode("ascii"))

            connection.sendall(_frame(0x9, b"p"))
            connection.sendall(_frame(0x1, b"hel", fin=False))
            connection.sendall(_frame(0x0, b"lo", fin=True))
            connection.sendall(_frame(0x1, b"x" * 200))

            self.frames.append(_read_frame(connection))
            self.frames.append(_read_frame(connection))
            connection.close()
        except Exception as exc:  # noqa: BLE001 - テスト側で確認する
            self.error = exc
        finally:
            self.sock.close()


def test_websocket_handshake_fragments_and_masking() -> None:
    server = _Server()
    server.start()
    ws = WebSocket(f"ws://127.0.0.1:{server.port}/devtools/page/1", timeout=5)

    assert ws.recv_message() == "hello"
    assert ws.recv_message() == "x" * 200
    ws.send_text("world")
    server.join(timeout=5)

    assert server.error is None
    assert server.frames[0][0] == 0xA
    assert server.frames[0][1] == b"p"
    assert server.frames[1][0] == 0x1
    assert server.frames[1][1] == b"world"
    assert all(masked for _, _, masked in server.frames)

    ws.close()


def test_websocket_rejects_non_ws_url() -> None:
    with pytest.raises(WebSocketError):
        WebSocket("http://127.0.0.1:1/")
