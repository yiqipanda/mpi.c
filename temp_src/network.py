import socket
import time
from collections.abc import Callable, Collection
from contextlib import ExitStack
from typing import BinaryIO

from message import Message

"""Huge difference in way main_process and other_process handles network operations, to encapsulate these two different network classes are implemented for this build. """

"""Helps main_process handle network operations"""
class MainNetwork:

    """The way init is implemented may change in the future to enchance modifications"""
    def __init__(
        self,
        server: socket.socket,
        host: str,
        port: int,
        timeout: float,
        n_listen: int,
        worker_ids: Collection[int],
    ) -> None:
        self.server = server
        self.host = host
        self.port = port
        self.timeout = timeout
        self.worker_ids = frozenset(worker_ids)
        self.streams: dict[int, BinaryIO] = {}
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((host, port))
        server.listen(n_listen)
        server.settimeout(timeout)


    def set_stream(self, worker_id: int, stream: BinaryIO) -> None:
        self.streams[worker_id] = stream

    def get_stream(self, worker_id: int) -> BinaryIO:
        return self.streams[worker_id]



    """Helps main process send request to other process, not broadcast"""
    def send_request(self, worker_id: int, message: Message) -> Message:
        stream = self.get_stream(worker_id)
        if not stream:
            raise RuntimeError(f"Worker {worker_id} is not registered.")
        stream.write((message.serialize() + "\n").encode())
        stream.flush()
        response = stream.readline()
        if not response:
            raise ConnectionError(f"Worker {worker_id} disconnected before replying")
        reply = Message.deserialize(response.decode())
        if reply.worker_id != worker_id or reply.request_id != message.request_id:
            raise RuntimeError(f"Unexpected reply from worker {worker_id}: {reply}")
        if reply.request_type != "OK":
            raise RuntimeError(f"Worker {worker_id} rejected request: {reply.parameters}")
        return reply

    """Registers other processes to main process table"""
    def register_worker(self, stack: ExitStack) -> int:
        connection, _ = self.server.accept()
        stack.enter_context(connection)
        connection.settimeout(self.timeout)
        stream = stack.enter_context(connection.makefile("rwb"))
        data = stream.readline()
        if not data:
            raise ConnectionError("Worker disconnected before registering")
        registration = Message.deserialize(data.decode())
        worker_id = registration.worker_id
        if registration.request_type != "register" or worker_id not in self.worker_ids:
            raise ValueError(f"Invalid worker registration: {registration}")
        if worker_id in self.streams:
            raise ValueError(f"Worker {worker_id} registered twice")
        self.set_stream(worker_id, stream)
        acknowledgement = Message(worker_id=worker_id, request_type="OK", operation="register")
        stream.write((acknowledgement.serialize() + "\n").encode())
        stream.flush()
        return worker_id


"""Helps other_process handle network operations"""
class otherNetwork:
    
    """The way init is implemented may change in the future to enchance modifications"""
    def __init__(
        self,
        worker_id: int,
        host: str,
        port: int,
        timeout: float = 10.0,
        retries: int = 50,
        retry_delay: float = 0.1,
    ) -> None:
        self.worker_id = worker_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay


    """Allows multiple connection creation retries."""
    def connect(self) -> socket.socket:
        for _ in range(self.retries):
            try:
                return socket.create_connection((self.host, self.port), timeout=self.timeout)
            except ConnectionRefusedError:
                time.sleep(self.retry_delay)
        raise RuntimeError("Could not connect to main_process.py")

    """Helps with registry of other process to main process"""
    def register(self, stream: BinaryIO) -> None:
        registration = Message(worker_id=self.worker_id, request_type="register", operation="noop")
        stream.write((registration.serialize() + "\n").encode())
        stream.flush()
        response = stream.readline()
        if not response:
            raise ConnectionError("Main process disconnected during registration")
        acknowledgement = Message.deserialize(response.decode())
        if (
            acknowledgement.request_type != "OK"
            or acknowledgement.operation != "register"
            or acknowledgement.worker_id != self.worker_id
            or acknowledgement.request_id != registration.request_id
        ):
            raise RuntimeError(f"Registration rejected: {acknowledgement}")
