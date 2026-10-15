"""Run the existing matrix partition contract with Ray tasks.

Use ``--address local`` for a one-machine demo. On a Ray head node with
other machines joined to the cluster, use ``--address auto --require-remote``.
"""

import argparse
import socket
import time
from pathlib import Path

import ray

from matrix import MatrixMultiplication


@ray.remote(num_cpus=1)
def evaluate_partition(
    partition: MatrixMultiplication,
) -> tuple[MatrixMultiplication, str, str]:
    """Evaluate a partition and identify the node that ran it."""
    if not partition.eval():
        raise RuntimeError("Partition evaluation failed")
    return partition, ray.get_runtime_context().get_node_id(), socket.gethostname()


def make_matrices(size: int) -> tuple[list[list[int]], list[list[int]]]:
    left = [
        [row * size + column + 1 for column in range(size)]
        for row in range(size)
    ]
    right = [
        [row * size + column for column in range(size)]
        for row in range(size)
    ]
    return left, right


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        choices=("local", "auto"),
        default="local",
        help="Start a local Ray instance, or connect to an existing cluster",
    )
    parser.add_argument(
        "--size",
        type=int,
        default=15,
        help="Square matrix size (at least 2; default: 15)",
    )
    parser.add_argument(
        "--require-remote",
        action="store_true",
        help="Fail if any partition runs on the driver's node",
    )
    args = parser.parse_args()
    if args.size < 2:
        parser.error("--size must be at least 2")
    if args.require_remote and args.address == "local":
        parser.error("--require-remote requires --address auto")

    left, right = make_matrices(args.size)
    local_function = MatrixMultiplication(params=[left, right])
    local_start = time.perf_counter()
    if not local_function.eval():
        raise RuntimeError("Local matrix evaluation failed")
    local_elapsed = time.perf_counter() - local_start

    # Ray ships this source directory to task workers on other cluster nodes.
    ray.init(
        address=args.address,
        runtime_env={"working_dir": str(Path(__file__).resolve().parent)},
    )
    try:
        driver_node_id = ray.get_runtime_context().get_node_id()
        distributed_start = time.perf_counter()
        distributed_function = MatrixMultiplication(params=[left, right])
        partitions = distributed_function.partition()
        refs = [evaluate_partition.remote(partition) for partition in partitions]
        evaluations = ray.get(refs)
        results = [evaluation[0] for evaluation in evaluations]

        if not distributed_function.orchestrate(results):
            raise RuntimeError("Could not combine partition results")
        distributed_elapsed = time.perf_counter() - distributed_start

        if distributed_function.result != local_function.result:
            raise RuntimeError("Partitioned matrix product differs from local evaluation")
        if args.require_remote and any(
            node_id == driver_node_id for _, node_id, _ in evaluations
        ):
            raise RuntimeError("At least one partition ran on the driver's node")

        print(f"Matrix size: {args.size} x {args.size}")
        print(f"Local evaluation: {local_elapsed:.6f} s")
        print(f"Ray partitioned evaluation: {distributed_elapsed:.6f} s")
        print(f"Driver node ID: {driver_node_id}")
        for index, (_, node_id, hostname) in enumerate(evaluations, start=1):
            print(f"Partition {index}: node ID {node_id}, host {hostname}")
        print("Results match: yes")
        if args.require_remote:
            print("All partitions ran on a different node from the driver: yes")
    finally:
        ray.shutdown()


if __name__ == "__main__":
    main()
