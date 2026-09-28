"""Small TCP transport used by the app and engine processes."""

from __future__ import annotations

import json
import socket
import struct
from typing import Any


MAX_MESSAGE_BYTES = 16 * 1024 * 1024


class ProtocolError(RuntimeError):
    """Raised when a TCP message does not follow the expected format."""


class Network:
    """Create TCP connections and exchange length-prefixed JSON objects."""

    def __init__(self, owner_id: str = "app") -> None:
        self.owner_id = owner_id

    def listen(self, host: str, port: int) -> socket.socket:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server.bind((host, port))
            server.listen(1)
            self._print(f"listening on {host}:{port}")
            return server
        except Exception:
            server.close()
            raise

    def connect(
        self,
        host: str,
        port: int,
        timeout: float,
        peer_id: str = "peer",
    ) -> socket.socket:
        connection = socket.create_connection((host, port), timeout=timeout)
        self._print(f"connected to {peer_id} at {host}:{port}")
        return connection

    def accept(self, server: socket.socket, peer_id: str = "peer") -> socket.socket:
        connection, address = server.accept()
        self._print(f"accepted {peer_id} from {address[0]}:{address[1]}")
        return connection

    def reserve_port(self, host: str) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
            reservation.bind((host, 0))
            return reservation.getsockname()[1]

    def send(
        self,
        connection: socket.socket,
        payload: dict[str, Any],
        peer_id: str = "peer",
    ) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        if len(encoded) > MAX_MESSAGE_BYTES:
            raise ProtocolError("message exceeds the 16 MiB limit")
        connection.sendall(struct.pack("!I", len(encoded)) + encoded)
        self._print(f"sent {self._describe(payload)} -> {peer_id}")

    def receive(
        self,
        connection: socket.socket,
        peer_id: str = "peer",
    ) -> dict[str, Any]:
        size = struct.unpack("!I", self._receive_exact(connection, 4))[0]
        if size > MAX_MESSAGE_BYTES:
            raise ProtocolError("message exceeds the 16 MiB limit")
        payload = json.loads(self._receive_exact(connection, size).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ProtocolError("message payload must be an object")
        self._print(f"received {self._describe(payload)} <- {peer_id}")
        return payload

    def close(self, connection: socket.socket, peer_id: str = "peer") -> None:
        connection.close()
        self._print(f"closed connection with {peer_id}")

    def _print(self, message: str) -> None:
        print(f"[tcp {self.owner_id}] {message}", flush=True)

    @staticmethod
    def _describe(payload: dict[str, Any]) -> str:
        operation = payload.get("command", "response")
        identifiers = []
        for name in ("object_id", "engine_id"):
            if name in payload:
                identifiers.append(f"{name}={payload[name]}")
        if operation == "response":
            identifiers.insert(0, "ok" if payload.get("ok") else "warning")
        details = f" ({', '.join(identifiers)})" if identifiers else ""
        return f"{operation}{details}"

    @staticmethod
    def _receive_exact(connection: socket.socket, size: int) -> bytes:
        chunks = bytearray()
        while len(chunks) < size:
            chunk = connection.recv(size - len(chunks))
            if not chunk:
                raise EOFError("socket closed while receiving a message")
            chunks.extend(chunk)
        return bytes(chunks)
