from __future__ import annotations

from typing import Literal, TypedDict

ProgramState = Literal["idle", "running", "finished", "aborted", "unavailable"]
TaskState = Literal[
    "idle",
    "pending",
    "blocked",
    "running",
    "completed",
    "failed",
    "aborted",
    "unavailable",
]

PROGRAM_STATES: frozenset[str] = frozenset(
    {"idle", "running", "finished", "aborted", "unavailable"}
)
TASK_STATES: frozenset[str] = frozenset(
    {
        "idle",
        "pending",
        "blocked",
        "running",
        "completed",
        "failed",
        "aborted",
        "unavailable",
    }
)


class WorkerStatus(TypedDict):
    program_state: ProgramState
    parent_state: TaskState
    task_state: TaskState
    child_states: dict[int, TaskState]
    parent_pid: int | None
    child_pids: list[int]
    return_code: int | None
    message: str
    started_at: str | None
    finished_at: str | None


class MainStatus(WorkerStatus):
    return_value: int | None


def new_worker_status() -> WorkerStatus:
    return {
        "program_state": "idle",
        "parent_state": "idle",
        "task_state": "idle",
        "child_states": {},
        "parent_pid": None,
        "child_pids": [],
        "return_code": None,
        "message": "",
        "started_at": None,
        "finished_at": None,
    }


def new_main_status() -> MainStatus:
    return {**new_worker_status(), "return_value": None}
