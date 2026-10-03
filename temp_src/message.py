import json
from dataclasses import asdict, dataclass, field


@dataclass
class Message:
    request_type: str = None
    operation: str = None
    parameters: list[int] = field(default_factory=list)
    worker_id: int = -1
    request_id: int = -1
    def serialize(self) -> str:
        return json.dumps(asdict(self))

    def to_eval_request(self: "Message") -> "Message | None":
        if self.request_type != "createObject":
            return None
        return Message(
            request_type="eval",
            operation="return",
            worker_id=self.worker_id,
            request_id=self.request_id,
        )

    @classmethod
    def deserialize(cls, data: str) -> "Message":
        return cls(**json.loads(data))
