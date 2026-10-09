"""Sorted system log storage, independent of message transport."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from functions import Serializable
from log import Log

@dataclass
class LogBuffer(Serializable):
    """Keep system logs sorted until a returned batch is acknowledged."""

    logs: list[Log] = field(default_factory=list)
    pending_logs: list[Log] | None = None

    def serialize(self) -> dict[str, Any]:
        return {
            "class": "LogBuffer",
            "logs": [log.serialize() for log in self.logs],
            "pending_logs": (
                [log.serialize() for log in self.pending_logs]
                if self.pending_logs is not None else None
            ),
        }

    @classmethod
    def deserialize(cls, payload: Mapping[str, Any]) -> "LogBuffer":
        if not isinstance(payload, Mapping) or set(payload) != {
            "class", "logs", "pending_logs"
        } or payload["class"] != cls.__name__:
            raise ValueError("Invalid serialized LogBuffer")
        logs = payload["logs"]
        pending_logs = payload["pending_logs"]
        if not isinstance(logs, list) or (
            pending_logs is not None and not isinstance(pending_logs, list)
        ):
            raise ValueError("Invalid LogBuffer entries")
        return cls(
            logs=[Log.deserialize(log) for log in logs],
            pending_logs=(
                [Log.deserialize(log) for log in pending_logs]
                if pending_logs is not None else None
            ),
        )

    def append(self, log: Log) -> None:
        self.logs.insert(bisect_right(self.logs, log), log)

    @staticmethod
    def merge_sorted(left: list[Log], right: list[Log]) -> list[Log]:
        """Merge sorted inputs without changing either list."""

        merged: list[Log] = []
        left_index = right_index = 0
        while left_index < len(left) and right_index < len(right):
            if left[left_index] <= right[right_index]:
                merged.append(left[left_index])
                left_index += 1
            else:
                merged.append(right[right_index])
                right_index += 1
        merged.extend(left[left_index:])
        merged.extend(right[right_index:])
        return merged

    def merge(self, logs: list[Log]) -> None:
        """Merge an already sorted list into the current logs in linear time."""

        self.logs = self.merge_sorted(self.logs, logs)

    def snapshot(self) -> list[Log]:
        """Return a stable batch while retaining it until flush is called."""

        if self.pending_logs is None: #Used as a flag
            self.pending_logs, self.logs = self.logs, []
        return self.pending_logs.copy()

    def flush(self) -> None:
        """Discard the returned batch; leave logs added afterward untouched."""

        self.pending_logs = None

    def export_syslog(
        self,
        hostname: str = "-",
        facility: int = 1,
        severity: int = 6,
        app_name: str = "mpi.c",
    ) -> str:
        """Format all unflushed logs as RFC 5424 lines without sending them.

        A Log containing a Message adds its captured JSON after the event text.

        Example input::

            from log import Log

            buffer = LogBuffer()
            buffer.append(Log(1_000_000_000, "worker 1", 0, "started"))
            buffer.export_syslog(hostname="node1")

        Example output (shown with ``repr`` to expose the UTF-8 marker)::

            '<14>1 1970-01-01T00:00:01.000000Z node1 mpi.c - - - \\ufeff[worker 1] started'
        """

        logs = self.merge_sorted(self.pending_logs or [], self.logs)
        priority = facility * 8 + severity
        lines: list[str] = []
        for log in logs:
            seconds, nanoseconds = divmod(log.timestamp_ns, 1_000_000_000)
            date = datetime.fromtimestamp(seconds, timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            timestamp = f"{date}.{nanoseconds // 1_000:06d}Z"
            event = f"[{log.source}] {log.event}"
            event = event.replace("\r", "\\r").replace("\n", "\\n")
            lines.append(
                f"<{priority}>1 {timestamp} {hostname} {app_name} - - - \ufeff{event}"
            )
        return "\n".join(lines)
