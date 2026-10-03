"""Function implementations available to the demo resolution engine."""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any
import time

@dataclass
class Function(ABC):
    """Stateful contract implemented by every distributable function."""

    params: Any = None
    result: Any = None

    @abstractmethod
    def partition(self) -> tuple["Function", "Function"]:
        """Partition this function exactly once into two explicit functions."""

    @abstractmethod
    def eval(self) -> bool:
        """Store the evaluation in ``result`` and report whether it succeeded."""

    @abstractmethod
    def orchestrate(self, partitions: Sequence["Function"]) -> bool:
        """Combine an ordered collection of evaluated partitions into ``result``."""

    @abstractmethod
    def serialize(self) -> dict[str, Any]:
        """Return a transport-safe representation of this function's state."""

    @classmethod
    @abstractmethod
    def deserialize(cls, payload: Mapping[str, Any]) -> "Function":
        """Reconstruct a function from its transport representation."""


@dataclass
class Sum(Function):
    """Add all numbers in a list."""

    def partition(self) -> tuple["Sum", "Sum"]:
        if not isinstance(self.params, list):
            raise TypeError("Sum parameters must be a list")

        midpoint = len(self.params) // 2
        return (
            Sum(params=self.params[:midpoint]),
            Sum(params=self.params[midpoint:]),
        )

    def eval(self) -> bool:
        if self.params is None:
            return False

        z = 0
        for e in self.params:
            z += e
            time.sleep(3)
        self.result = z
        return True

    def orchestrate(self, partitions: Sequence[Function]) -> bool:
        if self.result is not None:
            return False
        if len(partitions) != 2:
            raise ValueError("Sum.orchestrate() requires exactly two partitions")
        if any(partition.result is None for partition in partitions):
            return False

        self.result = sum(partition.result for partition in partitions)
        return True

    def serialize(self) -> dict[str, Any]:
        payload = {
            "class": type(self).__name__,
            "params": self.params,
            "result": self.result,
        }
        try:
            json.dumps(payload)
        except (TypeError, ValueError) as exc:
            raise TypeError(
                "Sum parameters and result must be JSON-serializable"
            ) from exc
        return payload

    @classmethod
    def deserialize(cls, payload: Mapping[str, Any]) -> "Sum":
        if not isinstance(payload, Mapping):
            raise TypeError("serialized Sum must be a mapping")
        if payload.get("class") != cls.__name__:
            raise ValueError("serialized payload does not describe a Sum")
        if "params" not in payload:
            raise ValueError("serialized Sum is missing params")

        return cls(params=payload["params"], result=payload.get("result"))


FUNCTION_CLASSES: dict[str, type[Function]] = {
    Sum.__name__: Sum,
}


def deserialize_function(payload: Mapping[str, Any]) -> Function:
    """Resolve a controlled class name and deserialize its state."""

    if not isinstance(payload, Mapping):
        raise TypeError("serialized function must be a mapping")
    class_name = payload.get("class")
    if not isinstance(class_name, str) or class_name not in FUNCTION_CLASSES:
        raise ValueError(f"unknown serialized function class: {class_name!r}")
    return FUNCTION_CLASSES[class_name].deserialize(payload)
