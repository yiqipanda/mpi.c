import socket
import tempfile
import time
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path
from log import Log
from log_buffer import LogBuffer
from message import Message
from network import MainNetwork
from matrix import MatrixMultiplication

HOST = "127.0.0.1"
PORT = 5001
WORKER_IDS = {1, 2, 3}

def collect_system_logs(network: MainNetwork, main_log: LogBuffer) -> LogBuffer:

    """Fetch worker snapshots, acknowledge receipt, and order all system events."""

    combined = LogBuffer()
    acknowledgments: list[int] = []
    for worker_id in sorted(network.worker_ids):
        reply = network.send_request(
            worker_id,
            Message(request_id=-1, request_type="getLogs", operation="return"),
        )
        if (
            reply.operation != "return"
            or len(reply.parameters) != 1
            or not isinstance(reply.parameters[0], list)
        ):
            raise ValueError(f"Worker {worker_id} returned an invalid log snapshot")
        worker_entries = reply.parameters[0]
        if any(type(entry) is not Log or entry.source != f"worker {worker_id}" for entry in worker_entries):
            raise ValueError(f"Worker {worker_id} returned invalid log entries")
        combined.merge(worker_entries)
        acknowledgments.append(worker_id)

    for worker_id in acknowledgments:
        reply = network.send_request(
            worker_id,
            Message(
                request_id=-1,
                request_type="ackLogs",
                operation="flush",
            ),
        )
        if reply.operation != "flush":
            raise ValueError(f"Worker {worker_id} returned an invalid log acknowledgment")
    combined.merge(main_log.snapshot())
    return combined

"""Main process is where we can use set of operations to distribute workload to other processes with prix fixe protocols.
Message objects are used in streams, for each connection there exists singular IOStream.
Registration is both ways with int ids to distinguish, no authentication implemented.
No fallbacks, test cases, traces implemented as of this build"""

def demo() -> None:
    """Compare local evaluation with partitioned evaluation of the same matrix."""
    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    main_log = LogBuffer()
    main_log.append(Log(time.time_ns(), "main", "process started"))
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server, ExitStack() as stack:
        network = MainNetwork(server=server, port=PORT, host=HOST, timeout=30, n_listen=3, worker_ids=WORKER_IDS, log_buffer=main_log)
        main_log.append(Log(time.time_ns(), "main", "listening for worker processes"))

        for _ in WORKER_IDS:
            worker_id = network.register_worker(stack)
            main_log.append(Log(time.time_ns(), "main", f"worker {worker_id} registered"))

        size = 15
        m1: list[list[int]] = [
            [row * size + column + 1 for column in range(size)]
            for row in range(size)
        ]
        m2: list[list[int]] = [
            [row * size + column for column in range(size)]
            for row in range(size)
        ]

        local_start = time.perf_counter()
        local_function = MatrixMultiplication(params=[m1, m2])
        if not local_function.eval():
            raise RuntimeError("Local matrix evaluation failed")
        local_elapsed = time.perf_counter() - local_start

        # The launcher starts three workers. Keep the unused one connected
        # during the second timed path without adding work to that measurement.
        network.send_request(
            worker_id=3,
            message=Message(request_type="ackLogs", operation="flush"),
        )

        distributed_start = time.perf_counter()
        distributed_function = MatrixMultiplication(params=[m1, m2])
        left_partition, right_partition = distributed_function.partition()
        matrix_operations = {
            1: Message(request_id=0, request_type="createFunction", operation="create", parameters=[left_partition]),
            2: Message(request_id=1, request_type="createFunction", operation="create", parameters=[right_partition]),
        }

        for worker_id in (1, 2):
            network.send_request(worker_id=worker_id, message=matrix_operations[worker_id])

        results: dict[int, MatrixMultiplication] = {}
        for worker_id in (1, 2):
            result_request = Message(
                request_id=matrix_operations[worker_id].request_id,
                request_type="getFunctionResult",
                operation="return",
            )
            result = network.send_request(worker_id=worker_id, message=result_request).parameters[0]
            if not isinstance(result, MatrixMultiplication):
                raise TypeError(f"Worker {worker_id} returned an unexpected result")
            results[worker_id] = result

        if not distributed_function.orchestrate([results[1], results[2]]):
            raise RuntimeError("Could not combine partition results")
        distributed_elapsed = time.perf_counter() - distributed_start

        if distributed_function.result != local_function.result:
            raise RuntimeError("Partitioned matrix product differs from local evaluation")

        print(f"Matrix size: {size} x {size}")
        print(f"Local object creation + evaluation: {local_elapsed:.6f} s")
        print(f"Distributed creation through orchestration: {distributed_elapsed:.6f} s")
        print(f"Local / distributed time ratio: {local_elapsed / distributed_elapsed:.3f}x")
        print("Results match: yes")

        syslog = collect_system_logs(network, main_log).export_syslog()
        log_directory = Path(__file__).resolve().parents[1] / "logs"
        log_directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            prefix=f"syslog-{run_timestamp}-",
            suffix=".log",
            dir=log_directory,
            delete=False,
        ) as log_file:
            log_file.write(f"{syslog}\n")
            log_path = Path(log_file.name)
        print(f"Syslog: {log_path}")


if __name__ == "__main__":
    demo()
