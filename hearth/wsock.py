"""A WebSocket on the window's own server (RFC 6455, text frames only).

Enough for one page talking to this PC both ways at once, like voice mode's
audio frames: the handshake, masked frames from the browser, plain frames
back, ping and close. The HTTP checks (host, origin) run before accept().
"""
from __future__ import annotations

import base64
import hashlib
import struct
import threading
from typing import Optional

_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
MAX_FRAME = 4 * 1024 * 1024


def is_upgrade(headers) -> bool:
    return ("websocket" in (headers.get("Upgrade") or "").lower()
            and bool(headers.get("Sec-WebSocket-Key")))


class Closed(Exception):
    pass


class Socket:
    """One accepted connection. recv() blocks for the next text message;
    send() may be called from any thread."""

    def __init__(self, rfile, wfile):
        self.r, self.w = rfile, wfile
        self._send_lock = threading.Lock()
        self.closed = False

    # ---------------------------------------------------------------- out
    def _frame(self, op: int, data: bytes) -> None:
        n = len(data)
        if n < 126:
            head = struct.pack("!BB", 0x80 | op, n)
        elif n < 65536:
            head = struct.pack("!BBH", 0x80 | op, 126, n)
        else:
            head = struct.pack("!BBQ", 0x80 | op, 127, n)
        with self._send_lock:
            if self.closed:
                raise Closed()
            try:
                self.w.write(head + data)
                self.w.flush()
            except (OSError, ValueError) as e:
                self.closed = True
                raise Closed() from e

    def send(self, text: str) -> bool:
        try:
            self._frame(0x1, text.encode("utf-8"))
            return True
        except Closed:
            return False

    def close(self) -> None:
        if not self.closed:
            try:
                self._frame(0x8, struct.pack("!H", 1000))
            except Closed:
                pass
        self.closed = True

    # ---------------------------------------------------------------- in
    def _read(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            try:
                got = self.r.read(n - len(out))
            except (OSError, ValueError) as e:
                raise Closed() from e
            if not got:
                raise Closed()
            out += got
        return out

    def recv(self) -> Optional[str]:
        """The next text message, or None once the page has gone."""
        parts = []
        try:
            while True:
                b1, b2 = self._read(2)
                fin, op = b1 & 0x80, b1 & 0x0F
                n = b2 & 0x7F
                if n == 126:
                    n = struct.unpack("!H", self._read(2))[0]
                elif n == 127:
                    n = struct.unpack("!Q", self._read(8))[0]
                if n > MAX_FRAME:
                    raise Closed()
                mask = self._read(4) if b2 & 0x80 else b""
                data = self._read(n)
                if mask and n:
                    m = (mask * (n // 4 + 1))[:n]
                    data = (int.from_bytes(data, "big") ^ int.from_bytes(m, "big")).to_bytes(n, "big")
                if op == 0x8:
                    self.close()
                    return None
                if op == 0x9:
                    self._frame(0xA, data)
                    continue
                if op == 0xA:
                    continue
                parts.append(data)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")
        except Closed:
            self.closed = True
            return None


def accept(handler) -> Socket:
    """Answer the handshake on a BaseHTTPRequestHandler and hand back the socket."""
    key = handler.headers.get("Sec-WebSocket-Key", "").strip()
    token = base64.b64encode(hashlib.sha1((key + _GUID).encode()).digest()).decode()
    handler.protocol_version = "HTTP/1.1"          # browsers take a 101 only as HTTP/1.1
    handler.send_response(101, "Switching Protocols")
    handler.send_header("Upgrade", "websocket")
    handler.send_header("Connection", "Upgrade")
    handler.send_header("Sec-WebSocket-Accept", token)
    handler.end_headers()
    handler.wfile.flush()
    handler.close_connection = True
    return Socket(handler.rfile, handler.wfile)
