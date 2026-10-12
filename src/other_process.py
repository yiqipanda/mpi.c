import sys
import time
from collections.abc import Callable
from functools import wraps

from functions import Function
from log import Log
from log_buffer import LogBuffer
from message import Message
from network import otherNetwork

HOST = "127.0.0.1"
PORT = 5001
LOG_BUFFER = LogBuffer()

request_table: dict[int, Function] = {}
evaluation_status: dict[int, bool] = {}

"""Other process is specialized process in which by set of protocols helps main process accomplish a work/task to reduce execution time"""

""" Receives main process requests and provides a feedback"""


def log_execution(func: Callable[[int, Message], Message]) -> Callable[[int, Message], Message]:
    @wraps(func)
    def wrapper(worker_id: int, message: Message) -> Message:
        start = time.perf_counter()
        reply = func(worker_id, message)

        log = Log(time.time_ns(), f"worker {worker_id}", "")
        log.message_event(message)
        log.event += f"; reply={reply.request_type}; dispatch_elapsed={time.perf_counter() - start:.6f}s"
        if reply.request_type == "ERR":
            log.event += f"; error={reply.parameters!r}"

        LOG_BUFFER.append(log)
        return reply
    return wrapper

def command_getlogs(worker_id: int, message: Message) -> Message:
    if message.operation != "return" or message.parameters:
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Invalid log request"])
    entries = LOG_BUFFER.snapshot()
    return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="return", parameters=[entries])


def command_acklogs(worker_id: int, message: Message) -> Message:
    if message.operation != "flush" or message.parameters:
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Invalid log acknowledgment"])
    LOG_BUFFER.flush()
    return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="flush")


def command_createfunction(worker_id: int, message: Message) -> Message:
    if not (
        message.operation == "create"
        and len(message.parameters) == 1
        and isinstance(message.parameters[0], Function)
    ):
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Invalid function request"])
    request_table[message.request_id] = message.parameters[0]
    evaluation_status[message.request_id] = False
    return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="noop", parameters=[0])

def command_getfunctionresult(worker_id: int, message: Message) -> Message:
    function = request_table.get(message.request_id)
    if function is None:
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Request not created"])
    if not evaluation_status.get(message.request_id, False):
        return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Evaluation failed"])
    return Message(worker_id=worker_id, request_id=message.request_id, request_type="OK", operation="noop", parameters=[function])


@log_execution
def get_request(worker_id: int, message: Message) -> Message:
    if message.request_type == "getLogs":
        return command_getlogs(worker_id, message)
    if message.request_type == "ackLogs":
        return command_acklogs(worker_id, message)
    if message.request_type == "createFunction":
        return command_createfunction(worker_id, message)
    if message.request_type == "getFunctionResult":
        return command_getfunctionresult(worker_id, message)
    return Message(worker_id=worker_id, request_id=message.request_id, request_type="ERR", operation="noop", parameters=["Unknown request type"])

"""The method that stays in the long run."""
def main() -> None:
    worker_id = int(sys.argv[1])
    source = f"worker {worker_id}"
    LOG_BUFFER.append(Log(time.time_ns(), source, "process started"))
    network = otherNetwork(worker_id=worker_id, host=HOST, port=PORT, log_buffer=LOG_BUFFER, timeout=30)
    with network.connect() as connection, connection.makefile("rwb") as stream:
        LOG_BUFFER.append(Log(time.time_ns(), source, "connected to main process"))
        network.register(stream)
        LOG_BUFFER.append(Log(time.time_ns(), source, "registered with main process"))
        for data in stream:
            message = Message.deserialize(data.decode())
            LOG_BUFFER.append(Log(
                time.time_ns(), source,
                f"message received source=main dest={source} "
                f"request_type={message.request_type} operation={message.operation} "
                f"request_id={message.request_id}",
            ))
            reply = get_request(worker_id, message)
            stream.write((reply.serialize() + "\n").encode())
            stream.flush()
            error = f" error={reply.parameters!r}" if reply.request_type == "ERR" else ""
            LOG_BUFFER.append(Log(
                time.time_ns(), source,
                f"message sent source={source} dest=main "
                f"request_type={reply.request_type} operation={reply.operation} "
                f"request_id={reply.request_id}{error}",
            ))
            
            if message.request_type == "createFunction" and reply.request_type == "OK":
                execution_start = time.perf_counter()
                execution_error: Exception | None = None
                try:
                    evaluation_status[message.request_id] = request_table[message.request_id].eval()
                except Exception as exc:
                    evaluation_status[message.request_id] = False
                    execution_error = exc

                execution_elapsed = time.perf_counter() - execution_start
                log = Log(time.time_ns(), source, "")
                log.message_event(message)
                log.event += (
                    f"; function_execution={'OK' if evaluation_status[message.request_id] else 'ERR'}"
                    f"; elapsed={execution_elapsed:.6f}s"
                )
                if execution_error is not None:
                    log.event += f"; error={execution_error!r}"
                LOG_BUFFER.append(log)

if __name__ == "__main__":
    main()
