"""Function implementations available to the demo resolution engine."""

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from typing import Any
import time



class Serializable(ABC):
    """ Separate abstract class to solidify serializing of functions """
    

    @abstractmethod
    def serialize(self) -> dict[str, Any]:
        pass

    @classmethod
    @abstractmethod
    def deserialize(cls, payload: Mapping[str, Any]) -> "Serializable":
        pass


@dataclass
class Function(Serializable):
    """ Function Objects are primarily partitionable entities to distribute workload """
    params: Any = None
    result: Any = None

    @abstractmethod
    def partition(self) -> tuple:
        """Partition this function to several entities"""

    @abstractmethod
    def eval(self) -> bool:
        """Evaluation result stored in self.result, completion is returned as boolean value."""

    @abstractmethod
    def orchestrate(self, partitions: Sequence["Function"]) -> bool:
        """Combine instances of evaluated entities into self.result, compeletion is returned as boolean value."""

@dataclass
class Sum(Function):
    """Best instance of function class because easy to partition."""

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
        if z>4:
            time.sleep(4)
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





"""To be deleted later, no longer necessary """
def deserialize_function(payload: Mapping[str, Any]) -> Function:
    from registered_types import FUNCTION_CLASSES

    if not isinstance(payload, Mapping):
        raise TypeError("serialized function must be a mapping")
    class_name = payload.get("class")
    if not isinstance(class_name, str) or class_name not in FUNCTION_CLASSES:
        raise ValueError(f"unknown serialized function class: {class_name!r}")
    return FUNCTION_CLASSES[class_name].deserialize(payload)
