from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import json


@dataclass
class Trace:
    enabled: bool = True
    path: Path | None = None
    entries: list[dict[str, Any]] = field(default_factory=list)

    # Record a generic trace event with optional structured details.
    def record(self, event: str, message: str = "", **details: Any) -> dict[str, Any]:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "message": message,
            "details": details,
        }
        if self.enabled:
            self.entries.append(entry)
        return entry

    # Record a task-related trace event.
    def task(self, message: str, **details: Any) -> dict[str, Any]:
        return self.record("task", message, **details)

    # Record a parent-related trace event.
    def parent(self, message: str, **details: Any) -> dict[str, Any]:
        return self.record("parent", message, **details)

    # Record a child-related trace event.
    def child(self, message: str, **details: Any) -> dict[str, Any]:
        return self.record("child", message, **details)

    # Record a program-level trace event.
    def program(self, message: str, **details: Any) -> dict[str, Any]:
        return self.record("program", message, **details)

    # Convert the trace log into a serializable dictionary.
    def to_dict(self) -> dict[str, Any]:
        return {"entries": list(self.entries)}

    # Write the trace log to disk as JSON.
    def dump(self, path: str | Path | None = None) -> Path | None:
        target = Path(path) if path is not None else self.path
        if target is None:
            return None
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return target

    # Remove every recorded entry from the trace log.
    def clear(self) -> None:
        self.entries.clear()

