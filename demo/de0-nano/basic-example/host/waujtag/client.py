# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0
"""TCP client for the wau_jtag_server.tcl line protocol."""

from __future__ import annotations

import socket
import threading
from contextlib import contextmanager
from typing import Iterator


class TCLClientError(RuntimeError):
    """Raised on protocol-level errors from the TCL server."""


class TCLClient:
    """Thin, thread-safe TCP client for the quartus_stp TCL server.

    The TCL server speaks a tiny line protocol:

        > W <addr> <data>         < OK
        > R <addr>                < D <value>
        > OBS                     < D <value>
        > RST                     < OK
        > PING                    < PONG
        > QUIT                    < BYE

    All values are decimal integers.
    """

    def __init__(self, host: str = "localhost", port: int = 2540, timeout: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._rfile = None
        self._lock = threading.Lock()

    # ---------- connection -----------
    def connect(self) -> None:
        if self._sock is not None:
            return
        s = socket.create_connection((self.host, self.port), timeout=self.timeout)
        s.settimeout(self.timeout)
        self._sock = s
        self._rfile = s.makefile("r", encoding="utf-8", newline="\n")
        # Server greets with "READY"
        greet = self._rfile.readline().strip()
        if greet != "READY":
            raise TCLClientError(f"unexpected greeting: {greet!r}")

    def close(self) -> None:
        if self._sock is None:
            return
        try:
            self._send_line("QUIT")
            try:
                self._rfile.readline()
            except Exception:
                pass
        finally:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            self._rfile = None

    def __enter__(self) -> "TCLClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ---------- raw command -----------
    def _send_line(self, line: str) -> None:
        assert self._sock is not None
        self._sock.sendall((line + "\n").encode("utf-8"))

    def _recv_line(self) -> str:
        assert self._rfile is not None
        line = self._rfile.readline()
        if not line:
            raise TCLClientError("server closed connection")
        return line.rstrip("\r\n")

    def command(self, line: str) -> str:
        """Send a single line, return the single response line."""
        with self._lock:
            if self._sock is None:
                raise TCLClientError("client not connected; call connect() or use a `with` block")
            self._send_line(line)
            resp = self._recv_line()
        if resp.startswith("ERR"):
            raise TCLClientError(resp[4:].strip())
        return resp

    # ---------- typed helpers -----------
    def ping(self) -> bool:
        return self.command("PING") == "PONG"

    def reset(self) -> None:
        if self.command("RST") != "OK":
            raise TCLClientError("RST did not return OK")

    def write32(self, addr: int, data: int) -> None:
        # cast data to unsigned 32-bit for safety on signed inputs
        u = data & 0xFFFFFFFF
        if self.command(f"W {addr} {u}") != "OK":
            raise TCLClientError("W did not return OK")

    def read32(self, addr: int) -> int:
        resp = self.command(f"R {addr}")
        if not resp.startswith("D "):
            raise TCLClientError(f"R returned {resp!r}")
        return int(resp[2:], 10)

    def obs_aux(self) -> int:
        resp = self.command("OBS")
        if not resp.startswith("D "):
            raise TCLClientError(f"OBS returned {resp!r}")
        return int(resp[2:], 10)


@contextmanager
def connect(host: str = "localhost", port: int = 2540, timeout: float = 10.0) -> Iterator[TCLClient]:
    """Context-manager shortcut: `with connect() as c: ...`."""
    c = TCLClient(host=host, port=port, timeout=timeout)
    c.connect()
    try:
        yield c
    finally:
        c.close()
