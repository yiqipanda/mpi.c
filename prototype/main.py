from __future__ import annotations

from pathlib import Path
from typing import Any
import os
import sys
import time

from task import Task
from trace import Trace
from worker import Worker


class Main:
    # Set up the simulation with explicit constructor parameters instead of config files.
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
        self.status: dict[str, Any] = {
            "program_state": "idle",
            "parent_state": "idle",
            "task_state": "idle",
            "child_states": {},
            "parent_pid": None,
            "child_pids": [],
            "return_code": None,
            "return_value": None,
            "message": "",
            "started_at": None,
            "finished_at": None,
        }
        self.started = False

    # Build the default program task used by the demo launcher.
    def _build_default_task(self) -> Task:
        program = os.environ.get("PYTHON", sys.executable)
        return Task(
            program_assigned=program,
            args=[
                "-c",
                "print(10)",
            ],
            name="demo-task",
            fragment_name="taskA",
        )

    # Build the worker registry owned by Main.
    def _build_workers(self) -> list[Worker]:
        return [
            Worker(role="parent" if index == 0 else "child", index=index, trace=self.trace)
            for index in range(self.worker_count)
        ]

    # Describe the workers that will participate in the simulation.
    def get_workers(self) -> list[Worker]:
        return self.workers

    # Return workers that are available to receive a task assignment.
    def get_available_workers(self) -> list[Worker]:
        return [worker for worker in self.workers if worker.is_available()]

    # Check the worker currently assigned to a task and migrate it if unhealthy.
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

            worker.healthy = False
            worker.status["program_state"] = "unavailable"
            worker.status["task_state"] = "unavailable"
            worker.status["message"] = f"{worker.role} worker failed health check"
            replacement = self._replacement_worker(worker)
            if replacement is None:
                self.trace.record(
                    "worker",
                    "task health check failed without replacement",
                    worker=worker.index,
                    task=task.name,
                )
                return False

            self._transfer_task(worker, replacement)
            self.trace.record(
                "worker",
                "task transferred after worker health failure",
                worker=worker.index,
                replacement_worker=replacement.index,
                task=task.name,
            )
            return False

        return None

    # Monitor every live assignment from Main's polling loop.
    def _monitor_workers(self, task: Task | None = None) -> None:
        if task is not None:
            self.request_task_health(task)
            return

        for worker in list(self.workers):
            assigned_task = worker.task
            if assigned_task is None or assigned_task.task_done:
                continue
            self.request_task_health(assigned_task)

    # Find a healthy worker that is not part of the unhealthy worker's subtree.
    def _replacement_worker(self, unhealthy_worker: Worker) -> Worker | None:
        excluded = {id(worker) for worker in unhealthy_worker.child_workers}
        ancestor = unhealthy_worker.parent_worker
        while ancestor is not None:
            excluded.add(id(ancestor))
            ancestor = ancestor.parent_worker
        candidates = [
            worker
            for worker in self.workers
            if worker is not unhealthy_worker
            and id(worker) not in excluded
            and worker.is_available()
        ]
        return next((worker for worker in candidates if worker.task is None), None) or (
            candidates[0] if candidates else None
        )

    # Find the child slot represented by a worker assignment.
    def _child_index(self, parent_task: Task, child_worker: Worker) -> int | None:
        if child_worker.task_index is not None:
            if 0 <= child_worker.task_index < len(parent_task.task_children):
                return child_worker.task_index
            return None

        for child_index, child_task in enumerate(parent_task.task_children):
            if child_worker.task is child_task:
                return child_index
        return None

    # Create an isolated task tree while preserving trusted completed children.
    def _isolated_task_for_worker(self, source_worker: Worker) -> Task:
        source_task = source_worker.task
        if source_task is None:
            raise RuntimeError("Cannot isolate a worker without a task")

        replacement = source_task._new_attempt()
        for child_worker in source_worker.child_workers:
            if not child_worker.healthy or child_worker.task is None:
                continue

            child_index = self._child_index(source_task, child_worker)
            if child_index is None:
                continue

            trusted_child = child_worker.task
            replacement_child = trusted_child._new_attempt()
            replacement_child._copy_completed_state_from(trusted_child)
            replacement.task_children[child_index] = replacement_child

            if replacement_child.task_done and replacement_child.completion_status == "completed":
                child_value = replacement_child.return_value
                if child_value is None:
                    child_value = replacement_child.subprocess_value
                if child_value is not None:
                    replacement.captured_list[child_index] = int(child_value)

        replacement._refresh_subtasks_done()
        return replacement

    # Move a task and its child-worker assignments to a healthy worker.
    def _transfer_task(self, source: Worker, target: Worker) -> None:
        parent_worker = source.parent_worker
        source_task = source.task
        if source_task is None:
            return
        replacement_task = self._isolated_task_for_worker(source)
        source_task_index = source.task_index
        child_bindings: list[tuple[Worker, int]] = []
        for child_worker in source.child_workers:
            if not child_worker.healthy or child_worker.task is None:
                continue
            child_index = self._child_index(source_task, child_worker)
            if child_index is not None:
                child_bindings.append((child_worker, child_index))

        if parent_worker is not None:
            source._replace_parent_task_reference(source_task, replacement_task)

        target_parent = target.parent_worker
        if target_parent is not None:
            target_parent.child_workers = [
                worker for worker in target_parent.child_workers if worker is not target
            ]
        target.parent_worker = None

        if parent_worker is not None:
            parent_worker.child_workers = [
                target if worker is source else worker
                for worker in parent_worker.child_workers
            ]
        source.parent_worker = None

        source._abort_worker_tree()
        source.task = None
        source.task_index = None
        source.status["program_state"] = "unavailable"
        source.status["task_state"] = "unavailable"
        source.status["message"] = f"{source.role} worker unavailable after health failure"

        if target.task is not None or target.child_workers:
            target._abort_worker_tree()
        target.parent_worker = parent_worker
        target.task = replacement_task
        target.task_index = source_task_index
        target.child_workers = [worker for worker, _ in child_bindings]
        for child_worker, child_index in child_bindings:
            child_worker.parent_worker = target
            child_worker.task_index = child_index
            child_worker.task = replacement_task.task_children[child_index]
            child_worker.last_report = None
            child_worker.status["return_code"] = None
            child_worker.status["finished_at"] = None
            child_worker.status["message"] = (
                f"{child_worker.role} worker received "
                f"{child_worker.task.name}"
            )
        target.last_report = None
        target.status["program_state"] = "running"
        target.status["task_state"] = "running"
        target.status["return_code"] = None
        target.status["finished_at"] = None
        target.status["message"] = f"{target.role} worker received {replacement_task.name}"
        target.start(replacement_task, workers=self.workers)
        for child_worker, _ in child_bindings:
            child_worker.start(child_worker.task, workers=self.workers)

        if self.task is source_task:
            self.task = replacement_task

    # Return the current combined status object.
    def get_status(self) -> dict[str, Any]:
        if self.started:
            return self.poll()["status"]
        return dict(self.status)

    # Abort a task and replace any live worker assignment that owns it.
    def abort_task(self, task: Task | None) -> Task | None:
        if task is None:
            return None

        for worker in self.workers:
            if worker.task is task:
                return worker._fallback_to_parent_worker(workers=self.workers)
        return task.abort_task()

    # Create a worker wrapper for a given role and index.
    def create_worker(self, role: str, index: int, task: Task | None = None) -> Worker:
        return Worker(role=role, index=index, task=task, trace=self.trace)

    # Return the live root task currently owned by worker 0.
    def _root_task(self) -> Task:
        return self.workers[0].task if self.workers[0].task is not None else self.task

    # Clarify trace usage by recording a structured program-level message.
    def _trace_program(self, message: str, **details: Any) -> None:
        self.trace.program(message, **details)

    # Report whether the task tree and worker subprocesses have all reached terminal states.
    def is_finished(self) -> bool:
        if any(worker.process is not None for worker in self.workers):
            return False
        return self._task_tree_finished(self._root_task())

    def _task_tree_finished(self, task: Task) -> bool:
        return task.task_done and all(
            self._task_tree_finished(child) for child in task.task_children
        )

    # Start the root worker and return the first snapshot.
    def start(self) -> dict[str, Any]:
        if self.started:
            return self.poll()

        self.status["program_state"] = "running"
        self.status["task_state"] = "running"
        self.status["message"] = "main orchestration started"
        self.status["started_at"] = self.trace.record("program", "main orchestration started")["timestamp"]
        self._trace_program("main run started", worker_count=self.worker_count, task=self.task.name)

        self.workers[0].start(self.task, workers=self.workers)
        self.status["parent_state"] = "running"
        for worker in self.workers[1:]:
            self.status["child_states"][worker.index] = "idle"
        self.started = True
        return self.poll()

    # Poll every worker and optionally request health for one assigned task.
    def poll(self, task: Task | None = None) -> dict[str, Any]:
        self._monitor_workers(task)

        if self.started:
            for worker in self.workers:
                worker.poll(workers=self.workers)

        root_task = self._root_task()
        self.task = root_task
        worker_snapshots = [worker.snapshot() for worker in self.workers]
        child_states = {
            worker.index: ("idle" if worker.task is None else worker.task.completion_status)
            for worker in self.workers[1:]
        }
        root_status = root_task.completion_status
        return_code = self._combined_return_code(root_task)
        finished = self.is_finished() if self.started else False

        self.status["parent_state"] = root_status if self.workers[0].task is not None else "idle"
        self.status["child_states"] = child_states
        self.status["task_state"] = "completed" if finished and return_code == 0 else root_status
        self.status["program_state"] = "finished" if finished else ("running" if self.started else "idle")
        self.status["return_code"] = return_code if finished else None
        self.status["return_value"] = root_task.return_value
        if finished and self.status["finished_at"] is None:
            self.status["message"] = "main orchestration finished"
            self.status["finished_at"] = self.trace.record("program", "main orchestration finished")["timestamp"]
            self._trace_program("main run finished", return_code=return_code)
            if self.trace.path is not None:
                self.trace.dump()

        self.trace.record("poll", "main poll", finished=finished, return_code=return_code)
        return {
            "task": root_task.to_dict(),
            "workers": worker_snapshots,
            "worker_tree": self.workers[0].snapshot(),
            "worker_count": self.worker_count,
            "return_code": return_code,
            "return_value": root_task.return_value,
            "finished": finished,
            "status": dict(self.status),
            "trace": self.trace.to_dict(),
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

    # Wait is a convenience wrapper for demos/tests; callers can use poll() directly instead.
    def wait(self, poll_interval: float = 0.01, timeout: float = 5.0) -> dict[str, Any]:
        if not self.started:
            snapshot = self.start()
        else:
            snapshot = self.poll()

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

    # Run the worker tree to completion for callers.
    def run(self) -> dict[str, Any]:
        return self.wait()
