import sys
import time
from functions import Function
from log import Log
from log_buffer import LogBuffer
from message import Message
from network import otherNetwork

HOST = "127.0.0.1"
PORT = 5001

request_table: dict[int, Function] = {}
evaluation_status: dict[int, bool] = {}

"""Other process is specialized process in which by set of protocols helps main process accomplish a work/task to reduce execution time"""

""" Receives main process requests and provides a feedback"""
def get_request(
    worker_id: int, message: Message, log_buffer: LogBuffer | None = None
) -> Message:
    if message.request_type in {"getLogs", "ackLogs"} and log_buffer is None:
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Log buffer is unavailable"])

    if message.request_type == "getLogs":
        if message.operation != "return" or message.parameters:
            return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Invalid log request"])
        entries = log_buffer.snapshot()
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="return", parameters=[entries])

    if message.request_type == "ackLogs":
        if (
            message.operation != "flush"
            or message.parameters
        ):
            return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Invalid log acknowledgment"])
        log_buffer.flush()
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="flush")

    if message.request_type == "createFunction":
        if not (
            message.operation == "create"
            and len(message.parameters) == 1
            and isinstance(message.parameters[0], Function)
        ):
            return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Invalid function request"])
        request_table[message.request_id] = message.parameters[0]
        evaluation_status[message.request_id] = False
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="noop", parameters=[0])

    if message.request_type == "getFunctionResult":
        function = request_table.get(message.request_id)
        if function is None:
            return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Request not created"])
        if not evaluation_status.get(message.request_id, False):
            return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Evaluation failed"])
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="noop", parameters=[function])

    return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Unknown request type"])

"""The method that stays in the long run."""
def main() -> None:
    worker_id = int(sys.argv[1])
    log_buffer = LogBuffer()
    source = f"worker {worker_id}"
    log_buffer.append(Log(time.time_ns(), source, 0, "process started"))
    network = otherNetwork(worker_id=worker_id, host=HOST, port=PORT, timeout=30)
    with network.connect() as connection, connection.makefile("rwb") as stream:
        log_buffer.append(Log(time.time_ns(), source, 1, "connected to main process"))
        network.register(stream)
        log_buffer.append(Log(time.time_ns(), source, 2, "registered with main process"))
        for data in stream:
            message = Message.deserialize(data.decode())
            reply = get_request(worker_id, message, log_buffer)
            stream.write((reply.serialize() + "\n").encode())
            stream.flush()
            
            #For evaluating a function object right after receiving it, can be encapsulated under a method in future.
            if message.request_type == "createFunction" and reply.request_type == "OK":
                try:
                    evaluation_status[message.request_id] = request_table[message.request_id].eval()
                except Exception:
                    evaluation_status[message.request_id] = False

if __name__ == "__main__":
    main()
