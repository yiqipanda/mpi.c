import socket
from contextlib import ExitStack
from message import Message

HOST = "127.0.0.1"
PORT = 5001
WORKER_IDS = {1, 2, 3}


def send_request(stream, worker_id, message):
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


def register_worker(server, stack, streams):
    connection, _ = server.accept()
    stack.enter_context(connection)
    connection.settimeout(10)
    stream = stack.enter_context(connection.makefile("rwb"))
    data = stream.readline()
    if not data:
        raise ConnectionError("Worker disconnected before registering")
    registration = Message.deserialize(data.decode())
    worker_id = registration.worker_id
    if registration.request_type != "register" or worker_id not in WORKER_IDS:
        raise ValueError(f"Invalid worker registration: {registration}")
    if worker_id in streams:
        raise ValueError(f"Worker {worker_id} registered twice")
    streams[worker_id] = stream
    acknowledgement = Message(worker_id=worker_id, request_type="OK", operation="register")
    stream.write((acknowledgement.serialize() + "\n").encode())
    stream.flush()


def main():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((HOST, PORT))
        server.listen(3)
        server.settimeout(10)

        sum_operations = {
            1: Message(request_id=0, request_type="createObject", operation="sum", parameters=[1, 2, 3, 4]),
            2: Message(request_id=1, request_type="createObject", operation="sum", parameters=[4, 5, 6, 7]),
            3: Message(request_id=2, request_type="createObject", operation="sum", parameters=[1, 2, 3, 5]),
        }
        with ExitStack() as stack:
            streams = {}
            for _ in WORKER_IDS:
                register_worker(server, stack, streams)
    
            for worker_id in sorted(WORKER_IDS):
                send_request(streams[worker_id], worker_id, sum_operations[worker_id])
            for worker_id in sorted(WORKER_IDS):
                result_request = sum_operations[worker_id].to_eval_request()
                if result_request is None:
                    continue
                result = send_request(streams[worker_id], worker_id, result_request).parameters[0]
                print(f"worker {worker_id} result is {result}")


if __name__ == "__main__":
    main()
