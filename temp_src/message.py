import json
from dataclasses import dataclass, field
from typing import Any

from functions import SERIALIZABLE_CLASSES, Serializable


@dataclass
class Message:
    request_type: str | None = None
    operation: str | None = None
    parameters: list[Any] = field(default_factory=list)
    worker_id: int = -1
    request_id: int = -1

    def serialize(self) -> str:
        return json.dumps(
            {
                "request_type": self.request_type,
                "operation": self.operation,
                "parameters": [self._pack_parameter(value) for value in self.parameters],
                "worker_id": self.worker_id,
                "request_id": self.request_id,
            }
        )

    @staticmethod
    def _pack_parameter(value: Any) -> dict[str, Any]:
        kind = type(value)
        if kind in {type(None), bool, int, float, str}:
            return {"type": kind.__name__, "value": value}
        if kind is list:
            return {"type": "list", "value": [Message._pack_parameter(item) for item in value]}
        if kind is dict:
            if any(type(key) is not str for key in value):
                raise TypeError("Dictionary parameter keys must be strings")
            return {
                "type": "dict",
                "value": {key: Message._pack_parameter(item) for key, item in value.items()},
            }
        if isinstance(value, Serializable):
            class_name = kind.__name__
            if class_name in {"NoneType", "bool", "int", "float", "str", "list", "dict"}:
                raise TypeError(f"Reserved serializable class name: {class_name}")
            if SERIALIZABLE_CLASSES.get(class_name) is not kind:
                raise TypeError(f"Unregistered serializable class: {class_name}")
            state = value.serialize()
            if not isinstance(state, dict) or state.get("class") != class_name:
                raise ValueError("Serialized state must identify its registered class")
            return {"type": class_name, "value": state}
        raise TypeError(f"Cannot serialize {kind.__name__}")

    @staticmethod
    def _unpack_parameter(entry: Any) -> Any:
        if not isinstance(entry, dict) or set(entry) != {"type", "value"}:
            raise ValueError("Each parameter must have type and value fields")
        kind, value = entry["type"], entry["value"]
        if not isinstance(kind, str):
            raise ValueError("Parameter type must be a string")
        primitive_types = {"NoneType": type(None), "bool": bool, "int": int, "float": float, "str": str}
        if kind in primitive_types:
            if type(value) is not primitive_types[kind]:
                raise ValueError(f"Invalid value for parameter type {kind!r}")
            return value
        if kind == "list" and isinstance(value, list):
            return [Message._unpack_parameter(item) for item in value]
        if kind == "dict" and isinstance(value, dict):
            return {key: Message._unpack_parameter(item) for key, item in value.items()}
        serializable_class = SERIALIZABLE_CLASSES.get(kind)
        if serializable_class is None or not isinstance(value, dict) or value.get("class") != kind:
            raise ValueError(f"Unknown or invalid parameter type: {kind!r}")
        return serializable_class.deserialize(value)

    def to_eval_request(self: "Message") -> "Message | None":
        if self.request_type not in {"createObject", "createFunction"}:
            return None
        return Message(
            request_type="eval",
            operation="return",
            worker_id=self.worker_id,
            request_id=self.request_id,
        )

    @classmethod
    def deserialize(cls, data: str) -> "Message":
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise TypeError("Serialized message must be a JSON object")
        if payload.get("operation") is not None and not isinstance(payload["operation"], str):
            raise TypeError("Serialized message operation must be a string")
        parameters = payload.get("parameters")
        if not isinstance(parameters, list):
            raise TypeError("Serialized message parameters must be a list")
        payload["parameters"] = [cls._unpack_parameter(entry) for entry in parameters]
        return cls(**payload)
