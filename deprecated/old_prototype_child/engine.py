"""TCP engine processes and command parsing for the interactive demo."""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass, field
from pathlib import Path
import shlex
import socket
import subprocess
import sys
import time
from typing import Any, TextIO

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.functions import Function, deserialize_function
from src.network import Network, ProtocolError


class CommandParse:
    """Turn one terminal command into a directive or warning payload."""

    @classmethod
    def parse(cls, line: str) -> dict[str, Any]:
        try:
            tokens = shlex.split(line)
            if not tokens:
                return cls._directive("noop")

            command = tokens[0].lower()
            if command in {"exit", "quit"}:
                cls._require(tokens, 1, "exit")
                return cls._directive("exit")
            if command == "help":
                cls._require(tokens, 1, "help")
                return cls._directive("help")
            if tokens == ["list", "processes"]:
                return cls._directive("list_processes")
            if command == "create":
                return cls._parse_create(tokens)
            if command == "partition":
                cls._require_flagged(tokens, 3, "partition -id OBJECT_ID")
                return cls._directive("partition", object_id=tokens[2])
            if command == "transfer":
                cls._require_flagged(
                    tokens, 4, "transfer -id OBJECT_ID PROCESS_ID"
                )
                return cls._directive(
                    "transfer", object_id=tokens[2], process_id=tokens[3]
                )
            if command == "eval":
                cls._require_flagged(tokens, 3, "eval -id OBJECT_ID")
                return cls._directive("eval", object_id=tokens[2])
            if command == "orchestrate":
                cls._require_flagged(tokens, 3, "orchestrate -id OBJECT_ID")
                return cls._directive("orchestrate", object_id=tokens[2])
            raise ValueError(f"unknown command: {tokens[0]}")
        except (SyntaxError, ValueError) as exc:
            return {"ok": False, "warning": str(exc)}

    @classmethod
    def _parse_create(cls, tokens: list[str]) -> dict[str, Any]:
        if len(tokens) == 3 and tokens[1] == "-p":
            try:
                count = int(tokens[2])
            except ValueError as exc:
                raise ValueError("process count must be an integer") from exc
            return cls._directive("create_processes", count=count)

        if len(tokens) >= 5 and tokens[1] == "-fn" and tokens[3] == "-params":
            source = " ".join(tokens[4:])
            try:
                params = ast.literal_eval(source)
            except (SyntaxError, ValueError) as exc:
                raise ValueError(f"invalid parameter literal: {exc}") from exc
            return cls._directive(
                "create_function", class_name=tokens[2], params=params
            )

        raise ValueError(
            "usage: create -p NUMBER | "
            "create -fn CLASS_NAME -params PARAMETERS"
        )

    @staticmethod
    def _require(tokens: list[str], length: int, usage: str) -> None:
        if len(tokens) != length:
            raise ValueError(f"usage: {usage}")

    @staticmethod
    def _require_flagged(tokens: list[str], length: int, usage: str) -> None:
        if len(tokens) != length or tokens[1] != "-id":
            raise ValueError(f"usage: {usage}")

    @staticmethod
    def _directive(name: str, **arguments: Any) -> dict[str, Any]:
        return {
            "ok": True,
            "directive": name,
            "arguments": arguments,
        }


class EngineError(RuntimeError):
    """Raised when an engine cannot complete a requested operation."""


def _warning(exc: Exception) -> dict[str, Any]:
    return {"ok": False, "warning": f"{type(exc).__name__}: {exc}"}


@dataclass
class EngineServer:
    """Worker-side server state and request handling."""

    engine_id: str
    network: Network
    objects: dict[str, Function] = field(default_factory=dict)

    def run(self, host: str, port: int) -> int:
        """Accept one app connection and serve it until shutdown."""

        with self.network.listen(host, port) as listener:
            connection = self.network.accept(listener, "app")
            with connection:
                self._serve_connection(connection)
        return 0

    def _serve_connection(self, connection: socket.socket) -> None:
        while self._process_next_request(connection):
            pass

    def _process_next_request(self, connection: socket.socket) -> bool:
        try:
            request = self.network.receive(connection, "app")
        except EOFError:
            return False
        except (ProtocolError, ValueError) as exc:
            response, should_continue = _warning(exc), True
        else:
            response, should_continue = self._handle_request(request)

        self.network.send(connection, response, "app")
        return should_continue

    def _handle_request(
        self,
        request: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        try:
            return self._dispatch_request(request)
        except Exception as exc:
            return _warning(exc), True

    def _dispatch_request(
        self,
        request: dict[str, Any],
    ) -> tuple[dict[str, Any], bool]:
        command = request.get("command")
        if command == "transfer":
            return self._handle_transfer(request), True
        if command == "eval":
            return self._handle_evaluation(request), True
        if command == "shutdown":
            return self._handle_shutdown()
        raise ValueError(f"unknown engine command: {command}")

    def _handle_transfer(self, request: dict[str, Any]) -> dict[str, Any]:
        object_id = request["object_id"]
        if not isinstance(object_id, str):
            raise TypeError("object_id must be a string")

        function = deserialize_function(request["function"])
        self.objects[object_id] = function
        return {"ok": True, "engine_id": self.engine_id}

    def _handle_evaluation(self, request: dict[str, Any]) -> dict[str, Any]:
        object_id = request["object_id"]
        if object_id not in self.objects:
            raise KeyError(
                f"object {object_id!r} is not assigned to this process"
            )

        function = self.objects[object_id]
        if function.eval() is not True:
            raise RuntimeError(f"{type(function).__name__}.eval() reported failure")
        return {
            "ok": True,
            "engine_id": self.engine_id,
            "function": function.serialize(),
        }

    def _handle_shutdown(self) -> tuple[dict[str, Any], bool]:
        return {"ok": True, "engine_id": self.engine_id}, False


def serve(
    host: str,
    port: int,
    engine_id: str,
    network: Network | None = None,
) -> int:
    """Run one engine's TCP protocol until its client requests shutdown."""

    server = EngineServer(engine_id, network or Network(engine_id))
    return server.run(host, port)


@dataclass
class Engine:
    """Parent-side handle for an independently launched TCP engine process."""

    id: str
    host: str = "127.0.0.1"
    network: Network = field(default_factory=Network, repr=False)
    port: int = field(init=False)
    object_ids: set[str] = field(default_factory=set, init=False)
    evaluating_object_id: str | None = field(default=None, init=False)
    _socket: socket.socket = field(init=False, repr=False)
    _process: subprocess.Popen[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.port = self.network.reserve_port(self.host)
        project_root = Path(__file__).resolve().parent.parent
        self._process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "src.engine",
                "--serve",
                "--host",
                self.host,
                "--port",
                str(self.port),
                "--id",
                self.id,
            ],
            cwd=project_root,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            self._socket = self._connect()
        except Exception:
            self._stop_process()
            raise

    def _connect(self) -> socket.socket:
        deadline = time.monotonic() + 5
        last_error: OSError | None = None
        while time.monotonic() < deadline:
            if self._process.poll() is not None:
                details = self._read_stderr()
                raise EngineError(
                    f"process {self.id} exited during startup"
                    f"{f': {details}' if details else ''}"
                )
            try:
                connection = self.network.connect(
                    self.host,
                    self.port,
                    timeout=0.2,
                    peer_id=self.id,
                )
                connection.settimeout(None)
                return connection
            except OSError as exc:
                last_error = exc
                time.sleep(0.02)
        raise EngineError(
            f"process {self.id} did not open {self.host}:{self.port}: {last_error}"
        )

    @property
    def is_alive(self) -> bool:
        return self._process.poll() is None

    def transfer(self, object_id: str, function: Function) -> None:
        try:
            serialized = function.serialize()
        except Exception as exc:
            raise EngineError(f"could not serialize object {object_id}: {exc}") from exc
        self._request(
            {
                "command": "transfer",
                "object_id": object_id,
                "function": serialized,
            }
        )
        self.object_ids.add(object_id)

    def evaluate(self, object_id: str) -> Any:
        self.evaluating_object_id = object_id
        try:
            reply = self._request({"command": "eval", "object_id": object_id})
        finally:
            self.evaluating_object_id = None
        try:
            function = deserialize_function(reply["function"])
        except Exception as exc:
            raise EngineError(
                f"process {self.id} returned invalid function state: {exc}"
            ) from exc
        return function.result

    def _request(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.is_alive:
            raise EngineError(
                f"process {self.id} is not running "
                f"(exit code {self._process.returncode})"
            )
        try:
            self.network.send(self._socket, payload, self.id)
            reply = self.network.receive(self._socket, self.id)
        except (EOFError, OSError, ProtocolError, ValueError) as exc:
            raise EngineError(f"process {self.id} stopped responding: {exc}") from exc
        if not reply.get("ok"):
            raise EngineError(
                f"process {self.id}: {reply.get('warning', 'unknown warning')}"
            )
        return reply

    def close(self) -> None:
        if hasattr(self, "_socket"):
            if self.is_alive:
                try:
                    self._request({"command": "shutdown"})
                except EngineError:
                    pass
            self.network.close(self._socket, self.id)
        self._stop_process()

    def _stop_process(self) -> None:
        if not hasattr(self, "_process"):
            return
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
        if self._process.stderr is not None:
            self._process.stderr.close()

    def _read_stderr(self) -> str:
        if self._process.stderr is None:
            return ""
        return self._process.stderr.read().strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a resolution engine process")
    parser.add_argument("--serve", action="store_true", help="run the TCP server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int)
    parser.add_argument("--id", dest="engine_id")
    arguments = parser.parse_args(argv)

    if not arguments.serve or arguments.port is None or arguments.engine_id is None:
        parser.error("--serve, --port, and --id are required")
    network = Network(arguments.engine_id)
    return serve(arguments.host, arguments.port, arguments.engine_id, network)


if __name__ == "__main__":
    raise SystemExit(main())
