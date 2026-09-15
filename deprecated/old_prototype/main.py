from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any

from prototype.status import MainStatus, TaskState, new_main_status
from prototype.task import Task
from prototype.trace import Trace
from prototype.worker import Prunner, Worker


class Main:
    def __init__(
        self,
        worker_count: int = 3,
        task: Task | None = None,
        trace: Trace | None = None,
    ):
        self.worker_count = max(int(worker_count), 1)
        self.task = task or self._build_default_task()
        self.trace = trace or Trace(path=Path("trace.json"))
        self.workers: list[Worker] = self._build_workers()
        self.status: MainStatus = new_main_status()
        self.started = False
        self.pending_recoveries: list[Worker] = []

    def _build_default_task(self) -> Task:
        program = os.environ.get("PYTHON", sys.executable)
        return Task(
            program_assigned=program,
            args=["-c", "print(10)"],
            name="demo-task",
            fragment_name="taskA",
        )

    def _build_workers(self) -> list[Worker]:
        return [
            Worker(
                role="parent" if index == 0 else "child",
                index=index,
                trace=self.trace,
            )
            for index in range(self.worker_count)
        ]

    def get_self(self) -> Main:
        return self

    def get_workers(self) -> list[Worker]:
        return self.workers

    def get_available_workers(self) -> list[Worker]:
        return [worker for worker in self.workers if worker.is_available()]

    def request_task_health(self, task: Task) -> bool | None:
        for worker in self.workers:
            health = worker.health_check(task)
            if health is None:
                continue
            if health:
                self.trace.record(
                    "worker",
                    "task health check passed",
                    worker=worker.index,
                    task=task.name,
                )
                return True
            self._queue_recovery(worker, f"{worker.role} worker failed health check")
            self._recover_pending_assignments()
            return False
        return None

    def _monitor_workers(self, task: Task | None = None) -> None:
        if task is not None:
            self.request_task_health(task)
            return
        for worker in list(self.workers):
            assignment = worker.assignment()
            if assignment.task is None or assignment.task.task_done:
                continue
            if worker in self.pending_recoveries:
                continue
            self.request_task_health(assignment.task)

    def _queue_recovery(self, worker: Worker, message: str) -> None:
        if worker not in self.pending_recoveries:
            self.pending_recoveries.append(worker)
            worker.stop_local_process(clear_pid=True)
            worker.mark_unavailable(message)
            assignment = worker.assignment()
            self.trace.record(
                "worker",
                "assignment waiting for replacement",
                worker=worker.index,
                task=assignment.task.name if assignment.task is not None else None,
            )

    def _collect_execution_failures(self) -> None:
        for worker in self.workers:
            if worker.needs_recovery():
                self._queue_recovery(
                    worker,
                    f"{worker.role} worker unavailable after subprocess failure",
                )

    def _descendants(self, worker: Worker) -> set[int]:
        descendants: set[int] = set()
        stack = list(worker.assignment().children)
        while stack:
            candidate = stack.pop()
            if id(candidate) in descendants:
                continue
            descendants.add(id(candidate))
            stack.extend(candidate.assignment().children)
        return descendants

    def _replacement_worker(self, unhealthy_worker: Worker) -> Worker | None:
        descendants = self._descendants(unhealthy_worker)
        excluded = {id(unhealthy_worker)}
        ancestor = unhealthy_worker.assignment().parent
        while ancestor is not None:
            excluded.add(id(ancestor))
            ancestor = ancestor.assignment().parent
        candidates: list[Worker] = []
        for worker in self.workers:
            if id(worker) in excluded or not worker.is_available():
                continue
            assignment = worker.assignment()
            if assignment.parent is not None and id(worker) not in descendants:
                continue
            candidates.append(worker)
        return next(
            (worker for worker in candidates if worker.assignment().task is None),
            candidates[0] if candidates else None,
        )

    def _isolated_task_for_worker(self, source_worker: Worker) -> Task:
        assignment = source_worker.assignment()
        source_task = assignment.task
        if source_task is None:
            raise RuntimeError("Cannot isolate a worker without a task")
        trusted_children: dict[int, Task] = {}
        for child_worker in assignment.children:
            child_assignment = child_worker.assignment()
            if not child_worker.healthy or child_assignment.task is None:
                continue
            child_index = child_worker.child_index_for(source_task)
            if child_index is not None:
                trusted_children[child_index] = child_assignment.task
        return source_task.clone_for_recovery(trusted_children)

    def _transfer_task(self, source: Worker, target: Worker) -> bool:
        source_assignment = source.assignment()
        source_task = source_assignment.task
        if source_task is None:
            return False
        replacement_task = self._isolated_task_for_worker(source)
        target_subtree = self._descendants(target) | {id(target)}
        child_bindings: list[tuple[Worker, int]] = []
        for child_worker in source_assignment.children:
            child_assignment = child_worker.assignment()
            if (
                id(child_worker) in target_subtree
                or not child_worker.healthy
                or child_assignment.task is None
            ):
                continue
            child_index = child_worker.child_index_for(source_task)
            if child_index is not None:
                child_bindings.append((child_worker, child_index))

        parent_worker = source_assignment.parent
        if parent_worker is not None:
            parent_worker.replace_child_task(source_task, replacement_task)
            parent_worker.replace_child_worker(source, target)

        target_assignment = target.assignment()
        target_parent = target_assignment.parent
        if target_parent is not None:
            target_parent.remove_child_worker(target)

        source_task.abort()
        source.release_assignment()
        source.mark_unavailable(
            f"{source.role} worker unavailable after assignment failure"
        )

        try:
            target.accept_assignment(
                replacement_task,
                parent=parent_worker,
                task_index=source_assignment.task_index,
                children=child_bindings,
                workers=self.workers,
            )
        except Exception as exc:  # noqa: BLE001 - transaction compensation boundary
            retained_children = {
                index: child
                for index, child in enumerate(replacement_task.task_children)
                if child.task_done and child.completion_status == "completed"
            }
            queued_task = replacement_task.clone_for_recovery(retained_children)
            target.abort_assignment_tree()
            target.release_assignment()
            target.mark_unavailable(
                f"{target.role} worker failed while accepting an assignment"
            )
            source.hold_assignment(
                queued_task,
                parent=parent_worker,
                task_index=source_assignment.task_index,
                children=[],
            )
            if parent_worker is not None:
                parent_worker.replace_child_task(replacement_task, queued_task)
                parent_worker.replace_child_worker(target, source)
            if self.task is source_task:
                self.task = queued_task
            self.trace.record(
                "worker",
                "task transfer failed; assignment remains queued",
                worker=source.index,
                replacement_worker=target.index,
                task=source_task.name,
                error=type(exc).__name__,
            )
            return False
        if self.task is source_task:
            self.task = replacement_task
        self.trace.record(
            "worker",
            "task transferred after worker failure",
            worker=source.index,
            replacement_worker=target.index,
            task=source_task.name,
        )
        return True

    def _recover_pending_assignments(self) -> None:
        for source in list(self.pending_recoveries):
            if source.assignment().task is None:
                self.pending_recoveries.remove(source)
                continue
            target = self._replacement_worker(source)
            if target is None:
                continue
            if self._transfer_task(source, target):
                self.pending_recoveries.remove(source)

    def get_status(self) -> dict[str, Any]:
        if self.started:
            return self.poll()["status"]
        return dict(self.status)

    def abort_task(self, task: Task | None) -> Task | None:
        if task is None:
            return None
        for worker in self.workers:
            assignment = worker.assignment()
            if assignment.task is not task:
                continue
            replacement = task.new_attempt() if task.task_children else None
            worker.abort_assignment_tree()
            if replacement is not None:
                if assignment.parent is not None:
                    assignment.parent.replace_child_task(task, replacement)
                worker.bind_assignment(
                    replacement,
                    parent=assignment.parent,
                    task_index=assignment.task_index,
                )
                worker.start(replacement, workers=self.workers)
                if self.task is task:
                    self.task = replacement
            return replacement
        return task.abort_task()

    def create_worker(
        self,
        role: str,
        index: int,
        task: Task | None = None,
        prunner: Prunner | None = None,
    ) -> Worker:
        return Worker(
            role=role,
            index=index,
            task=task,
            trace=self.trace,
            prunner=prunner or Prunner(),
        )

    def register_worker(self, worker: Worker) -> None:
        if any(candidate.index == worker.index for candidate in self.workers):
            raise ValueError(f"worker index {worker.index} is already registered")
        worker.trace = self.trace
        self.workers.append(worker)
        self.worker_count = len(self.workers)
        self.status["child_states"][worker.index] = "idle"

    def _root_task(self) -> Task:
        root_assignment = self.workers[0].assignment().task
        return root_assignment if root_assignment is not None else self.task

    def _trace_program(self, message: str, **details: Any) -> None:
        self.trace.program(message, **details)

    def is_finished(self) -> bool:
        if self.pending_recoveries:
            return False
        if any(worker.has_active_process() for worker in self.workers):
            return False
        return self._task_tree_finished(self._root_task())

    def _task_tree_finished(self, task: Task) -> bool:
        return task.task_done and all(
            self._task_tree_finished(child) for child in task.task_children
        )

    def start(self) -> dict[str, Any]:
        if self.started:
            return self.poll()
        self.status["program_state"] = "running"
        self.status["task_state"] = "running"
        self.status["message"] = "main orchestration started"
        self.status["started_at"] = self.trace.record(
            "program", "main orchestration started"
        )["timestamp"]
        self._trace_program(
            "main run started", worker_count=self.worker_count, task=self.task.name
        )
        self.workers[0].start(self.task, workers=self.workers)
        self.status["parent_state"] = "running"
        for worker in self.workers[1:]:
            self.status["child_states"][worker.index] = "idle"
        self.started = True
        return self.poll()

    def _refresh_status(self, root_task: Task, finished: bool) -> int | None:
        child_states: dict[int, TaskState] = {}
        for worker in self.workers[1:]:
            child_task = worker.assignment().task
            child_states[worker.index] = (
                "idle" if child_task is None else child_task.completion_status
            )
        root_status = root_task.completion_status
        return_code = self._combined_return_code(root_task)
        self.status["parent_state"] = (
            root_status if self.workers[0].assignment().task is not None else "idle"
        )
        self.status["child_states"] = child_states
        self.status["task_state"] = (
            "completed" if finished and return_code == 0 else root_status
        )
        self.status["program_state"] = (
            "finished" if finished else ("running" if self.started else "idle")
        )
        self.status["return_code"] = return_code if finished else None
        self.status["return_value"] = root_task.return_value
        return return_code

    def poll(self, task: Task | None = None) -> dict[str, Any]:
        self._monitor_workers(task)
        self._recover_pending_assignments()
        if self.started:
            for worker in self.workers:
                if worker.healthy:
                    worker.poll(workers=self.workers)
        self._collect_execution_failures()
        self._recover_pending_assignments()

        root_task = self._root_task()
        self.task = root_task
        worker_snapshots = [worker.snapshot() for worker in self.workers]
        finished = self.is_finished() if self.started else False
        return_code = self._refresh_status(root_task, finished)
        if finished and self.status["finished_at"] is None:
            self.status["message"] = "main orchestration finished"
            self.status["finished_at"] = self.trace.record(
                "program", "main orchestration finished"
            )["timestamp"]
            self._trace_program("main run finished", return_code=return_code)
            self.trace.persist_if_configured()
        self.trace.record(
            "poll", "main poll", finished=finished, return_code=return_code
        )
        return {
            "task": root_task.to_dict(),
            "workers": worker_snapshots,
            "worker_tree": self.workers[0].snapshot(),
            "worker_count": self.worker_count,
            "return_code": return_code,
            "return_value": root_task.return_value,
            "finished": finished,
            "status": dict(self.status),
            "trace": self.trace.snapshot(),
        }

    def _combined_return_code(self, task: Task) -> int | None:
        return_codes = list(self._task_return_codes(task))
        if not return_codes:
            return None
        return max(int(return_code) for return_code in return_codes)

    def _task_return_codes(self, task: Task) -> list[int]:
        return_codes = [] if task.return_code is None else [int(task.return_code)]
        for child in task.task_children:
            return_codes.extend(self._task_return_codes(child))
        return return_codes

    def wait(self, poll_interval: float = 0.01, timeout: float = 5.0) -> dict[str, Any]:
        snapshot = self.start() if not self.started else self.poll()
        deadline = time.monotonic() + timeout
        while not snapshot["finished"]:
            if time.monotonic() > deadline:
                snapshot["timed_out"] = True
                self.status["message"] = "main orchestration timed out"
                snapshot["status"] = dict(self.status)
                return snapshot
            time.sleep(poll_interval)
            snapshot = self.poll()
        snapshot["timed_out"] = False
        return snapshot

    def run(self) -> dict[str, Any]:
        return self.wait()
