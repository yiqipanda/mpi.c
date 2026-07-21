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
    captured_list: list[int | None] = field(default_factory=list)
    subtasks_done: bool = False
    task_done: bool = False
    completion_status: str = "pending"
    result: Any = None
    return_value: int | None = None
    subprocess_value: int | None = None
    subprocess_done: bool = False
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
    orchestration_task: Task | None = None

    def __post_init__(self) -> None:
        self._sync_capture_slots()
        self._refresh_subtasks_done()

    def _sync_capture_slots(self) -> None:
        expected = len(self.task_children)
        if expected == 0:
            if self.captured_list and len(self.captured_list) > 0:
                self.captured_list = []
            return

        if not self.captured_list:
            self.captured_list = [None] * expected
            return

        if len(self.captured_list) < expected:
            self.captured_list.extend([None] * (expected - len(self.captured_list)))
        elif len(self.captured_list) > expected:
            self.captured_list = self.captured_list[:expected]

    def _refresh_subtasks_done(self) -> None:
        self.subtasks_done = len(self.task_children) == 0 or all(
            value is not None for value in self.captured_list
        )

    def _parse_stdout_value(self, stdout: str) -> int:
        text = stdout.strip()
        if not text:
            return 0
        return int(text.splitlines()[-1])

    # Mark the task as running and store the start timestamp.
    def mark_started(self) -> None:
        self.completion_status = "running"
        self.started_at = datetime.now(timezone.utc).isoformat()

    # Store subprocess output without finalizing the subtree result.
    def mark_subprocess_finished(
        self,
        return_code: int,
        stdout: str = "",
        stderr: str = "",
    ) -> None:
        self.subprocess_done = True
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        if return_code == 0:
            self.subprocess_value = self._parse_stdout_value(stdout)
        else:
            self.subprocess_value = None
            self.result = None
            self.task_done = True
            self.completion_status = "failed"
            self.finished_at = datetime.now(timezone.utc).isoformat()

    # Backward-compatible wrapper for older callers.
    def mark_finished(
        self,
        return_code: int,
        stdout: str = "",
        stderr: str = "",
        result: Any = None,
    ) -> None:
        self.mark_subprocess_finished(return_code, stdout, stderr)
        if result is not None:
            self.result = result

    # Record a child task value in task_children order.
    def record_child_result(self, child_index: int, value: int) -> None:
        self._sync_capture_slots()
        if child_index < 0 or child_index >= len(self.captured_list):
            raise IndexError("child_index out of range for captured_list")
        self.captured_list[child_index] = int(value)
        self._refresh_subtasks_done()

    # Mark the task as aborted and terminal.
    def abort(self, return_code: int = 1) -> None:
        self.subprocess_done = True
        self.task_done = True
        self.completion_status = "aborted"
        self.return_code = return_code
        self.finished_at = datetime.now(timezone.utc).isoformat()
        self.return_value = None
        self.result = None
        self.clear_orchestration_task()

    # Clear orchestration_task recursively for this task tree.
    def clear_orchestration_task(self) -> None:
        self.orchestration_task = None
        for child in self.task_children:
            child.clear_orchestration_task()

    # Build a new attempt from task configuration without carrying runtime state.
    def _new_attempt(self) -> "Task":
        return Task(
            program_assigned=self.program_assigned,
            args=list(self.args),
            name=self.name,
            task_children=[child._new_attempt() for child in self.task_children],
            dependencies=list(self.dependencies),
            working_dir=self.working_dir,
            environment=dict(self.environment),
            role=self.role,
            sequence_index=self.sequence_index,
            fragment_name=self.fragment_name,
        )

    # Copy only trusted completed execution state into an independent task tree.
    def _copy_completed_state_from(self, source: "Task") -> None:
        for child_index, source_child in enumerate(source.task_children):
            if child_index >= len(self.task_children):
                break
                
            target_child = self.task_children[child_index]
            target_child._copy_completed_state_from(source_child)
            if source_child.task_done and source_child.completion_status == "completed":
                child_value = source_child.return_value
                if child_value is None:
                    child_value = source_child.subprocess_value
                if child_value is not None:
                    self.captured_list[child_index] = int(child_value)

        if source.task_done and source.completion_status == "completed":
            self.captured_list = list(source.captured_list)
            self.subtasks_done = source.subtasks_done
            self.task_done = True
            self.completion_status = "completed"
            self.result = source.result
            self.return_value = source.return_value
            self.subprocess_value = source.subprocess_value
            self.subprocess_done = source.subprocess_done
            self.dependencies = list(source.dependencies)
            self.working_dir = source.working_dir
            self.environment = dict(source.environment)
            self.started_at = source.started_at
            self.finished_at = source.finished_at
            self.return_code = source.return_code
            self.stdout = source.stdout
            self.stderr = source.stderr
        else:
            self._refresh_subtasks_done()
        self.orchestration_task = None

    # Abort this task and return a replacement tree when it has children.
    def abort_task(self) -> "Task | None":
        if not self.task_children:
            self.abort()
            return None

        replacement = self._new_attempt()
        self.abort()
        return replacement

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
        children = list(self.task_children) if task_children is None else task_children
        return replace(
            self,
            task_children=children,
            captured_list=[None] * len(children),
            role=self.role if role is None else role,
            sequence_index=self.sequence_index if sequence_index is None else sequence_index,
            completion_status=self.completion_status if completion_status is None else completion_status,
            fragment_name=self.fragment_name if fragment_name is None else fragment_name,
            result=None,
            return_value=None,
            subprocess_value=None,
            started_at=None,
            finished_at=None,
            return_code=None,
            stdout="",
            stderr="",
            subtasks_done=False,
            task_done=False,
            subprocess_done=False,
        )

    # Convert the task to a JSON-friendly dictionary.
    def to_dict(self) -> dict[str, Any]:
        return {
            "program_assigned": self.program_assigned,
            "args": list(self.args),
            "name": self.name,
            "task_children": [child.to_dict() for child in self.task_children],
            "captured_list": list(self.captured_list),
            "subtasks_done": self.subtasks_done,
            "task_done": self.task_done,
            "completion_status": self.completion_status,
            "result": self.result,
            "return_value": self.return_value,
            "subprocess_value": self.subprocess_value,
            "subprocess_done": self.subprocess_done,
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
            captured_list=list(data.get("captured_list", [])),
            subtasks_done=bool(data.get("subtasks_done", False)),
            task_done=bool(data.get("task_done", False)),
            completion_status=data.get("completion_status", data.get("status", "pending")),
            result=data.get("result"),
            return_value=data.get("return_value"),
            subprocess_value=data.get("subprocess_value"),
            subprocess_done=bool(data.get("subprocess_done", False)),
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
