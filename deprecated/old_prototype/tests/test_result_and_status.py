from __future__ import annotations

import pytest

from prototype.main import Main
from prototype.status import (
    PROGRAM_STATES,
    TASK_STATES,
    new_main_status,
    new_worker_status,
)
from prototype.task import IntegerSumResultPolicy, ResultPolicyError, Task
from prototype.trace import Trace
from prototype.worker import Worker


def test_integer_sum_parsing_and_combination() -> None:
    policy = IntegerSumResultPolicy()
    assert policy.parse_subprocess("") == 0
    assert policy.parse_subprocess("diagnostic\n-4\n") == -4
    assert policy.combine(None, []) == 0
    assert policy.combine(2, [3, None, 5]) == 10


def test_integer_sum_rejects_non_integer_output() -> None:
    with pytest.raises(ResultPolicyError):
        IntegerSumResultPolicy().parse_subprocess("not-an-integer\n")


def test_integer_sum_child_value_has_explicit_precedence() -> None:
    policy = IntegerSumResultPolicy()
    task = Task(program_assigned="program.py")
    task.subprocess_value = 1
    task.result = "2"
    task.return_value = 3
    assert policy.child_value(task) == 3
    task.return_value = None
    assert policy.child_value(task) == 2
    task.result = object()
    assert policy.child_value(task) is None


def test_status_factories_have_exact_compatible_shapes_and_no_aliasing() -> None:
    worker = new_worker_status()
    main = new_main_status()
    assert set(worker) == {
        "program_state",
        "parent_state",
        "task_state",
        "child_states",
        "parent_pid",
        "child_pids",
        "return_code",
        "message",
        "started_at",
        "finished_at",
    }
    assert set(main) == set(worker) | {"return_value"}
    first = new_worker_status()
    second = new_worker_status()
    first["child_states"][1] = "running"
    first["child_pids"].append(10)
    assert second["child_states"] == {}
    assert second["child_pids"] == []


def test_status_vocabularies_cover_emitted_states() -> None:
    assert PROGRAM_STATES == {
        "idle",
        "running",
        "finished",
        "aborted",
        "unavailable",
    }
    assert TASK_STATES == {
        "idle",
        "pending",
        "blocked",
        "running",
        "completed",
        "failed",
        "aborted",
        "unavailable",
    }


def test_successful_output_protocol_shapes_are_preserved() -> None:
    trace = Trace(path=None)
    main = Main(
        worker_count=1,
        task=Task(program_assigned="-c", args=[], name="unused"),
        trace=trace,
    )
    task_keys = set(main.task.to_dict())
    worker_keys = set(Worker(trace=trace).snapshot())

    assert task_keys == {
        "program_assigned",
        "args",
        "name",
        "task_children",
        "captured_list",
        "subtasks_done",
        "task_done",
        "completion_status",
        "result",
        "return_value",
        "subprocess_value",
        "subprocess_done",
        "dependencies",
        "working_dir",
        "environment",
        "started_at",
        "finished_at",
        "return_code",
        "stdout",
        "stderr",
        "role",
        "sequence_index",
        "fragment_name",
    }
    assert worker_keys == {
        "role",
        "index",
        "task_index",
        "healthy",
        "assignment_generation",
        "pid",
        "state",
        "process_state",
        "task",
        "last_report",
        "children",
    }
