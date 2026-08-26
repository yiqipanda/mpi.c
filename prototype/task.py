from __future__ import annotations

import shlex
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast

from prototype.status import TaskState

TASKS_DIR = Path(__file__).resolve().parent / "tasks"


class ResultPolicy(Protocol):
    def parse_subprocess(self, stdout: str) -> int: ...

    def child_value(self, child: Task) -> int | None: ...

    def combine(self, own: int | None, children: list[int | None]) -> int: ...


class ResultPolicyError(ValueError):
    pass


@dataclass(frozen=True)
class IntegerSumResultPolicy:
    """Interpret the final stdout line as an integer and sum child results."""

    name: str = "integer-sum"

    def parse_subprocess(self, stdout: str) -> int:
        text = stdout.strip()
        if not text:
            return 0
        try:
            return int(text.splitlines()[-1])
        except ValueError as exc:
            raise ResultPolicyError(
                "integer-sum requires an integer on the final stdout line"
            ) from exc

    def child_value(self, child: Task) -> int | None:
        if child.return_value is not None:
            return int(child.return_value)
        if child.result is not None:
            try:
                return int(child.result)
            except (TypeError, ValueError):
                return None
        if child.subprocess_value is not None:
            return int(child.subprocess_value)
        return None

    def combine(self, own: int | None, children: list[int | None]) -> int:
        return (0 if own is None else int(own)) + sum(
            int(value) for value in children if value is not None
        )


class TaskInvariantError(RuntimeError):
    pass


@dataclass
class Task:
    FIELD_CLASSIFICATION: ClassVar[dict[str, frozenset[str]]] = {
        "definition": frozenset(
            {
                "program_assigned",
                "args",
                "name",
                "task_children",
                "dependencies",
                "working_dir",
                "environment",
                "role",
                "sequence_index",
                "fragment_name",
                "result_policy",
            }
        ),
        "attempt": frozenset(
            {
                "completion_status",
                "result",
                "return_value",
                "subprocess_value",
                "subprocess_done",
                "started_at",
                "finished_at",
                "return_code",
                "stdout",
                "stderr",
            }
        ),
        "derived": frozenset({"captured_list", "subtasks_done", "task_done"}),
        "relationship": frozenset({"orchestration_task"}),
    }

    program_assigned: str
    args: list[str] = field(default_factory=list[str])
    name: str = "task"
    task_children: list[Task] = field(
        default_factory=cast(Callable[[], list["Task"]], list)
    )
    captured_list: list[int | None] = field(default_factory=list[int | None])
    subtasks_done: bool = False
    task_done: bool = False
    completion_status: TaskState = "pending"
    result: Any = None
    return_value: int | None = None
    subprocess_value: int | None = None
    subprocess_done: bool = False
    dependencies: list[str] = field(default_factory=list[str])
    working_dir: str | None = None
    environment: dict[str, str] = field(default_factory=dict[str, str])
    started_at: str | None = None
    finished_at: str | None = None
    return_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    role: str = "worker"
    sequence_index: int = 0
    fragment_name: str | None = None
    orchestration_task: Task | None = None
    result_policy: ResultPolicy = field(
        default_factory=IntegerSumResultPolicy, repr=False, compare=False
    )

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

    def prepare_for_scheduling(self) -> None:
        self._sync_capture_slots()
        if self.completion_status in {"pending", "blocked"}:
            self.mark_pending()

    def replace_child(self, child_index: int, replacement: Task) -> None:
        if child_index < 0 or child_index >= len(self.task_children):
            raise IndexError("child_index out of range for task_children")
        self.task_children[child_index] = replacement
        self._sync_capture_slots()
        self.captured_list[child_index] = None
        self._refresh_subtasks_done()

    # Mark the task as running and store the start timestamp.
    def mark_started(self) -> None:
        if self.task_done:
            raise TaskInvariantError("a terminal task cannot be started")
        self.completion_status = "running"
        self.started_at = datetime.now(timezone.utc).isoformat()

    def mark_blocked(self) -> None:
        self.completion_status = "blocked"

    def mark_pending(self) -> None:
        self.completion_status = "pending"

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
            try:
                self.subprocess_value = self.result_policy.parse_subprocess(stdout)
            except ResultPolicyError as exc:
                self.subprocess_value = None
                self.result = None
                self.task_done = True
                self.completion_status = "failed"
                self.return_code = 1
                self.stderr = f"{stderr}\n{exc}".strip()
                self.finished_at = datetime.now(timezone.utc).isoformat()
                self.validate_invariants()
        else:
            self.subprocess_value = None
            self.result = None
            self.task_done = True
            self.completion_status = "failed"
            self.finished_at = datetime.now(timezone.utc).isoformat()
            self.validate_invariants()

    # Record a child task value in task_children order.
    def record_child_result(self, child_index: int, value: int) -> None:
        self._sync_capture_slots()
        if child_index < 0 or child_index >= len(self.captured_list):
            raise IndexError("child_index out of range for captured_list")
        self.captured_list[child_index] = int(value)
        self._refresh_subtasks_done()

    def mark_completed(self, value: int, finished_at: str | None = None) -> None:
        if not self.subprocess_done or not self.subtasks_done:
            raise TaskInvariantError(
                "completion requires finished subprocess and child results"
            )
        self.return_value = int(value)
        self.result = int(value)
        self.task_done = True
        self.completion_status = "completed"
        self.finished_at = finished_at or datetime.now(timezone.utc).isoformat()
        self.validate_invariants()

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
        self.validate_invariants()

    def validate_invariants(self) -> None:
        if self.completion_status in {
            "completed",
            "failed",
            "aborted",
        } and (not self.task_done or self.finished_at is None):
            raise TaskInvariantError("terminal tasks require task_done and finished_at")
        if self.completion_status == "completed":
            if not self.subprocess_done or not self.subtasks_done:
                raise TaskInvariantError(
                    "completed tasks require subprocess and child completion"
                )
            if self.return_value is None or self.result is None:
                raise TaskInvariantError("completed tasks require a result value")
        if self.completion_status in {"failed", "aborted"} and not self.subprocess_done:
            raise TaskInvariantError(
                "failed and aborted tasks require subprocess completion"
            )

    # Clear orchestration_task recursively for this task tree.
    def clear_orchestration_task(self) -> None:
        self.orchestration_task = None
        for child in self.task_children:
            child.clear_orchestration_task()

    # Build a new attempt from task configuration without carrying runtime state.
    def new_attempt(self) -> Task:
        return Task(
            program_assigned=self.program_assigned,
            args=list(self.args),
            name=self.name,
            task_children=[child.new_attempt() for child in self.task_children],
            dependencies=list(self.dependencies),
            working_dir=self.working_dir,
            environment=dict(self.environment),
            role=self.role,
            sequence_index=self.sequence_index,
            fragment_name=self.fragment_name,
            result_policy=self.result_policy,
        )

    # Copy only trusted completed execution state into an independent task tree.
    def copy_completed_state_from(self, source: Task) -> None:
        for child_index, source_child in enumerate(source.task_children):
            if child_index >= len(self.task_children):
                break

            target_child = self.task_children[child_index]
            target_child.copy_completed_state_from(source_child)
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

    def clone_for_recovery(self, trusted_children: dict[int, Task]) -> Task:
        replacement = self.new_attempt()
        for child_index, source_child in trusted_children.items():
            if child_index < 0 or child_index >= len(replacement.task_children):
                raise IndexError("trusted child index is outside the task tree")
            replacement_child = source_child.new_attempt()
            replacement_child.copy_completed_state_from(source_child)
            replacement.task_children[child_index] = replacement_child
            if (
                replacement_child.task_done
                and replacement_child.completion_status == "completed"
            ):
                child_value = self.result_policy.child_value(replacement_child)
                if child_value is not None:
                    replacement.captured_list[child_index] = child_value
        replacement._refresh_subtasks_done()
        return replacement

    # Abort this task and return a replacement tree when it has children.
    def abort_task(self) -> Task | None:
        if not self.task_children:
            self.abort()
            return None

        replacement = self.new_attempt()
        self.abort()
        return replacement

    # Build the subprocess argument vector for this program task.
    def spawn_argv(self) -> list[str]:
        argv = (
            [self.program_assigned, *self.args]
            if self.args
            else shlex.split(self.program_assigned)
        )
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

    # Create a fresh attempt with selected definition metadata changed.
    def with_definition_updates(
        self,
        *,
        role: str | None = None,
        sequence_index: int | None = None,
        fragment_name: str | None = None,
        task_children: list[Task] | None = None,
    ) -> Task:
        children = (
            [child.new_attempt() for child in self.task_children]
            if task_children is None
            else [child.new_attempt() for child in task_children]
        )
        return Task(
            program_assigned=self.program_assigned,
            args=list(self.args),
            name=self.name,
            task_children=children,
            dependencies=list(self.dependencies),
            working_dir=self.working_dir,
            environment=dict(self.environment),
            role=self.role if role is None else role,
            sequence_index=(
                self.sequence_index if sequence_index is None else sequence_index
            ),
            fragment_name=(
                self.fragment_name if fragment_name is None else fragment_name
            ),
            result_policy=self.result_policy,
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
    def from_dict(cls, data: dict[str, Any]) -> Task:
        task = cls(
            program_assigned=data.get("program_assigned", data.get("program", "")),
            args=list(data.get("args", [])),
            name=data.get("name", "task"),
            task_children=[
                cls.from_dict(child) for child in data.get("task_children", [])
            ],
            captured_list=list(data.get("captured_list", [])),
            subtasks_done=bool(data.get("subtasks_done", False)),
            task_done=bool(data.get("task_done", False)),
            completion_status=cast(
                TaskState,
                data.get("completion_status", data.get("status", "pending")),
            ),
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
        task.validate_invariants()
        return task
