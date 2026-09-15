from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, cast

from prototype.status import WorkerStatus, new_worker_status
from prototype.task import Task
from prototype.trace import Trace

TERMINAL_STATES = {"completed", "failed", "aborted"}


class PrunnerError(RuntimeError):
    pass


@dataclass
class Prunner:
    """Own the complete lifecycle of one local subprocess."""

    process: subprocess.Popen[str] | None = field(default=None, init=False)

    def start(self, argv: list[str], *, cwd: str | None = None) -> int:
        if self.process is not None:
            raise RuntimeError("Prunner already owns a subprocess")
        try:
            self.process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as exc:
            raise PrunnerError("could not start subprocess") from exc
        return self.process.pid

    def has_process(self) -> bool:
        return self.process is not None

    def poll(self) -> int | None:
        try:
            return None if self.process is None else self.process.poll()
        except OSError as exc:
            raise PrunnerError("could not poll subprocess") from exc

    def communicate(self) -> tuple[str, str]:
        if self.process is None:
            return "", ""
        try:
            return self.process.communicate()
        except OSError as exc:
            raise PrunnerError("could not collect subprocess output") from exc

    def terminate(self) -> None:
        try:
            if self.process is not None and self.process.poll() is None:
                self.process.terminate()
        except OSError as exc:
            raise PrunnerError("could not terminate subprocess") from exc

    def clear(self) -> None:
        self.process = None


@dataclass(frozen=True)
class AssignmentView:
    task: Task | None
    parent: Worker | None
    task_index: int | None
    children: tuple[Worker, ...]


@dataclass
class Worker:
    role: str = "worker"
    index: int = 0
    task: Task | None = None
    parent_worker: Worker | None = None
    task_index: int | None = None
    healthy: bool = True
    assignment_generation: int = 0
    trace: Trace = field(default_factory=Trace)
    status: WorkerStatus = field(default_factory=new_worker_status)
    last_report: dict[str, Any] | None = None
    prunner: Prunner = field(default_factory=Prunner)
    process_generation: int | None = None
    child_workers: list[Worker] = field(
        default_factory=cast(Callable[[], list["Worker"]], list)
    )
    pid: int | None = None
    execution_failed: bool = False

    def assignment(self) -> AssignmentView:
        return AssignmentView(
            task=self.task,
            parent=self.parent_worker,
            task_index=self.task_index,
            children=tuple(self.child_workers),
        )

    def has_active_process(self) -> bool:
        return self.prunner.has_process()

    def needs_recovery(self) -> bool:
        return self.execution_failed

    def is_available(self) -> bool:
        return (
            self.healthy
            and not self.prunner.has_process()
            and (self.task is None or self.task.completion_status in TERMINAL_STATES)
        )

    def health_check(self, task: Task) -> bool | None:
        if self.task is not task:
            return None
        return self.healthy

    def child_index_for(self, parent_task: Task) -> int | None:
        if self.task_index is not None:
            return (
                self.task_index
                if 0 <= self.task_index < len(parent_task.task_children)
                else None
            )
        for child_index, child_task in enumerate(parent_task.task_children):
            if self.task is child_task:
                return child_index
        return None

    def replace_child_task(self, previous: Task, replacement: Task) -> bool:
        if self.task is None:
            return False
        for child_index, child_task in enumerate(self.task.task_children):
            if child_task is previous:
                self.task.replace_child(child_index, replacement)
                return True
        return False

    def replace_child_worker(self, previous: Worker, replacement: Worker) -> None:
        self.child_workers = [
            replacement if child is previous else child for child in self.child_workers
        ]

    def remove_child_worker(self, child: Worker) -> None:
        self.child_workers = [
            candidate for candidate in self.child_workers if candidate is not child
        ]

    def bind_assignment(
        self,
        task: Task,
        *,
        parent: Worker | None,
        task_index: int | None,
    ) -> None:
        self.parent_worker = parent
        self.task_index = task_index
        self.task = task
        self.last_report = None
        self.execution_failed = False
        self.status["return_code"] = None
        self.status["finished_at"] = None
        self.status["message"] = f"{self.role} worker received {task.name}"

    def accept_assignment(
        self,
        task: Task,
        *,
        parent: Worker | None,
        task_index: int | None,
        children: list[tuple[Worker, int]],
        workers: list[Worker],
    ) -> None:
        if self.task is not None or self.child_workers or self.prunner.has_process():
            self.abort_assignment_tree()
        self.bind_assignment(task, parent=parent, task_index=task_index)
        self.child_workers = [child for child, _ in children]
        for child, child_index in children:
            child.bind_assignment(
                task.task_children[child_index], parent=self, task_index=child_index
            )
        self.start(task, workers=workers)
        for child, _ in children:
            if (
                child.task is not None
                and not child.task.task_done
                and not child.has_active_process()
            ):
                child.start(child.task, workers=workers)

    def hold_assignment(
        self,
        task: Task,
        *,
        parent: Worker | None,
        task_index: int | None,
        children: list[tuple[Worker, int]],
    ) -> None:
        """Retain a recoverable assignment without executing it locally."""
        self.bind_assignment(task, parent=parent, task_index=task_index)
        self.child_workers = [child for child, _ in children]
        for child, child_index in children:
            child.bind_assignment(
                task.task_children[child_index], parent=self, task_index=child_index
            )
        self.mark_unavailable(
            f"{self.role} worker holding task until a replacement is available"
        )

    def release_assignment(self) -> None:
        self.stop_local_process(clear_pid=True)
        self.task = None
        self.task_index = None
        self.parent_worker = None
        self.child_workers.clear()
        self.execution_failed = False

    def mark_unavailable(self, message: str) -> None:
        self.healthy = False
        self.status["program_state"] = "unavailable"
        self.status["task_state"] = "unavailable"
        self.status["message"] = message

    def stop_local_process(self, *, clear_pid: bool) -> None:
        try:
            if self.prunner.has_process():
                self.prunner.terminate()
                self.prunner.communicate()
        except PrunnerError as exc:
            self.execution_failed = True
            self.trace.record(
                "worker",
                "process runner cleanup failed",
                role=self.role,
                index=self.index,
                error=type(exc).__name__,
            )
        finally:
            self.prunner.clear()
        self.process_generation = None
        if clear_pid:
            self.pid = None
        self.assignment_generation += 1

    def abort_assignment_tree(self) -> None:
        self.stop_local_process(clear_pid=True)
        if self.task is not None:
            self.task.abort()
            self.status["program_state"] = "aborted"
            self.status["task_state"] = "aborted"
            self.status["message"] = f"{self.role} worker aborted {self.task.name}"
            self.trace.record(
                "worker", "task aborted", role=self.role, index=self.index
            )
        for child_worker in list(self.child_workers):
            child_worker.abort_assignment_tree()
            child_worker.parent_worker = None
        self.child_workers.clear()

    def _available_workers(self, workers: list[Worker] | None) -> list[Worker]:
        if not workers:
            return []
        return [
            worker
            for worker in workers
            if worker.index != self.index and worker.is_available()
        ]

    def _start_subprocess(self, task: Task) -> None:
        if self.prunner.has_process():
            return
        task.mark_started()
        self.status["program_state"] = "running"
        self.status["task_state"] = "running"
        self.status["message"] = f"{self.role} worker started {task.name}"
        self.trace.record(
            "worker", "task started", role=self.role, index=self.index, task=task.name
        )
        try:
            self.pid = self.prunner.start(task.spawn_argv(), cwd=task.working_dir)
        except PrunnerError as exc:
            self._record_prunner_failure(task, exc)
            return
        self.process_generation = self.assignment_generation

    def _record_prunner_failure(self, task: Task, error: Exception) -> None:
        self.prunner.clear()
        self.process_generation = None
        if not task.task_done:
            task.mark_subprocess_finished(1, "", str(error))
        self.execution_failed = True
        self.status["program_state"] = "finished"
        self.status["task_state"] = "failed"
        self.status["return_code"] = 1
        self.status["finished_at"] = task.finished_at
        self.status["message"] = f"{self.role} worker process runner failed {task.name}"
        self.trace.record(
            "worker",
            "process runner failed",
            role=self.role,
            index=self.index,
            task=task.name,
            error=type(error).__name__,
        )

    def _capture_child_results(self) -> None:
        if self.task is None or self.task.task_done:
            return
        for child_index, child_task in enumerate(self.task.task_children):
            child_worker = next(
                (
                    candidate
                    for candidate in self.child_workers
                    if candidate.task_index == child_index
                    or candidate.task is child_task
                ),
                None,
            )
            if child_worker is None or child_worker.task is None:
                continue
            if (
                child_worker.task.task_done
                and child_worker.task.completion_status == "failed"
                and not child_worker.needs_recovery()
            ):
                self.stop_local_process(clear_pid=False)
                self.task.mark_subprocess_finished(
                    child_worker.task.return_code or 1,
                    self.task.stdout,
                    f"child task {child_worker.task.name!r} failed",
                )
                self.status["program_state"] = "finished"
                self.status["task_state"] = "failed"
                self.status["return_code"] = self.task.return_code
                self.status["finished_at"] = self.task.finished_at
                self.status["message"] = (
                    f"{self.role} worker stopped after child task failure"
                )
                return
            if (
                not child_worker.task.task_done
                or child_worker.task.completion_status != "completed"
            ):
                continue
            if (
                child_index < len(self.task.captured_list)
                and self.task.captured_list[child_index] is not None
            ):
                continue
            child_value = self.task.result_policy.child_value(child_worker.task)
            if child_value is None:
                raise ValueError(
                    f"completed child {child_worker.task.name!r} has no integer result"
                )
            self.task.record_child_result(child_index, child_value)
            self.trace.record(
                "worker",
                "captured child result",
                role=self.role,
                index=self.index,
                child_index=child_index,
                child_task=child_task.name,
                value=child_value,
            )

    def _finalize_task_if_ready(self) -> None:
        if self.task is None or self.task.task_done:
            return
        if self.task.orchestration_task is not None:
            return
        if not self.task.subprocess_done or not self.task.subtasks_done:
            return
        final_value = self.task.result_policy.combine(
            self.task.subprocess_value, list(self.task.captured_list)
        )
        self.task.mark_completed(final_value)
        self.trace.record(
            "worker",
            "task finalized",
            role=self.role,
            index=self.index,
            task=self.task.name,
            return_value=final_value,
        )
        self.status["program_state"] = "finished"
        self.status["task_state"] = "completed"
        self.status["return_code"] = self.task.return_code
        self.status["finished_at"] = self.task.finished_at
        self.status["message"] = f"{self.role} worker finished {self.task.name}"
        self.last_report = {
            "role": self.role,
            "index": self.index,
            "task": self.task.to_dict(),
            "return_code": self.task.return_code,
            "stdout": self.task.stdout,
            "stderr": self.task.stderr,
            "return_value": final_value,
        }

    def poll(self, *, workers: list[Worker] | None = None) -> dict[str, Any]:
        self._start_children(workers)
        if self.prunner.has_process() and self.task is not None:
            if self.process_generation != self.assignment_generation:
                self.stop_local_process(clear_pid=True)
                return self.snapshot()
            try:
                return_code = self.prunner.poll()
            except PrunnerError as exc:
                self._record_prunner_failure(self.task, exc)
                return self.snapshot()
            if return_code is not None:
                try:
                    stdout, stderr = self.prunner.communicate()
                except PrunnerError as exc:
                    self._record_prunner_failure(self.task, exc)
                    return self.snapshot()
                self.prunner.clear()
                self.process_generation = None
                self.task.mark_subprocess_finished(return_code, stdout, stderr)
                if return_code == 0 and self.task.completion_status == "failed":
                    self.status["program_state"] = "finished"
                    self.status["task_state"] = "failed"
                    self.status["return_code"] = self.task.return_code
                    self.status["finished_at"] = self.task.finished_at
                    self.status["message"] = (
                        f"{self.role} worker received an invalid task result"
                    )
                    self.trace.record(
                        "worker",
                        "task result policy failed",
                        role=self.role,
                        index=self.index,
                        task=self.task.name,
                    )
                    return self.snapshot()
                if return_code != 0:
                    self.execution_failed = True
                    self.status["program_state"] = "finished"
                    self.status["task_state"] = "failed"
                    self.status["return_code"] = return_code
                    self.status["finished_at"] = self.task.finished_at
                    self.status["message"] = (
                        f"{self.role} worker subprocess failed {self.task.name}"
                    )
                    self.trace.record(
                        "worker",
                        "task subprocess failed",
                        role=self.role,
                        index=self.index,
                        task=self.task.name,
                        return_code=return_code,
                    )
                    return self.snapshot()
                self.last_report = {
                    "role": self.role,
                    "index": self.index,
                    "task": self.task.to_dict(),
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_value": self.task.return_value,
                }
                self.status["program_state"] = "running"
                self.status["task_state"] = self.task.completion_status
                self.status["return_code"] = return_code
                self.status["message"] = (
                    f"{self.role} worker subprocess finished {self.task.name}"
                )
                self.trace.record(
                    "worker",
                    "task subprocess finished",
                    role=self.role,
                    index=self.index,
                    task=self.task.name,
                    return_code=return_code,
                )
        for child_worker in list(self.child_workers):
            child_worker.poll(workers=workers)
        self._capture_child_results()
        self._finalize_task_if_ready()
        return self.snapshot()

    def terminate(self) -> None:
        self.abort_assignment_tree()

    def _start_children(self, workers: list[Worker] | None) -> None:
        if self.task is None or self.task.task_done:
            return
        delegated_indices = {
            worker.task_index
            for worker in self.child_workers
            if worker.task is not None and worker.task_index is not None
        }
        delegated_tasks = {
            id(worker.task) for worker in self.child_workers if worker.task is not None
        }
        for child_index, child_task in enumerate(self.task.task_children):
            if (
                child_index in delegated_indices
                or id(child_task) in delegated_tasks
                or child_task.completion_status not in {"pending", "blocked"}
            ):
                continue
            available_workers = self._available_workers(workers)
            if not available_workers:
                child_task.mark_blocked()
                continue
            child_worker = available_workers[0]
            child_worker.bind_assignment(
                child_task, parent=self, task_index=child_index
            )
            child_task.mark_pending()
            child_worker.start(child_task, workers=workers)
            self.child_workers.append(child_worker)
            delegated_tasks.add(id(child_task))

    def start(
        self, task: Task | None = None, *, workers: list[Worker] | None = None
    ) -> dict[str, Any]:
        task = task or self.task
        if task is None:
            raise RuntimeError("Worker requires a task before execution")
        self.assignment_generation += 1
        self.task = task
        task.prepare_for_scheduling()
        self.execution_failed = False
        self.status["return_code"] = None
        if self.role == "parent":
            self.status["parent_state"] = "running"
        else:
            self.status["child_states"][self.index] = "running"
        self._start_children(workers)
        if not task.subprocess_done:
            self._start_subprocess(task)
        else:
            self.status["program_state"] = "running"
            self.status["task_state"] = task.completion_status
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        process_state = "idle"
        if self.prunner.has_process():
            try:
                process_state = "running" if self.prunner.poll() is None else "exited"
            except PrunnerError:
                process_state = "failed"
        elif self.task is not None:
            process_state = self.task.completion_status
        return {
            "role": self.role,
            "index": self.index,
            "task_index": self.task_index,
            "healthy": self.healthy,
            "assignment_generation": self.assignment_generation,
            "pid": self.pid,
            "state": "idle" if self.task is None else self.task.completion_status,
            "process_state": process_state,
            "task": self.task.to_dict() if self.task is not None else None,
            "last_report": self.last_report,
            "children": [worker.snapshot() for worker in self.child_workers],
        }
