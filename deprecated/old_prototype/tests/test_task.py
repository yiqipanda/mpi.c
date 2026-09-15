from __future__ import annotations

import ast
from dataclasses import fields
from pathlib import Path

import pytest

from prototype.task import (
    IntegerSumResultPolicy,
    Task,
    TaskInvariantError,
)


def completed_leaf(value: int = 4) -> Task:
    task = Task(program_assigned="program.py", name="leaf")
    task.mark_started()
    task.mark_subprocess_finished(0, f"{value}\n")
    task.mark_completed(value)
    return task


def test_lifecycle_transitions_preserve_terminal_invariants() -> None:
    task = Task(program_assigned="program.py")
    task.mark_blocked()
    assert task.completion_status == "blocked"
    task.mark_pending()
    task.mark_started()
    task.mark_subprocess_finished(0, "7\n")
    task.mark_completed(7)

    assert task.completion_status == "completed"
    assert task.task_done is True
    assert task.subprocess_done is True
    assert task.subtasks_done is True
    assert task.result == task.return_value == 7
    assert task.started_at is not None
    assert task.finished_at is not None
    task.validate_invariants()


def test_failed_and_aborted_tasks_are_terminal() -> None:
    failed = Task(program_assigned="program.py")
    failed.mark_started()
    failed.mark_subprocess_finished(3, "", "failure")
    assert failed.completion_status == "failed"
    failed.validate_invariants()

    child = Task(program_assigned="child.py")
    parent = Task(
        program_assigned="parent.py",
        task_children=[child],
        orchestration_task=child,
    )
    child.orchestration_task = parent
    parent.abort()
    assert parent.completion_status == "aborted"
    assert parent.orchestration_task is None
    assert child.orchestration_task is None
    parent.validate_invariants()


def test_terminal_task_cannot_restart_or_complete_without_dependencies() -> None:
    task = completed_leaf()
    with pytest.raises(TaskInvariantError):
        task.mark_started()

    incomplete = Task(
        program_assigned="parent.py",
        task_children=[Task(program_assigned="child.py")],
    )
    incomplete.mark_started()
    incomplete.mark_subprocess_finished(0, "1\n")
    with pytest.raises(TaskInvariantError):
        incomplete.mark_completed(1)


def test_every_task_field_has_one_semantic_classification() -> None:
    actual = {item.name for item in fields(Task)}
    groups = Task.FIELD_CLASSIFICATION
    classified: set[str] = set()
    for group in groups.values():
        classified.update(group)

    assert classified == actual
    names = [name for group in groups.values() for name in group]
    assert len(names) == len(set(names))


def test_new_attempt_copies_definition_without_aliasing_runtime_state() -> None:
    source = Task(
        program_assigned="parent.py",
        args=["a"],
        name="parent",
        task_children=[Task(program_assigned="child.py", name="child")],
        dependencies=["dependency"],
        environment={"MODE": "test"},
        role="parent",
        sequence_index=2,
        fragment_name="fragment",
    )
    source.mark_started()
    replacement = source.new_attempt()

    assert replacement.program_assigned == source.program_assigned
    assert replacement.args == source.args
    assert replacement.dependencies == source.dependencies
    assert replacement.environment == source.environment
    assert replacement.task_children[0].name == "child"
    assert replacement.completion_status == "pending"
    assert replacement.started_at is None
    assert replacement.orchestration_task is None

    replacement.args.append("b")
    replacement.dependencies.append("other")
    replacement.environment["MODE"] = "changed"
    replacement.task_children[0].name = "changed"
    assert source.args == ["a"]
    assert source.dependencies == ["dependency"]
    assert source.environment == {"MODE": "test"}
    assert source.task_children[0].name == "child"


def test_recovery_clone_retains_only_explicit_completed_children() -> None:
    completed = completed_leaf(6)
    running = Task(program_assigned="running.py", name="running")
    running.mark_started()
    source = Task(
        program_assigned="parent.py",
        task_children=[completed, running],
    )

    replacement = source.clone_for_recovery({0: completed, 1: running})

    assert replacement.task_children[0].task_done is True
    assert replacement.task_children[0].return_value == 6
    assert replacement.captured_list == [6, None]
    assert replacement.task_children[1].completion_status == "pending"
    assert replacement.task_children[1].started_at is None
    assert replacement.subtasks_done is False


def test_definition_update_is_always_a_fresh_attempt() -> None:
    task = completed_leaf(3)
    updated = task.with_definition_updates(
        role="parent", sequence_index=5, fragment_name="new"
    )
    assert updated.role == "parent"
    assert updated.sequence_index == 5
    assert updated.fragment_name == "new"
    assert updated.completion_status == "pending"
    assert updated.task_done is False
    assert updated.return_value is None


def test_replace_child_resets_capture_slot_and_derived_state() -> None:
    parent = Task(
        program_assigned="parent.py",
        task_children=[completed_leaf(2)],
        captured_list=[2],
    )
    assert parent.subtasks_done is True
    replacement = Task(program_assigned="replacement.py")
    parent.replace_child(0, replacement)
    assert parent.task_children == [replacement]
    assert parent.captured_list == [None]
    assert parent.subtasks_done is False


def test_serialization_round_trip_preserves_public_shape_and_state() -> None:
    task = completed_leaf(9)
    serialized = task.to_dict()
    restored = Task.from_dict(serialized)
    assert restored.to_dict() == serialized
    assert "result_policy" not in serialized
    assert isinstance(restored.result_policy, IntegerSumResultPolicy)


def test_removed_compatibility_api_is_not_available() -> None:
    removed = {
        "mark_finished",
        "_new_attempt",
        "_copy_completed_state_from",
        "with_updates",
        "assigned_program",
        "program",
        "status",
    }
    assert all(not hasattr(Task, name) for name in removed)


def test_orchestrators_do_not_assign_task_lifecycle_fields_directly() -> None:
    protected = {
        "completion_status",
        "task_done",
        "subprocess_done",
        "return_value",
        "result",
        "started_at",
        "finished_at",
    }
    root = Path(__file__).resolve().parents[1]
    violations: list[tuple[str, int, str]] = []
    for filename in ("main.py", "worker.py"):
        tree = ast.parse((root / filename).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            targets: list[ast.expr] = []
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            for target in targets:
                if isinstance(target, ast.Attribute) and target.attr in protected:
                    violations.append(
                        (filename, int(getattr(node, "lineno", 0)), target.attr)
                    )
    assert violations == []
