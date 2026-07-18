from __future__ import annotations

from pathlib import Path
from typing import Any
import json
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

    # Return the current combined status object.
    def get_status(self) -> dict[str, Any]:
        if self.started:
            return self.poll()["status"]
        return dict(self.status)

    # Track an aborted task and stop any running worker work.
    def abort_task(self, task: Task | None) -> None:
        self.status["task_state"] = "aborted"
        self.status["program_state"] = "finished"
        self.status["return_code"] = 1
        self.status["return_value"] = None
        self.status["message"] = "task aborted"
        self.status["finished_at"] = self.trace.record("program", "task aborted")["timestamp"]
        if task is not None:
            task.abort()
        for worker in self.workers:
            worker.terminate()

    # Create a worker wrapper for a given role and index.
    def create_worker(self, role: str, index: int, task: Task | None = None) -> Worker:
        return Worker(role=role, index=index, task=task, trace=self.trace)

    # Clarify trace usage by recording a structured program-level message.
    def _trace_program(self, message: str, **details: Any) -> None:
        self.trace.program(message, **details)

    # Report whether the task tree and worker subprocesses have all reached terminal states.
    def is_finished(self) -> bool:
        if any(worker.process is not None for worker in self.workers):
            return False
        return self._task_tree_finished(self.task)

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

    # Poll every worker and return the current whole-tree snapshot.
    def poll(self) -> dict[str, Any]:
        if self.started:
            for worker in self.workers:
                worker.poll(workers=self.workers)

        worker_snapshots = [worker.snapshot() for worker in self.workers]
        child_states = {
            worker.index: ("idle" if worker.task is None else worker.task.completion_status)
            for worker in self.workers[1:]
        }
        root_status = self.task.completion_status
        return_code = self._combined_return_code()
        finished = self.is_finished() if self.started else False

        self.status["parent_state"] = root_status if self.workers[0].task is not None else "idle"
        self.status["child_states"] = child_states
        self.status["task_state"] = "completed" if finished and return_code == 0 else root_status
        self.status["program_state"] = "finished" if finished else ("running" if self.started else "idle")
        self.status["return_code"] = return_code if finished else None
        self.status["return_value"] = self.task.return_value
        if finished and self.status["finished_at"] is None:
            self.status["message"] = "main orchestration finished"
            self.status["finished_at"] = self.trace.record("program", "main orchestration finished")["timestamp"]
            self._trace_program("main run finished", return_code=return_code)
            if self.trace.path is not None:
                self.trace.dump()

        self.trace.record("poll", "main poll", finished=finished, return_code=return_code)
        return {
            "task": self.task.to_dict(),
            "workers": worker_snapshots,
            "worker_tree": self.workers[0].snapshot(),
            "worker_count": self.worker_count,
            "return_code": return_code,
            "return_value": self.task.return_value,
            "finished": finished,
            "status": dict(self.status),
            "trace": self.trace.to_dict(),
        }

    def _combined_return_code(self) -> int | None:
        return_codes = list(self._task_return_codes(self.task))
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

    # Run the worker tree to completion for demos/tests.
    def run(self) -> dict[str, Any]:
        return self.wait()




def _poll_and_print(
    main_runner: Main,
    *,
    poll_interval: float = 1.0,
    timeout: float | None = None,
) -> dict[str, Any]:
    report = main_runner.start()
    deadline = None if timeout is None else time.monotonic() + timeout
    _print_driver_view(report)

    while not report["finished"]:
        if deadline is not None and time.monotonic() >= deadline:
            report["timed_out"] = True
            main_runner.status["message"] = "main orchestration timed out"
            report["status"] = dict(main_runner.status)
            break
        time.sleep(poll_interval)
        report = main_runner.poll()
        _print_driver_view(report)

    return report


def _print_driver_view(report: dict[str, Any]) -> None:
    print(json.dumps(_driver_view(report), indent=2), flush=True)


def _driver_view(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for worker in report["workers"]:
        task = worker.get("task")
        rows.append(
            {
                "task_val": None if task is None else task.get("return_value"),
                "task": None if task is None else task.get("name"),
                "worker": f"{worker['role']}-{worker['index']}",
                "pid": worker.get("pid"),
                "completion_status": "idle" if task is None else task.get("completion_status"),
            }
        )
    return rows

def build_demo_task() -> Task:
    #task.py is our main program.
    #when we get a child worker, we assign task1.py to main worker and task2.py to child worker.
    #when we a get a grandchild worker, we update child worker to task3.py and grandchild is assigned task4.py
    return Task(
        program_assigned="task1.py",
        name="task.py",
        fragment_name="task1.py",
        task_children=[
            Task(
                program_assigned="task3.py",
                name="task3.py",
                fragment_name="task3.py",
                task_children=[
                    Task(
                        program_assigned="task4.py",
                        name="task4.py",
                        fragment_name="task4.py",
                    )
                ],
            )
        ],
    )
    

def main() -> int:
    main_runner = Main(worker_count=4, task=build_demo_task())
    report = _poll_and_print(main_runner, poll_interval=1.0, timeout=30.0)
    if not report["finished"]:
        for worker in main_runner.workers:
            worker.terminate()
    return int(report["return_code"] or 0)

if __name__ == "__main__":
    
    raise SystemExit(main())
