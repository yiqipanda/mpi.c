import socket
from contextlib import ExitStack
from message import Message
from network import MainNetwork
import functions


HOST = "127.0.0.1"
PORT = 5001
WORKER_IDS = {1, 2, 3}

"""Main process is where we can use set of operations to distribute workload to other processes with prix fixe protocols.
Message objects are used in streams, for each connection there exists singular IOStream.
Registration is both ways with int ids to distinguish, no authentication implemented.
No fallbacks, test cases, traces implemented as of this build"""

def demo() -> None:
    """Not all network operations are encapsulated under network class to avoid coupling in IO ops"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        network = MainNetwork(server=server, port=PORT, host=HOST, timeout=30, n_listen=3, worker_ids=WORKER_IDS)


        #Initialization of the Function objects and encapsulation of them under Message objects"
        obj1 = functions.Sum(params=[1, 2, 3, 4])
        obj2, obj3 = obj1.partition()
        obj4 = functions.Sum(params=[0])
        sum_operations = {
            1: Message(request_id=0, request_type="createFunction", operation="create", parameters=[obj4]),
            2: Message(request_id=1, request_type="createFunction", operation="create", parameters=[obj2]),
            3: Message(request_id=2, request_type="createFunction", operation="create", parameters=[obj3]),
        }


        """ExitStack is used for handling multiple context managers better"""
        with ExitStack() as stack:

            for _ in WORKER_IDS:
                network.register_worker(stack)
    
            for worker_id in sorted(WORKER_IDS):
                network.send_request(worker_id=worker_id, message=sum_operations[worker_id])

            results: dict[int, functions.Sum] = {}
            for worker_id in sorted(WORKER_IDS):
                result_request = Message(
                    request_id=sum_operations[worker_id].request_id,
                    request_type="getFunctionResult",
                    operation="return",
                )
                result = network.send_request(worker_id=worker_id, message=result_request).parameters[0]
                if not isinstance(result, functions.Sum):
                    raise TypeError(f"Worker {worker_id} returned an unexpected result")
                results[worker_id] = result
                print(f"worker {worker_id} result is {result.result}")

            if not obj1.orchestrate([results[2], results[3]]):
                raise RuntimeError("Could not combine partition results")
            print(f"orchestrated result is {obj1.result}")


if __name__ == "__main__":
    demo()
