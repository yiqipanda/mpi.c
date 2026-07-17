from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import shlex
import sys


TASKS_DIR = Path(__file__).resolve().parent / "tasks"


@dataclass
class Task:
    program_assigned: str
    args: list[str] = field(default_factory=list)
    name: str = "task"
    task_children: list["Task"] = field(default_factory=list)
    completion_status: str = "pending"
    result: Any = None
    dependencies: list[str] = field(default_factory=list)
    working_dir: str | None = None
    environment: dict[str, str] = field(default_factory=dict)
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    role: str = "worker"
    sequence_index: int = 0
    fragment_name: str | None = None

    # Mark the task as running and store the start timestamp.
    def mark_started(self) -> None:
        self.completion_status = "running"
        self.started_at = datetime.now(timezone.utc).isoformat()

    # Mark the task as completed and store the final execution details.
    def mark_finished(
        self,
        return_code: int,
        stdout: str = "",
        stderr: str = "",
        result: Any = None,
    ) -> None:
        self.completion_status = "completed" if return_code == 0 else "failed"
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.result = result

    # Return the effective program path or file assigned to this task.
    def assigned_program(self) -> str:
        return self.program_assigned

    # Build the subprocess argument vector for this program task.
    def spawn_argv(self) -> list[str]:
        argv = [self.program_assigned, *self.args] if self.args else shlex.split(self.program_assigned)
        if not argv:
            return []

        program = Path(argv[0])
        if not program.is_absolute():
            tasks_candidate = TASKS_DIR / program
            local_candidate = Path(__file__).resolve().parent / program
            if tasks_candidate.exists():
                program = tasks_candidate
            elif local_candidate.exists():
                program = local_candidate

        argv[0] = str(program)
        if program.suffix == ".py":
            return [sys.executable, *argv]
        return argv

    # Create a shallow copy of the task with updated execution metadata.
    def with_updates(
        self,
        *,
        role: str | None = None,
        sequence_index: int | None = None,
        completion_status: str | None = None,
        fragment_name: str | None = None,
        task_children: list["Task"] | None = None,
    ) -> "Task":
        return replace(
            self,
            task_children=list(self.task_children) if task_children is None else task_children,
            role=self.role if role is None else role,
            sequence_index=self.sequence_index if sequence_index is None else sequence_index,
            completion_status=self.completion_status if completion_status is None else completion_status,
            fragment_name=self.fragment_name if fragment_name is None else fragment_name,
            result=None,
            started_at=None,
            finished_at=None,
            return_code=None,
            stdout="",
            stderr="",
        )

    # Convert the task to a JSON-friendly dictionary.
    def to_dict(self) -> dict[str, Any]:
        return {
            "program_assigned": self.program_assigned,
            "args": list(self.args),
            "name": self.name,
            "task_children": [child.to_dict() for child in self.task_children],
            "completion_status": self.completion_status,
            "result": self.result,
            "dependencies": list(self.dependencies),
            "working_dir": self.working_dir,
            "environment": dict(self.environment),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "return_code": self.return_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "role": self.role,
            "sequence_index": self.sequence_index,
            "fragment_name": self.fragment_name,
        }

    # Reconstruct a task from a serialized dictionary.
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        return cls(
            program_assigned=data.get("program_assigned", data.get("program", "")),
            args=list(data.get("args", [])),
            name=data.get("name", "task"),
            task_children=[cls.from_dict(child) for child in data.get("task_children", [])],
            completion_status=data.get("completion_status", data.get("status", "pending")),
            result=data.get("result"),
            dependencies=list(data.get("dependencies", [])),
            working_dir=data.get("working_dir"),
            environment=dict(data.get("environment", {})),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            return_code=data.get("return_code"),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            role=data.get("role", "worker"),
            sequence_index=int(data.get("sequence_index", 0)),
            fragment_name=data.get("fragment_name"),
        )

    # Compatibility accessors for older code paths.
    @property
    def program(self) -> str:
        return self.program_assigned

    @program.setter
    def program(self, value: str) -> None:
        self.program_assigned = value

    @property
    def status(self) -> str:
        return self.completion_status

    @status.setter
    def status(self, value: str) -> None:
        self.completion_status = value
