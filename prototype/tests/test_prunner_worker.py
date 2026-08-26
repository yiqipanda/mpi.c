from __future__ import annotations

from typing import Any

from prototype.task import Task
from prototype.trace import Trace
from prototype.worker import Prunner, PrunnerError, Worker


class FakePrunner(Prunner):
    def __init__(
        self,
        *,
        return_code: int | None = None,
        stdout: str = "",
        stderr: str = "",
        start_error: Exception | None = None,
    ) -> None:
        self.active = False
        self.return_code = return_code
        self.stdout = stdout
        self.stderr = stderr
        self.start_error = start_error
        self.started: list[tuple[list[str], str | None]] = []
        self.communicate_calls = 0
        self.terminate_calls = 0

    def start(self, argv: list[str], *, cwd: str | None = None) -> int:
        self.started.append((list(argv), cwd))
        if self.start_error is not None:
            raise self.start_error
        self.active = True
        return 1234

    def has_process(self) -> bool:
        return self.active

    def poll(self) -> int | None:
        return self.return_code

    def communicate(self) -> tuple[str, str]:
        self.communicate_calls += 1
        return self.stdout, self.stderr

    def terminate(self) -> None:
        self.terminate_calls += 1

    def clear(self) -> None:
        self.active = False


def test_prunner_is_the_only_popen_adapter(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    class DummyProcess:
        pid = 42

        def poll(self) -> int | None:
            return None

        def communicate(self) -> tuple[str, str]:
            return "output", ""

        def terminate(self) -> None:
            captured["terminated"] = True

    def fake_popen(argv: list[str], **kwargs: Any) -> DummyProcess:
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return DummyProcess()

    monkeypatch.setattr("prototype.worker.subprocess.Popen", fake_popen)
    runner = Prunner()
    assert runner.start(["program", "argument"], cwd="/work") == 42
    assert captured["argv"] == ["program", "argument"]
    assert captured["kwargs"] == {
        "cwd": "/work",
        "stdout": __import__("subprocess").PIPE,
        "stderr": __import__("subprocess").PIPE,
        "text": True,
    }


def test_worker_executes_and_finalizes_through_injected_prunner() -> None:
    runner = FakePrunner(stdout="5\n")
    task = Task(program_assigned="program.py", working_dir="/work")
    worker = Worker(prunner=runner, trace=Trace(path=None))

    worker.start(task)
    assert runner.started[0][1] == "/work"
    assert worker.pid == 1234
    runner.return_code = 0
    worker.poll()

    assert task.completion_status == "completed"
    assert task.return_value == 5
    assert runner.communicate_calls == 1
    assert worker.has_active_process() is False


def test_nonzero_exit_is_reported_for_main_owned_recovery() -> None:
    runner = FakePrunner(return_code=4, stderr="failed")
    task = Task(program_assigned="program.py")
    worker = Worker(prunner=runner, trace=Trace(path=None))
    worker.start(task)
    worker.poll()

    assert task.completion_status == "failed"
    assert task.return_code == 4
    assert worker.needs_recovery() is True
    assert worker.healthy is True


def test_invalid_integer_result_fails_task_without_suspecting_worker() -> None:
    runner = FakePrunner(return_code=0, stdout="not-an-integer\n")
    task = Task(program_assigned="program.py")
    worker = Worker(prunner=runner, trace=Trace(path=None))
    worker.start(task)
    worker.poll()

    assert task.completion_status == "failed"
    assert task.return_code == 1
    assert "integer-sum requires" in task.stderr
    assert worker.needs_recovery() is False


def test_invalid_child_result_propagates_failure_to_parent() -> None:
    child_task = Task(program_assigned="child.py", name="child")
    parent_task = Task(
        program_assigned="parent.py", name="parent", task_children=[child_task]
    )
    parent = Worker(
        role="parent", prunner=FakePrunner(), task=parent_task, trace=Trace(path=None)
    )
    child = Worker(
        role="child",
        index=1,
        prunner=FakePrunner(return_code=0, stdout="invalid\n"),
        trace=parent.trace,
    )
    workers = [parent, child]
    parent.start(parent_task, workers=workers)
    parent.poll(workers=workers)

    assert child_task.completion_status == "failed"
    assert parent_task.completion_status == "failed"
    assert parent.needs_recovery() is False


def test_prunner_start_failure_is_normalized_as_worker_failure() -> None:
    runner = FakePrunner(start_error=PrunnerError("unavailable"))
    task = Task(program_assigned="program.py")
    worker = Worker(prunner=runner, trace=Trace(path=None))
    worker.start(task)

    assert task.completion_status == "failed"
    assert worker.needs_recovery() is True
    assert worker.status["task_state"] == "failed"


def test_stale_generation_terminates_only_the_stale_process() -> None:
    runner = FakePrunner()
    task = Task(program_assigned="program.py")
    worker = Worker(prunner=runner, trace=Trace(path=None))
    worker.start(task)
    worker.assignment_generation += 1
    worker.poll()

    assert runner.terminate_calls == 1
    assert runner.communicate_calls == 1
    assert worker.has_active_process() is False
    assert worker.pid is None


def test_abort_cleans_process_and_task_tree() -> None:
    runner = FakePrunner()
    task = Task(program_assigned="program.py")
    worker = Worker(prunner=runner, task=task, trace=Trace(path=None))
    worker.start(task)
    worker.abort_assignment_tree()

    assert runner.terminate_calls == 1
    assert task.completion_status == "aborted"
    assert worker.pid is None
    assert worker.status["program_state"] == "aborted"
