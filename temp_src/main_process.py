import socket
import time
from contextlib import ExitStack
from log import Log
from log_buffer import LogBuffer
from message import Message
from network import MainNetwork
from matrix import MatrixMultiplication

HOST = "127.0.0.1"
PORT = 5001
WORKER_IDS = {1, 2, 3}
MAIN_LOG = LogBuffer()

def collect_system_logs(network: MainNetwork, main_log: LogBuffer) -> LogBuffer:
    """Fetch worker snapshots, acknowledge receipt, and order all system events."""

    combined = LogBuffer()
    combined.merge(main_log.snapshot())
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
    return combined

"""Main process is where we can use set of operations to distribute workload to other processes with prix fixe protocols.
Message objects are used in streams, for each connection there exists singular IOStream.
Registration is both ways with int ids to distinguish, no authentication implemented.
No fallbacks, test cases, traces implemented as of this build"""

def demo() -> None:
    """Not all network operations are encapsulated under network class to avoid coupling in IO ops"""
    main_log = MAIN_LOG
    main_sequence = 0
    main_log.append(Log(time.time_ns(), "main", main_sequence, "process started"))
    main_sequence += 1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        network = MainNetwork(server=server, port=PORT, host=HOST, timeout=30, n_listen=3, worker_ids=WORKER_IDS)
        main_log.append(Log(time.time_ns(), "main", main_sequence, "listening for worker processes"))
        main_sequence += 1


        m1: list[list[int]] = [
            [1, 2, 3, 4],
            [5, 6, 7, 8],
            [9, 10, 11, 12],
            [13, 14, 15, 16],
        ]
        m2: list[list[int]] = [
            [0, 1, 2, 3],
            [4, 5, 6, 7],
            [8, 9, 10, 11],
            [12, 13, 14, 15],
        ]
        mm1 = MatrixMultiplication(params=[m1, m2])
        mm2, mm3 = mm1.partition()

        # Worker 1 evaluates the full product; workers 2 and 3 evaluate its partitions.
        matrix_operations = {
            1: Message(request_id=0, request_type="createFunction", operation="create", parameters=[mm1]),
            2: Message(request_id=1, request_type="createFunction", operation="create", parameters=[mm2]),
            3: Message(request_id=2, request_type="createFunction", operation="create", parameters=[mm3]),
        }

        """ExitStack is used for handling multiple context managers better"""
        with ExitStack() as stack:

            for _ in WORKER_IDS:
                worker_id = network.register_worker(stack)
                main_log.append(Log(time.time_ns(), "main", main_sequence, f"worker {worker_id} registered"))
                main_sequence += 1
    
            for worker_id in sorted(WORKER_IDS):
                network.send_request(worker_id=worker_id, message=matrix_operations[worker_id])

            results: dict[int, MatrixMultiplication] = {}
            for worker_id in sorted(WORKER_IDS):
                result_request = Message(
                    request_id=matrix_operations[worker_id].request_id,
                    request_type="getFunctionResult",
                    operation="return",
                )
                result = network.send_request(worker_id=worker_id, message=result_request).parameters[0]
                if not isinstance(result, MatrixMultiplication):
                    raise TypeError(f"Worker {worker_id} returned an unexpected result")
                results[worker_id] = result
                print(f"worker {worker_id} result is {result.result}")

            if not mm1.orchestrate([results[2], results[3]]):
                raise RuntimeError("Could not combine partition results")
            if mm1.result != results[1].result:
                raise RuntimeError("Partitioned matrix product differs from the full product")
            print(f"orchestrated result is {mm1.result}")

            print(collect_system_logs(network, main_log).export_syslog())


if __name__ == "__main__":
    demo()
