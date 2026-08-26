from __future__ import annotations

from pathlib import Path
import sys


PROTOTYPE_ROOT = Path(__file__).resolve().parents[2]

if str(PROTOTYPE_ROOT) not in sys.path:
    sys.path.insert(0, str(PROTOTYPE_ROOT))

from main import Main  # noqa: E402
from task import Task  # noqa: E402


def build_demo_task() -> Task:
    return Task(
        program_assigned="e2e/task1.py",
        name="task.py",
        fragment_name="e2e/task1.py",
        task_children=[
            Task(
                program_assigned="e2e/task3.py",
                name="task3.py",
                fragment_name="e2e/task3.py",
                task_children=[
                    Task(
                        program_assigned="e2e/task4.py",
                        name="task4.py",
                        fragment_name="e2e/task4.py",
                    )
                ],
            )
        ],
    )


def test_end_to_end_demo_run() -> None:
    main_runner = Main(worker_count=4, task=build_demo_task())
    report = main_runner.run()

    assert report["finished"] is True
    assert report["return_code"] == 0
    assert report["return_value"] == 8

    task = report["task"]
    assert task["task_done"] is True
    assert task["subtasks_done"] is True
    assert task["return_value"] == 8
    assert task["captured_list"] == [7]

    worker_tree = report["worker_tree"]
    assert worker_tree["task"]["completion_status"] == "completed"
    assert worker_tree["task"]["return_value"] == 8


def test_suddenly_unhealthy_worker_scenario() -> None:
    main_runner = Main(worker_count=4, task=build_demo_task())
    main_runner.workers[0].start(main_runner.task, workers=main_runner.workers)
    main_runner.started = True

    unhealthy_worker = next(
        worker
        for worker in main_runner.workers
        if worker.task is not None and worker.task.name == "task3.py"
    )
    old_task = unhealthy_worker.task
    assert old_task is not None

    unhealthy_worker.healthy = False
    report = main_runner.wait(poll_interval=0.001, timeout=5.0)

    assert report["finished"] is True
    assert report["return_code"] == 0
    assert report["return_value"] == 8
    assert unhealthy_worker.healthy is False
    assert unhealthy_worker.task is None

    replacement_worker = next(
        worker
        for worker in main_runner.workers
        if worker.task is not None and worker.task.name == "task3.py"
    )
    assert replacement_worker is not unhealthy_worker
    assert replacement_worker.task is not old_task
    assert replacement_worker.task.task_done is True
    assert replacement_worker.task.task_children[0].task_done is True


def test_failure_rolls_back_to_parent_and_retries(tmp_path: Path) -> None:
    marker = tmp_path / "child-marker.txt"

    task = Task(
        program_assigned="e2e/fallback/root.py",
        name="root",
        task_children=[
            Task(
                program_assigned="e2e/fallback/child.py",
                args=[str(marker)],
                name="child",
            )
        ],
    )

    main_runner = Main(worker_count=2, task=task)
    report = main_runner.run()

    assert report["finished"] is True
    assert report["return_code"] == 0
    assert report["return_value"] == 17

    live_root_task = main_runner.workers[0].task
    assert live_root_task is not None
    assert live_root_task.orchestration_task is None
    assert live_root_task.task_done is True
    assert live_root_task.return_value == 17
    assert live_root_task.task_children[0].orchestration_task is None
    assert live_root_task.task_children[0].task_done is True
    assert live_root_task.task_children[0].return_value == 7


def test_nested_failure_replaces_task_in_parent_tree(tmp_path: Path) -> None:
    marker = tmp_path / "nested-marker.txt"

    parent_task = Task(
        program_assigned="e2e/nested/parent.py",
        name="parent",
        task_children=[
            Task(
                program_assigned="e2e/nested/child.py",
                args=[str(marker)],
                name="child",
            )
        ],
    )
    task = Task(
        program_assigned="e2e/nested/root.py",
        name="root",
        task_children=[parent_task],
    )

    main_runner = Main(worker_count=3, task=task)
    report = main_runner.run()

    assert report["finished"] is True
    assert report["return_code"] == 0
    assert report["return_value"] == 7
    assert main_runner.workers[0].task is task
    assert task.task_children[0] is not parent_task
    assert task.task_children[0].task_done is True
    assert task.task_children[0].return_value == 6


def test_abort_task_returns_none_for_leaf_node() -> None:
    task = Task(program_assigned="leaf.py", name="leaf")
    main_runner = Main(worker_count=1, task=task)

    replacement = main_runner.abort_task(task)

    assert replacement is None
    assert task.task_done is True
    assert task.completion_status == "aborted"
    assert task.orchestration_task is None


def test_abort_task_returns_fresh_tree_for_fragmented_task() -> None:
    task = Task(
        program_assigned="program.py",
        name="program",
        task_children=[
            Task(
                program_assigned="fragment.py",
                name="fragment",
                task_children=[
                    Task(program_assigned="child.py", name="child"),
                ],
            )
        ],
    )
    main_runner = Main(worker_count=1, task=task)

    replacement = main_runner.abort_task(task)

    assert replacement is not None
    assert replacement is not task
    assert replacement.program_assigned == "program.py"
    assert not hasattr(replacement, "original_program_assigned")
    assert not hasattr(replacement, "original_task_children")
    assert replacement.task_done is False
    assert replacement.task_children[0].program_assigned == "fragment.py"
    assert replacement.task_children[0].orchestration_task is None
    assert replacement.orchestration_task is None
