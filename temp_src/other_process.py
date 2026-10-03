import socket
import sys
import time
from message import Message

HOST = "127.0.0.1"
PORT = 5001

global request_table 
request_table = {}

def render_request(worker_id, message):
    if message.request_type=="createObject":
        request_table[message.request_id]=message
        return Message(worker_id=worker_id, request_id = message.request_id,request_type="OK",operation="noop",parameters=[0])

    if message.request_type=="eval":
        if message.request_id in request_table:
            result = sum(request_table[message.request_id].parameters)
            return Message(worker_id=worker_id, request_id = message.request_id,request_type="OK",operation="noop",parameters=[result])
        else:
            return Message(worker_id=worker_id, request_id = message.request_id,request_type="ERR",operation="noop",parameters=["Request not created"])
def main():
    worker_id = int(sys.argv[1])

    # The launcher starts all processes together, so wait for the hub to bind.
    for _ in range(50):
        try:
            connection = socket.create_connection((HOST, PORT))
            break
        except ConnectionRefusedError:
            time.sleep(0.1)
    else:
        raise RuntimeError("Could not connect to main_process.py")

    with connection, connection.makefile("rwb") as stream:
        registration = Message(worker_id=worker_id, request_type="register", operation="noop")
        stream.write((registration.serialize() + "\n").encode())
        stream.flush()
        response = stream.readline()
        if not response:
            raise ConnectionError("Main process disconnected during registration")
        acknowledgement = Message.deserialize(response.decode())
        if acknowledgement.request_type != "OK" or acknowledgement.operation != "register" or acknowledgement.worker_id != worker_id:
            raise RuntimeError(f"Registration rejected: {acknowledgement}")

        for data in stream:
            message = Message.deserialize(data.decode())
            returnMessage = render_request(worker_id=worker_id,message=message)
            stream.write((returnMessage.serialize() + "\n").encode())
            stream.flush()

if __name__ == "__main__":
    main()
