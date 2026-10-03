"""Interactive CLI for the manager-free resolution engine demo."""

from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import inspect
import json
from pathlib import Path
import sys
from threading import RLock
from typing import Any, TextIO
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.engine import CommandParse, Engine, EngineError
from src.functions import Function
from src.network import Network


class DemoError(RuntimeError):
    """A recoverable problem that should be shown as a terminal warning."""


HELP = """Commands:
  list processes
  create -p NUMBER
  create -fn CLASS_NAME -params PARAMETERS
  partition -id OBJECT_ID
  transfer -id OBJECT_ID PROCESS_ID
  eval -id OBJECT_ID
  orchestrate -id OBJECT_ID
  help
  exit
"""

#! We don't have to store partition_children separate from partitions if we treat them as equals and let partitions store their child_part_ids instead
#! We don't have to worry about how many times something is partitioned at all.
@dataclass
class DemoSystem:
    """Demo state coordinating local objects and TCP engines."""

    network: Network = field(default_factory=Network, repr=False)
    processes: dict[str, Engine] = field(default_factory=dict)
    objects: dict[str, Function] = field(default_factory=dict)
    partitions: dict[str, tuple[str, str]] = field(default_factory=dict)
    assignments: dict[str, str] = field(default_factory=dict)
    partition_children: set[str] = field(default_factory=set, repr=False)
    used_ids: set[str] = field(default_factory=set, repr=False)
    state_lock: Any = field(default_factory=RLock, repr=False)

    def new_id(self) -> str:
        for _ in range(0,10):
            candidate = uuid.uuid4().hex[:4]
            if candidate not in self.used_ids:
                self.used_ids.add(candidate)
                return candidate
        raise DemoError("could not allocate a unique id")


    #! Right now we abstract other computers as child processes, in the future there are other protocols on binding. This method is temporary.
    def create_processes(self, count: int) -> list[str]:
        if count < 1:
            raise DemoError("process count must be at least 1")
        process_ids: list[str] = []
        try:
            for _ in range(count):
                process_id = self.new_id()
                self.processes[process_id] = Engine(
                    process_id, network=self.network
                )
                process_ids.append(process_id)
        except Exception as exc:
            for process_id in process_ids:
                self.processes.pop(process_id).close()
                self.used_ids.discard(process_id)
            raise DemoError(f"could not create process: {exc}") from exc
        return process_ids


    #? It's unclear why we use state locks since we won't use child processes in future.
    def create_function(self, class_name: str, params: Any) -> str:
        self.state_lock.acquire()
        try:
            module = importlib.import_module("src.functions")
            function_class = getattr(module, class_name)
            if (
                not inspect.isclass(function_class)
                or function_class is Function
                or not issubclass(function_class, Function)
            ):
                raise DemoError(
                    f"{class_name} does not implement the Function contract"
                )
            function = function_class(params=params)
        #! We did not include function object initialization errors.
        except (ImportError, AttributeError) as exc:
            raise DemoError(f"unknown function class: {class_name}") from exc
        except DemoError:
            raise
        except Exception as exc:
            raise DemoError(f"could not create {class_name}: {exc}") from exc
        else:
            object_id = self.new_id()
            self.objects[object_id] = function
            return object_id
        finally:
            self.state_lock.release()


    #! This is not necessary. We not only 'check' but also create partition objects, it can be done fairly easily inside partition method.
    def check_partition(self, object_id: str) -> tuple[Function, Function]:
        """Run and validate the complete one-time partition contract."""

        function = self.object(object_id)
        if object_id in self.partitions or object_id in self.partition_children:
            raise DemoError(f"object {object_id} cannot be partitioned more than once")
        try:
            partitions = function.partition()
        except Exception as exc:
            raise DemoError(f"partition failed for object {object_id}: {exc}") from exc
        if not isinstance(partitions, (tuple, list)) or len(partitions) != 2:
            raise DemoError("partition() must return exactly two function objects")
        if any(not isinstance(partition, Function) for partition in partitions):
            raise DemoError("partition() returned an object without the Function contract")
        return partitions[0], partitions[1]


    #!Cleanly written.
    def partition(self, object_id: str) -> tuple[str, str]:
        left, right = self.check_partition(object_id)
        left_id, right_id = self.new_id(), self.new_id()
        self.objects[left_id], self.objects[right_id] = left, right
        self.partitions[object_id] = (left_id, right_id)
        self.partition_children.update((left_id, right_id))
        return left_id, right_id


    #!Cleanly written.
    def transfer(self, object_id: str, process_id: str) -> None:
        function = self.object(object_id)
        engine = self.process(process_id)
        try:
            engine.transfer(object_id, function)
        except EngineError as exc:
            raise DemoError(f"transfer failed: {exc}") from exc
        else:
            self.assignments[object_id] = process_id



    #! Future: objects may be reassigned intermittently.
    def evaluate(self, object_id: str) -> Any:
        function = self.object(object_id)
        process_id = self.assignments.get(object_id)
        if process_id is None:
            raise DemoError(f"object {object_id} has not been transferred to a process")
        try:
            result = self.process(process_id).evaluate(object_id)
        except EngineError as exc:
            raise DemoError(f"evaluation failed: {exc}") from exc
        else:
            function.result = result
            return result

    #! Future: objects may be moved to another place so we have to clarify the procedures or abstractions as to how it works.
    def orchestrate(self, object_id: str) -> Any:
        function = self.object(object_id)
        partition_ids = self.partitions.get(object_id)
        if partition_ids is None:
            raise DemoError(f"object {object_id} has not been partitioned")

        ordered_partitions = [self.objects[part_id] for part_id in partition_ids]
        try:
            succeeded = function.orchestrate(ordered_partitions)
        except Exception as exc:
            raise DemoError(f"orchestration failed for object {object_id}: {exc}") from exc
        if succeeded is not True:
            raise DemoError(
                f"orchestration failed for object {object_id}: "
                "one or more partitions have no result"
            )
        return function.result

    def list_processes(
        self,
    ) -> list[tuple[str, bool, tuple[str, ...], str | None]]:
        return [
            (
                process_id,
                engine.is_alive,
                tuple(sorted(engine.object_ids)),
                engine.evaluating_object_id,
            )
            for process_id, engine in self.processes.items()
        ]


    def object(self, object_id: str) -> Function:
        try:
            return self.objects[object_id]
        except KeyError as exc:
            raise DemoError(f"unknown object id: {object_id}") from exc


    def process(self, process_id: str) -> Engine:
        try:
            return self.processes[process_id]
        except KeyError as exc:
            raise DemoError(f"unknown process id: {process_id}") from exc


    def close(self) -> None:
        for engine in list(self.processes.values()):
            try:
                engine.close()
            except Exception:
                pass
        self.processes.clear()
        self.objects.clear()
        self.partitions.clear()
        self.assignments.clear()
        self.partition_children.clear()
        self.used_ids.clear()



def _display(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return repr(value)


def execute_command(system: DemoSystem, line: str, output: TextIO) -> bool:
    """Execute one parsed directive; return ``False`` to stop the shell."""

    payload = CommandParse.parse(line)
    if not payload.get("ok"):
        raise DemoError(payload.get("warning", "could not parse command"))

    directive = payload["directive"]
    arguments = payload["arguments"]
    if directive == "noop":
        return True
    if directive == "exit":
        return False
    if directive == "help":
        output.write(HELP)
        return True
    if directive == "list_processes":
        processes = system.list_processes()
        if not processes:
            output.write("(no processes)\n")
        for process_id, alive, object_ids, evaluating_object_id in processes:
            output.write(f"{process_id} {'running' if alive else 'stopped'}\n")
            if not object_ids:
                output.write("  objects: (none)\n")
            for object_id in object_ids:
                state = (
                    "evaluating"
                    if object_id == evaluating_object_id
                    else "idle"
                )
                output.write(f"  object {object_id}: {state}\n")
        return True
    if directive == "create_processes":
        for process_id in system.create_processes(arguments["count"]):
            output.write(f"process {process_id} created\n")
        return True
    if directive == "create_function":
        class_name = arguments["class_name"]
        object_id = system.create_function(class_name, arguments["params"])
        output.write(f"object {object_id} created ({class_name})\n")
        return True
    if directive == "partition":
        object_id = arguments["object_id"]
        left_id, right_id = system.partition(object_id)
        output.write(f"partitions {object_id}: {left_id} {right_id}\n")
        return True
    if directive == "transfer":
        system.transfer(arguments["object_id"], arguments["process_id"])
        output.write(
            f"object {arguments['object_id']} transferred to process "
            f"{arguments['process_id']}\n"
        )
        return True
    if directive == "eval":
        object_id = arguments["object_id"]
        output.write(f"result {object_id}: {_display(system.evaluate(object_id))}\n")
        return True
    if directive == "orchestrate":
        object_id = arguments["object_id"]
        output.write(f"result {object_id}: {_display(system.orchestrate(object_id))}\n")
        return True
    raise DemoError(f"unknown directive: {directive}")


#! Without relying on external libraries we have constructed CLI for the demo but it needs further examination.
def run(
    input_stream: TextIO = sys.stdin,
    output: TextIO = sys.stdout,
    error_output: TextIO | None = None,
) -> int:
    system = DemoSystem()
    interactive = input_stream.isatty()
    if error_output is None:
        error_output = sys.stderr
    try:
        if interactive:
            output.write("Resolution engine demo. Type 'help' for commands.\n")
        while True:
            if interactive:
                output.write("> ")
                output.flush()
            line = input_stream.readline()
            if not line:
                return 0
            try:
                if not execute_command(system, line, output):
                    return 0
            except DemoError as exc:
                print(f"warning: {exc}", file=error_output)
    finally:
        system.close()


if __name__ == "__main__":
    raise SystemExit(run())
