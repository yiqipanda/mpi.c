from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
import subprocess

from task import Task
from trace import Trace

TERMINAL_STATES = {"completed", "failed", "aborted"}

@dataclass
class Worker:
    role: str = "worker"
    index: int = 0
    task: Task | None = None
    trace: Trace = field(default_factory=Trace)
    status: dict[str, Any] = field(default_factory=lambda: {
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
    })
    last_report: dict[str, Any] | None = None
    process: subprocess.Popen[str] | None = None
    child_workers: list["Worker"] = field(default_factory=list)
    pid: int | None = None

    # Report whether this worker can accept a new task assignment.
    def is_available(self) -> bool:
        return self.process is None and (
            self.task is None or self.task.completion_status in TERMINAL_STATES
        )

    # Find workers that can receive fragmented child tasks.
    def _available_workers(self, workers: list["Worker"] | None) -> list["Worker"]:
        if not workers:
            return []
        return [
            worker
            for worker in workers
            if worker.index != self.index and worker.is_available()
        ]

    # Launch the task program without waiting for it to finish.
    def _start_subprocess(self, task: Task) -> None:
        if self.process is not None:
            return

        task.mark_started()
        self.status["program_state"] = "running"
        self.status["task_state"] = "running"
        self.status["message"] = f"{self.role} worker started {task.name}"
        self.trace.record("worker", "task started", role=self.role, index=self.index, task=task.name)

        self.process = subprocess.Popen(
            task.spawn_argv(),
            cwd=task.working_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.pid = self.process.pid

    # Convert a finished child task into the value stored in the parent capture slot.
    def _child_task_value(self, child_task: Task) -> int | None:
        if child_task.return_value is not None:
            return int(child_task.return_value)
        if child_task.result is not None:
            try:
                return int(child_task.result)
            except (TypeError, ValueError):
                return None
        if child_task.subprocess_value is not None:
            return int(child_task.subprocess_value)
        return None

    # Copy completed child task values into the parent task in task_children order.
    def _capture_child_results(self) -> None:
        if self.task is None:
            return

        for child_index, child_task in enumerate(self.task.task_children):
            child_worker = None
            for candidate in self.child_workers:
                if candidate.task is child_task:
                    child_worker = candidate
                    break
            if child_worker is None or child_worker.task is None:
                continue
            if not child_worker.task.task_done:
                continue
            if child_index < len(self.task.captured_list) and self.task.captured_list[child_index] is not None:
                continue

            child_value = self._child_task_value(child_worker.task)
            if child_value is None:
                child_value = 0
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

    # Finalize this task if both its subprocess and child captures are ready.
    def _finalize_task_if_ready(self) -> None:
        if self.task is None:
            return

        if self.task.task_done:
            return
        if self.task.orchestration_task is not None:
            return
        if not self.task.subprocess_done or not self.task.subtasks_done:
            return

        child_values = [int(value) for value in self.task.captured_list if value is not None]
        subprocess_value = 0 if self.task.subprocess_value is None else int(self.task.subprocess_value)
        final_value = subprocess_value + sum(child_values)
        finished_at = self.trace.record(
            "worker",
            "task finalized",
            role=self.role,
            index=self.index,
            task=self.task.name,
            return_value=final_value,
        )["timestamp"]
        self.task.return_value = final_value
        self.task.result = final_value
        self.task.task_done = True
        self.task.completion_status = "completed"
        self.task.finished_at = finished_at

        self.status["program_state"] = "finished"
        self.status["task_state"] = self.task.completion_status
        self.status["return_code"] = self.task.return_code
        self.status["finished_at"] = finished_at
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

    # Poll the subprocess and finalize task state if it has exited.
    def poll(self, *, workers: list["Worker"] | None = None) -> dict[str, Any]:
        self._start_children(workers)

        if self.process is not None and self.task is not None:
            return_code = self.process.poll()
            if return_code is not None:
                stdout, stderr = self.process.communicate()
                self.task.mark_subprocess_finished(return_code, stdout, stderr)
                self.last_report = {
                    "role": self.role,
                    "index": self.index,
                    "task": self.task.to_dict(),
                    "return_code": return_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "return_value": self.task.return_value,
                }
                self.process = None
                self.status["program_state"] = "running"
                self.status["task_state"] = self.task.completion_status
                self.status["return_code"] = return_code
                self.status["message"] = f"{self.role} worker subprocess finished {self.task.name}"
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

    # Stop this worker's subprocess if it is still running.
    def terminate(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
        if self.task is not None:
            self.task.abort()
            self.status["program_state"] = "aborted"
            self.status["task_state"] = "aborted"
            self.status["message"] = f"{self.role} worker aborted {self.task.name}"
            self.trace.record("worker", "task aborted", role=self.role, index=self.index)

        for child_worker in self.child_workers:
            child_worker.terminate()

    # Start pending child tasks on available workers.
    def _start_children(self, workers: list["Worker"] | None) -> None:
        if self.task is None:
            return

        delegated_tasks = {id(worker.task) for worker in self.child_workers if worker.task is not None}
        for child_task in self.task.task_children:
            if id(child_task) in delegated_tasks or child_task.completion_status not in {"pending", "blocked"}:
                continue

            available_workers = self._available_workers(workers)
            if not available_workers:
                child_task.completion_status = "blocked"
                continue

            child_worker = available_workers[0]
            child_task.completion_status = "pending"
            child_worker.start(child_task, workers=workers)
            self.child_workers.append(child_worker)
            delegated_tasks.add(id(child_task))

    # Start this worker's task and any child tasks without waiting for completion.
    def start(self, task: Task | None = None, *, workers: list["Worker"] | None = None) -> dict[str, Any]:
        task = task or self.task
        if task is None:
            raise RuntimeError("Worker requires a task before execution")

        self.task = task
        self.task._sync_capture_slots()
        if task.completion_status in {"pending", "blocked"}:
            task.completion_status = "pending"
        self.status["return_code"] = None

        if self.role == "parent":
            self.status["parent_state"] = "running"
        else:
            self.status["child_states"][self.index] = "running"

        self._start_children(workers)
        self._start_subprocess(task)
        return self.snapshot()

    # Return a JSON-friendly view of this worker at poll time.
    def snapshot(self) -> dict[str, Any]:
        process_state = "idle"
        if self.process is not None:
            process_state = "running" if self.process.poll() is None else "exited"
        elif self.task is not None:
            process_state = self.task.completion_status

        return {
            "role": self.role,
            "index": self.index,
            "pid": self.pid,
            "state": "idle" if self.task is None else self.task.completion_status,
            "process_state": process_state,
            "task": self.task.to_dict() if self.task is not None else None,
            "last_report": self.last_report,
            "children": [worker.snapshot() for worker in self.child_workers],
        }
