from __future__ import annotations

from pathlib import Path

from prototype.main import Main
from prototype.task import Task
from prototype.trace import Trace
from prototype.worker import Prunner, Worker


class ExplodingPrunner(Prunner):
    def start(self, argv: list[str], *, cwd: str | None = None) -> int:
        raise RuntimeError("injected acceptance failure")


def test_recovery_waits_and_resumes_after_worker_registration() -> None:
    main = Main(
        worker_count=1,
        task=Task(program_assigned="e2e/task1.py", name="root"),
        trace=Trace(path=None),
    )
    main.workers[0].start(main.task, workers=main.workers)
    main.started = True
    source = main.workers[0]
    source.healthy = False

    waiting = main.poll()
    assert waiting["finished"] is False
    assert main.pending_recoveries == [source]
    assert source.assignment().task is not None
    assert source.has_active_process() is False

    replacement = Worker(role="child", index=1, trace=main.trace)
    main.register_worker(replacement)
    report = main.wait(poll_interval=0.001, timeout=5.0)

    assert report["finished"] is True
    assert report["return_value"] == 1
    assert main.pending_recoveries == []
    assert source.assignment().task is None
    assert replacement.assignment().task is main.task


def test_nonzero_subprocess_exit_moves_task_to_another_worker(
    tmp_path: Path,
) -> None:
    marker = tmp_path / "retry-marker.txt"
    task = Task(
        program_assigned="e2e/fallback/child.py",
        args=[str(marker)],
        name="retry-on-another-worker",
    )
    main = Main(worker_count=2, task=task, trace=Trace(path=None))
    report = main.run()

    assert report["finished"] is True
    assert report["return_value"] == 7
    assert main.workers[0].healthy is False
    assert main.workers[0].assignment().task is None
    assert main.workers[1].assignment().task is main.task
    assert main.workers[1].healthy is True


def test_health_recovery_retains_completed_healthy_descendant() -> None:
    completed_child = Task(program_assigned="e2e/task4.py", name="child")
    completed_child.mark_started()
    completed_child.mark_subprocess_finished(0, "4\n")
    completed_child.mark_completed(4)
    root = Task(
        program_assigned="e2e/task1.py",
        name="root",
        task_children=[completed_child],
    )
    main = Main(worker_count=3, task=root, trace=Trace(path=None))
    source, child_worker, target = main.workers
    source.bind_assignment(root, parent=None, task_index=None)
    child_worker.bind_assignment(completed_child, parent=source, task_index=0)
    source.child_workers.append(child_worker)
    main.started = True
    source.healthy = False

    assert main.request_task_health(root) is False
    report = main.wait(poll_interval=0.001, timeout=5.0)

    assert report["finished"] is True
    assert report["return_value"] == 5
    replacement = target.assignment().task
    assert replacement is main.task
    assert replacement is not None
    assert replacement.task_children[0] is not completed_child
    assert replacement.task_children[0].return_value == 4
    assert replacement.captured_list == [4]


def test_transfer_acceptance_failure_leaves_one_queued_assignment() -> None:
    task = Task(program_assigned="e2e/task1.py", name="root")
    main = Main(worker_count=2, task=task, trace=Trace(path=None))
    source, target = main.workers
    target.prunner = ExplodingPrunner()
    source.bind_assignment(task, parent=None, task_index=None)
    source.healthy = False
    main.started = True

    assert main.request_task_health(task) is False

    assert main.pending_recoveries == [source]
    queued = source.assignment().task
    assert queued is main.task
    assert queued is not None
    assert queued is not task
    assert queued.completion_status == "pending"
    assert source.healthy is False
    assert target.healthy is False
    assert target.assignment().task is None
    assert not source.has_active_process()
    assert not target.has_active_process()


def test_register_worker_rejects_duplicate_index() -> None:
    main = Main(worker_count=1, trace=Trace(path=None))
    duplicate = Worker(index=0, trace=main.trace)
    try:
        main.register_worker(duplicate)
    except ValueError as exc:
        assert "already registered" in str(exc)
    else:
        raise AssertionError("duplicate worker index was accepted")


def test_main_abort_restarts_fragmented_assignment_through_public_api() -> None:
    task = Task(
        program_assigned="e2e/task1.py",
        name="root",
        task_children=[Task(program_assigned="e2e/task4.py", name="child")],
    )
    main = Main(worker_count=2, task=task, trace=Trace(path=None))
    main.workers[0].bind_assignment(task, parent=None, task_index=None)
    main.started = True

    replacement = main.abort_task(task)
    assert replacement is not None
    assert replacement is main.task
    assert task.completion_status == "aborted"

    report = main.wait(poll_interval=0.001, timeout=5.0)
    assert report["finished"] is True
    assert report["return_value"] == 5
