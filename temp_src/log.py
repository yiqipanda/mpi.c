
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from functions import Serializable

if TYPE_CHECKING:
    from message import Message


""" Log objects help with encapsulating traces of information across processes. They implement Serializable because they are sent across the processes by Message objects. """
@dataclass(slots=True)
class Log(Serializable):

    timestamp_ns: int
    source: str
    event: str


    """Probably going to change in future, they store some of message fields only"""
    def message_event(self, message: Message) -> None:
        self.event = f"request: {message.request_type} by id: {message.request_id} received by {message.worker_id} to handle operation {message.operation}"

    def serialize(self) -> dict[str, Any]:
        return {
            "class": "Log",
            "timestamp_ns": self.timestamp_ns,
            "source": self.source,
            "event": self.event,
        }

    @classmethod
    def deserialize(cls, payload: Mapping[str, Any]) -> "Log":
        if not isinstance(payload, Mapping) or set(payload) != {
            "class", "timestamp_ns", "source", "event"
        } or payload["class"] != cls.__name__:
            raise ValueError("Invalid serialized Log")
        if (type(payload["timestamp_ns"]) is not int
                or not isinstance(payload["source"], str)
                or not isinstance(payload["event"], str)):
            raise ValueError("Invalid Log fields")
        return cls(
            timestamp_ns=payload["timestamp_ns"],
            source=payload["source"],
            event=payload["event"],
        )


    """They help with sorting logs based on time"""

    def __lt__(self, other: object) -> bool:
        return self.timestamp_ns < other.timestamp_ns

    def __le__(self, other: object) -> bool:
        return self.timestamp_ns <= other.timestamp_ns

    def __gt__(self, other: object) -> bool:
        return self.timestamp_ns > other.timestamp_ns

    def __ge__(self, other: object) -> bool:
        return self.timestamp_ns >= other.timestamp_ns
