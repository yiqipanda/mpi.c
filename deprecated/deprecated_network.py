"""Reusable, authenticated TCP transport with role permissions and failover.

Only this module's explicit start/connect methods open sockets or start threads.
Authentication is mutual and connection-scoped; it does not encrypt traffic or
sign subsequent frames. Use this transport on trusted networks (or provide a
separately secured network path). It does not deserialize application objects.

Each peer configures the other peer's identity, shared secret, and trusted role:

    worker = Network(
        identity="worker-1",
        credentials=[PeerCredential("manager", b"a-shared-secret", "manager")],
        authorization=AuthorizationPolicy({"manager": {"evaluate"}}),
    )
    worker.register_handler(
        "evaluate", lambda peer, operation, metadata, payload: ({}, payload)
    )
    worker.start_server()  # status()["listener"] contains the assigned port

    manager = Network(
        identity="manager",
        credentials=[PeerCredential("worker-1", b"a-shared-secret", "worker")],
    )
    manager.register_endpoint(Endpoint(
        "worker", "127.0.0.1", worker.status()["listener"]["port"], "worker-1"
    ))
    result = manager.request("worker", "evaluate", payload=b"binary data")
    manager.close()
    worker.close()

Callbacks receive PeerIdentity (never secrets), an operation string, application
metadata, and bytes, and return (metadata, bytes). Long-running callbacks cannot
be forcibly stopped; they should cooperate with their owner's shutdown policy.
Retrying on another endpoint never migrates application state or guarantees
exactly-once execution. The caller must identify suitable alternative endpoints.
"""

# Reading guide for engineers who have not worked with sockets:
# - A socket is an OS handle for sending/receiving bytes between programs.
# - A host identifies a machine; a port identifies a service on that machine.
# - A listener accepts new connections; each accepted connection has its own socket.
# - "Client" and "server" describe who connects and who accepts, not fixed business
#   roles. A manager or worker can act as either, and Network can do both at once.
# - TCP delivers an ordered stream of bytes, retransmitting lost network packets.
#   It does not know where an application message ends, or whether a task executed.
# - A frame is our message boundary within that stream. Metadata describes the
#   message; the payload is the application data, kept as uninterpreted bytes.
# - Authentication establishes who the peer is. Authorization decides what that
#   authenticated peer may do. Neither one encrypts subsequent application data.
# - A probe is a small request that checks a service is answering. Failover means
#   trying another registered endpoint after a qualifying transport failure.
#
# Python conventions used throughout this file:
# - Leading '_' marks an internal helper by convention, not access enforcement.
# - '__init__' constructs an object; '__post_init__' validates generated dataclass
#   construction; '__enter__'/'__exit__' support cleanup with a 'with' statement.
# - Type annotations describe intended inputs/outputs. 'Any' leaves a value's type
#   open; 'T | None' permits no value. Explicit checks enforce only selected types.
# - Mapping means dictionary-like; Iterable means a sequence or other iterable;
#   Callable means a function/method supplied by the caller (a callback).
# - '*' in a signature makes following arguments keyword-only. '**options' collects
#   named arguments, and '**extra' expands a mapping into another mapping.
# - @staticmethod needs no instance; a @dataclass generates constructor/equality
#   methods. frozen=True prevents ordinary field reassignment, not nested mutation.
# - 'with lock' acquires/releases a lock automatically; 'finally' runs cleanup even
#   after an exception or return. 'raise ... from exc' preserves the original cause.
# - Seconds are used for timeouts; byte counts are used for frame sizes. A deadline
#   is an absolute monotonic-clock value, rather than a fresh timeout for each step.
# Defer annotation evaluation so types can refer to classes defined later.
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
import errno
import hashlib
import hmac
import json
import math
import queue
import secrets
import socket
import struct
import threading
import time
from typing import Any
import uuid


# The supported names exported by 'from src.network import *'. Other names remain
# accessible, but consumers should rely on this list rather than internal helpers.
__all__ = [
    "Endpoint", "PeerCredential", "PeerIdentity", "NetworkConfig",
    "Authenticator", "AuthorizationPolicy", "FrameCodec", "EndpointRouter",
    "HealthMonitor", "Network", "NetworkError", "AuthenticationError",
    "ProtocolError",
]

# This labels our application message format, not the operating system's TCP version.
PROTOCOL_VERSION = 1
# Only transport failures qualify for failover; permission or application errors
# must be returned to the caller rather than retried on a different server.
# frozenset is an immutable set: fast membership checks without accidental edits.
_TRANSPORT_ERRORS = frozenset({"connection_refused", "timeout", "disconnected", "unreachable"})
# Authentication, application traffic, and health probes share the same framing.
_FRAME_KINDS = frozenset({
    "auth_hello", "auth_challenge", "auth_proof", "auth_ok", "refusal",
    "request", "response", "ping", "pong",
})


class NetworkError(RuntimeError):
    """Low-level failure; public Network methods turn it into a result payload."""

    # Store a machine-readable category alongside the human-readable exception text.
    # Low-level components raise this; the Network facade normally returns result dicts.
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class AuthenticationError(NetworkError):
    # Use one category for unknown identities, invalid challenges, and bad proofs.
    # The caller receives a rejection, not the secret or expected proof value.
    def __init__(self, message: str):
        super().__init__("authentication_rejected", message)


class ProtocolError(NetworkError):
    # A protocol error means the peer's message does not follow the agreed format,
    # independently of whether its TCP connection was established successfully.
    def __init__(self, message: str):
        super().__init__("protocol_failure", message)


# Input: an exception from the OS socket API. Output: our stable failure category.
# errno holds the numeric reason; named errno constants avoid platform-specific numbers.
def _socket_error(exc: OSError) -> NetworkError:
    # Convert OS-specific exceptions into stable codes used by routing and status.
    if isinstance(exc, TimeoutError):
        return NetworkError("timeout", "network deadline expired")
    if exc.errno == errno.ECONNREFUSED:
        return NetworkError("connection_refused", "endpoint refused the connection")
    if exc.errno in {errno.ECONNRESET, errno.EPIPE, errno.ENOTCONN, errno.EBADF}:
        return NetworkError("disconnected", "connection was closed or reset")
    return NetworkError("unreachable", str(exc))


# Return seconds left before an absolute deadline, or raise once the budget expires.
# Call this before every blocking stage instead of granting a new full timeout each time.
def _remaining(deadline: float) -> float:
    # Monotonic time keeps elapsed-time budgets independent of wall-clock changes.
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise NetworkError("timeout", "network deadline expired")
    return remaining


# Release a socket safely, including a partially initialized or already-closed one.
# shutdown(SHUT_RDWR) disables both receiving and sending; close() releases the handle.
def _close_socket(connection: socket.socket | None) -> None:
    if connection is not None:
        try:
            # Wake threads blocked in recv() before releasing the socket handle.
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            # A disconnected or already-closed socket still needs close() cleanup.
            pass
        connection.close()


@dataclass(frozen=True)
class Endpoint:
    # The routing ID names an address; the expected identity authenticates whoever
    # answers there. These names need not match.
    id: str
    host: str  # IP address or hostname of the service to connect to.
    port: int  # Remote listening port; zero is not a usable destination.
    expected_peer_identity: str  # Must match the identity proven during authentication.

    # Called automatically after the dataclass constructor. Catch invalid destination
    # configuration immediately; registering an Endpoint does not yet connect to it.
    def __post_init__(self) -> None:
        if not self.id or not self.host or not self.expected_peer_identity:
            raise ValueError("endpoint ID, host, and peer identity must be nonempty")
        if not isinstance(self.port, int) or not 1 <= self.port <= 65535:
            raise ValueError("endpoint port must be between 1 and 65535")


@dataclass(frozen=True)
class PeerIdentity:
    # This is the credential-free view passed to policies and application handlers.
    identity: str  # Logical peer name, such as "worker-1"; not its IP address.
    role: str  # Locally trusted permission group, such as "worker" or "manager".


@dataclass(frozen=True)
class PeerCredential:
    identity: str
    # Keep secrets out of dataclass repr() output and ordinary diagnostic logs.
    secret: bytes = field(repr=False)  # bytes holds raw secret material, not text encoding.
    # Roles come from local configuration, never from a peer's transmitted claims.
    role: str

    # Validate a trust entry at construction. Both peers must be provisioned with
    # the same pairwise secret; this module does not distribute or generate credentials.
    def __post_init__(self) -> None:
        if not self.identity or not self.role:
            raise ValueError("peer identity and role must be nonempty")
        if not isinstance(self.secret, bytes) or not self.secret:
            raise ValueError("shared secret must be nonempty bytes")


@dataclass(frozen=True)
class NetworkConfig:
    host: str = "127.0.0.1"  # Loopback: accept connections from this machine by default.
    # Port zero is valid for listeners: bind() selects a free port atomically.
    port: int = 0
    connect_timeout: float = 3.0  # Connection/authentication budget; also bounds a health probe.
    request_timeout: float = 30.0  # Whole client exchange budget and partial-frame I/O budget.
    monitoring_interval: float = 5.0  # Pause after each full pass through registered endpoints.
    max_frame_bytes: int = 16 * 1024 * 1024  # 16 MiB per body; not an entire stream-size limit.
    max_connections: int = 32  # Accepted sessions, including authentication and health probes.
    shutdown_timeout: float = 2.0  # Shared wait budget for joining managed threads per close().
    unhealthy_threshold: int = 2  # Consecutive transport failures before normal routing skips a peer.

    # Reject impossible limits before starting sockets/threads. A finite timeout cannot
    # be infinity or NaN; the hexadecimal frame bound is the largest unsigned 32-bit integer.
    def __post_init__(self) -> None:
        if not self.host or not 0 <= self.port <= 65535:
            raise ValueError("invalid listener address")
        for name in ("connect_timeout", "request_timeout", "monitoring_interval", "shutdown_timeout"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if not 256 <= self.max_frame_bytes <= 0xFFFFFFFF:
            # The wire format stores lengths in unsigned four-byte integers.
            raise ValueError("max_frame_bytes must be between 256 and 2**32 - 1")
        if self.max_connections < 1 or self.unhealthy_threshold < 1:
            raise ValueError("connection limit and unhealthy threshold must be positive")


class FrameCodec:
    """Four-byte body length + four-byte metadata length + JSON + binary bytes.

    The body length includes its metadata-length prefix. receive() can wait
    indefinitely for the first byte when deadline is omitted, but the remainder
    of that frame must arrive within io_timeout. Partial-frame errors require
    closing the socket rather than trying to interpret the following bytes.
    """

    # Wire layout (lengths count bytes, not text characters):
    # [4-byte body length][4-byte metadata length][UTF-8 JSON metadata][binary payload]
    # A 10-byte metadata block plus a 3-byte payload has body length 4 + 10 + 3 = 17;
    # the complete transmitted frame is 21 bytes including its outer length prefix.
    # UTF-8 converts text to bytes; a character can occupy more than one byte.
    # Request metadata carries kind, version, request_id, operation, and nested
    # application metadata. Authentication/probe frames use their own kind-specific fields.

    # Configure framing limits only; constructing a codec neither connects nor binds.
    def __init__(self, max_frame_bytes: int = 16 * 1024 * 1024, io_timeout: float = 30.0):
        if (not 256 <= max_frame_bytes <= 0xFFFFFFFF
                or not math.isfinite(io_timeout) or io_timeout <= 0):
            raise ValueError("invalid frame limits")
        self.max_frame_bytes = max_frame_bytes
        self.io_timeout = io_timeout

    # Build and return one complete frame in memory without writing to a socket.
    # Empty bytes (b"") are a valid payload. Unsupported metadata or size raises ProtocolError.
    def encode(self, metadata: Mapping[str, Any], payload: bytes = b"") -> bytes:
        if not isinstance(metadata, Mapping) or not isinstance(payload, bytes):
            raise ProtocolError("frame requires mapping metadata and bytes payload")
        # Work on a copy so inserting the protocol version cannot mutate caller data.
        header = dict(metadata)
        header.setdefault("version", PROTOCOL_VERSION)
        # bool is a subclass of int in Python; an exact type check prevents True from
        # being accepted as protocol version 1. The message kind must be a known string.
        if (type(header["version"]) is not int or header["version"] != PROTOCOL_VERSION
                or not isinstance(header.get("kind"), str) or header["kind"] not in _FRAME_KINDS):
            raise ProtocolError("invalid frame version or kind")
        try:
            # Only metadata is JSON-encoded; binary data retains its original bytes.
            # Compact separators remove unnecessary whitespace. allow_nan=False rejects
            # nonstandard JSON floating values rather than sending ambiguous metadata.
            encoded = json.dumps(header, separators=(",", ":"), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
            raise ProtocolError("frame metadata must be JSON-serializable") from exc
        # Body length counts the metadata-length field but excludes its own prefix.
        size = 4 + len(encoded) + len(payload)
        if size > self.max_frame_bytes:
            raise ProtocolError("frame exceeds the configured size limit")
        # '!' gives both length fields the same big-endian representation on all hosts.
        # struct.pack converts numbers into fixed-width bytes; each 'I' is a four-byte
        # unsigned integer. Big-endian stores the most significant byte first.
        return struct.pack("!II", size, len(encoded)) + encoded + payload

    # Convenience path: validate/encode metadata and bytes, then transmit the frame.
    # The supplied connection must already exist; the codec does not authenticate it.
    def send(self, connection: socket.socket, metadata: Mapping[str, Any],
             payload: bytes = b"", *, deadline: float | None = None) -> None:
        self.send_encoded(connection, self.encode(metadata, payload), deadline=deadline)

    # Transmit a frame previously produced by encode(); do not re-encode it. This lets
    # request() validate once and reuse the same bytes across permitted failover attempts.
    def send_encoded(self, connection: socket.socket, frame: bytes,
                     *, deadline: float | None = None) -> None:
        if deadline is None:
            deadline = time.monotonic() + self.io_timeout
        try:
            connection.settimeout(_remaining(deadline))
            # sendall() handles partial writes; a failure does not reveal how much
            # data reached the peer, so higher layers track possible delivery.
            connection.sendall(frame)
        except OSError as exc:
            raise _socket_error(exc) from exc

    @staticmethod
    def _read(connection: socket.socket, size: int, deadline: float | None) -> bytes:
        # Read exactly 'size' bytes. bytearray permits efficient accumulation; the final
        # bytes conversion gives callers an immutable result. Size zero returns empty bytes.
        data = bytearray()
        # TCP preserves byte order, not message boundaries; recv() may return only
        # part of a field. Keep the same deadline across every partial read.
        while len(data) < size:
            try:
                connection.settimeout(None if deadline is None else _remaining(deadline))
                chunk = connection.recv(size - len(data))
            except OSError as exc:
                raise _socket_error(exc) from exc
            if not chunk:
                # recv() returning b"" means end-of-stream, not "try again later".
                raise NetworkError("disconnected", "connection closed before the frame completed")
            data.extend(chunk)
        return bytes(data)

    # Parse one complete frame and return (metadata_dict, payload_bytes). Authentication
    # and request-specific field checks happen above this layer. Read failures raise;
    # the owner must close the connection because resuming mid-frame is unsafe.
    def receive(self, connection: socket.socket, *, deadline: float | None = None
                ) -> tuple[dict[str, Any], bytes]:
        # Wait for the first byte separately so idle server connections can stay open.
        first = self._read(connection, 1, deadline)
        if deadline is None:
            deadline = time.monotonic() + self.io_timeout
        size = struct.unpack("!I", first + self._read(connection, 3, deadline))[0]
        # unpack reverses pack and returns a tuple; [0] selects the single decoded length.
        # Validate advertised lengths before accepting a potentially large body.
        if size < 4 or size > self.max_frame_bytes:
            raise ProtocolError("invalid frame length")
        metadata_size = struct.unpack("!I", self._read(connection, 4, deadline))[0]
        # The metadata cannot overlap the payload or extend outside the advertised body.
        if metadata_size > size - 4:
            raise ProtocolError("metadata length exceeds frame length")
        try:
            metadata = json.loads(self._read(connection, metadata_size, deadline).decode("utf-8"))
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise ProtocolError("invalid JSON frame metadata") from exc
        if (not isinstance(metadata, dict) or type(metadata.get("version")) is not int
                or metadata["version"] != PROTOCOL_VERSION or not isinstance(metadata.get("kind"), str)
                or metadata["kind"] not in _FRAME_KINDS):
            raise ProtocolError("invalid frame version or kind")
        # Consuming exactly this body leaves the next frame untouched in the socket.
        payload = self._read(connection, size - 4 - metadata_size, deadline)
        return metadata, payload


class Authenticator:
    """Mutual challenge authentication, with locally assigned peer roles.

    Each pair of peers must configure the same secret for the other identity.
    Proofs cover both identities and both nonces with different direction labels,
    preventing old transcripts and reflected proofs from authenticating a new
    connection. No peer-supplied role is trusted.
    """

    # Handshake order:
    # client -> server: auth_hello(identity, fresh client nonce)
    # server -> client: auth_challenge(identity, fresh server nonce, server proof)
    # client -> server: auth_proof(client proof)
    # server -> client: auth_ok, after accepting the client's proof
    # A nonce is a random value used to make this connection's proof unique.
    # HMAC is a hash calculated with a secret key: possession of the secret allows
    # proof generation. SHA-256 yields 32 bytes, represented here as 64 hex characters.
    # Proofs verify the handshake, not subsequent request bytes. Application traffic
    # still requires a trusted/secured network path when interception is a concern.

    # Build the peer credential lookup from caller configuration; no authentication I/O yet.
    def __init__(self, identity: str, credentials: Iterable[PeerCredential]):
        if not identity:
            raise ValueError("local identity must be nonempty")
        self.identity = identity
        # This allowlist supplies both the shared secret and authoritative peer role.
        self.credentials: dict[str, PeerCredential] = {}
        for credential in credentials:
            if credential.identity in self.credentials:
                raise ValueError(f"duplicate credential identity: {credential.identity}")
            self.credentials[credential.identity] = credential

    # Retrieve the locally provisioned trust entry; an unknown name is rejected even
    # if the remote side claims a plausible role or reaches the correct TCP port.
    def _credential(self, identity: Any) -> PeerCredential:
        if not isinstance(identity, str) or identity not in self.credentials:
            raise AuthenticationError("unknown peer identity")
        return self.credentials[identity]

    @staticmethod
    def _proof(secret: bytes, direction: str, transcript: list[str]) -> str:
        # Pure computation: return the expected hex digest without network activity.
        # A transcript contains client ID, server ID, client nonce, then server nonce.
        # Both sides encode the transcript identically. Direction labels prevent
        # a server's proof from being reused as the client's proof.
        message = json.dumps([PROTOCOL_VERSION, direction, *transcript], separators=(",", ":")).encode()
        return hmac.new(secret, message, hashlib.sha256).hexdigest()

    @staticmethod
    def _nonce(value: Any) -> str:
        # Check incoming nonce shape and return it unchanged; never generate randomness here.
        # Two hexadecimal characters represent one byte, hence 64 characters for 32 bytes.
        if not isinstance(value, str) or len(value) != 64:
            raise AuthenticationError("invalid authentication nonce")
        try:
            if len(bytes.fromhex(value)) != 32:
                raise ValueError("nonce must contain 32 bytes")
        except ValueError as exc:
            raise AuthenticationError("invalid authentication nonce") from exc
        return value

    @staticmethod
    def _verify(expected: str, provided: Any) -> None:
        # Return nothing on a valid proof, raise AuthenticationError otherwise. Constant-time
        # comparison reduces information exposed through matching-prefix timing differences.
        # Check shape before constant-time comparison; never compare secrets/proofs
        # with ordinary string equality.
        if (not isinstance(provided, str) or len(provided) != 64
                or not provided.isascii() or not hmac.compare_digest(expected, provided)):
            raise AuthenticationError("peer proof was rejected")

    @staticmethod
    def _receive(codec: FrameCodec, connection: socket.socket, kind: str,
                 deadline: float) -> dict[str, Any]:
        # Read the next handshake step, reject refusals/wrong order, and return its fields.
        # Handshake messages carry metadata only, so any binary payload is rejected.
        metadata, payload = codec.receive(connection, deadline=deadline)
        if metadata["kind"] == "refusal":
            # Capacity refusals are transport failures; credential refusals must
            # stop authentication rather than triggering automatic failover.
            code = metadata.get("code", "authentication_rejected")
            if code == "connection_refused":
                raise NetworkError(code, "server connection limit reached")
            raise AuthenticationError("peer rejected the connection")
        if metadata["kind"] != kind or payload:
            raise AuthenticationError("unexpected authentication message")
        return metadata

    # Initiator side of the handshake: prove our identity and verify the configured server.
    # Return the server's trusted PeerIdentity; leave the socket open for later requests.
    def authenticate_client(self, connection: socket.socket, expected_peer: str,
                            codec: FrameCodec, deadline: float) -> PeerIdentity:
        credential = self._credential(expected_peer)
        # A fresh client nonce binds the server's proof to this particular connection.
        client_nonce = secrets.token_hex(32)
        codec.send(connection, {"kind": "auth_hello", "identity": self.identity,
                               "nonce": client_nonce}, deadline=deadline)
        challenge = self._receive(codec, connection, "auth_challenge", deadline)
        if challenge.get("identity") != expected_peer:
            raise AuthenticationError("unexpected server identity")
        server_nonce = self._nonce(challenge.get("nonce"))
        transcript = [self.identity, expected_peer, client_nonce, server_nonce]
        # Verify the expected server before sending the client's proof. Both proofs
        # cover both identities and both nonces without transmitting the secret.
        self._verify(self._proof(credential.secret, "server", transcript), challenge.get("proof"))
        codec.send(connection, {"kind": "auth_proof", "proof": self._proof(
            credential.secret, "client", transcript)}, deadline=deadline)
        self._receive(codec, connection, "auth_ok", deadline)
        # The authenticated connection can now carry many requests without rehashing.
        return PeerIdentity(credential.identity, credential.role)

    # Acceptor side: verify the connecting peer and prove this server's own identity.
    # Return the locally configured client identity/role only after its proof succeeds.
    def authenticate_server(self, connection: socket.socket, codec: FrameCodec,
                            deadline: float) -> PeerIdentity:
        hello = self._receive(codec, connection, "auth_hello", deadline)
        credential = self._credential(hello.get("identity"))
        client_nonce = self._nonce(hello.get("nonce"))
        # A fresh server nonce invalidates a recorded client proof from an old session,
        # even if an attacker repeats the old hello message.
        server_nonce = secrets.token_hex(32)
        transcript = [credential.identity, self.identity, client_nonce, server_nonce]
        codec.send(connection, {"kind": "auth_challenge", "identity": self.identity,
                               "nonce": server_nonce, "proof": self._proof(
                                   credential.secret, "server", transcript)}, deadline=deadline)
        proof = self._receive(codec, connection, "auth_proof", deadline)
        self._verify(self._proof(credential.secret, "client", transcript), proof.get("proof"))
        # Acknowledge only after proof verification; permissions are checked later
        # for each operation using this locally configured role.
        codec.send(connection, {"kind": "auth_ok"}, deadline=deadline)
        return PeerIdentity(credential.identity, credential.role)


class AuthorizationPolicy:
    """Default-deny role permissions plus an optional custom policy.

    When supplied, callback(peer, operation, metadata) replaces the role lookup
    and must return True to permit a request. Explicit '*' allows all operations.
    Authenticated transport health probes bypass application permissions.
    """

    # Example table: {"manager": {"evaluate"}, "worker": {"report"}}. A role groups
    # permissions, while an identity names a specific peer. Sets make operation checks fast.
    # A callback can instead implement identity-specific or metadata-dependent rules.
    def __init__(self, permissions: Mapping[str, Iterable[str]] | None = None,
                 callback: Callable[[PeerIdentity, str, Mapping[str, Any]], bool] | None = None):
        self.permissions = {role: frozenset(operations) for role, operations in (permissions or {}).items()}
        self.callback = callback

    # Answer one permission question without sending bytes or executing the operation.
    # Policy callback exceptions propagate to _handle(), which returns application_failure.
    def allows(self, peer: PeerIdentity, operation: str, metadata: Mapping[str, Any]) -> bool:
        if self.callback is not None:
            # Custom policies replace the role table and must explicitly return True.
            return self.callback(peer, operation, metadata) is True
        allowed = self.permissions.get(peer.role, frozenset())
        # Missing roles and unlisted operations are denied by default.
        return operation in allowed or "*" in allowed


class EndpointRouter:
    """Thread-safe registry, ordered routes, and endpoint health snapshots."""

    # Build an empty registry. A route is a named ordered list of alternatives,
    # not an IP forwarding table; this router selects application service endpoints.
    def __init__(self, unhealthy_threshold: int = 2):
        if unhealthy_threshold < 1:
            raise ValueError("unhealthy threshold must be positive")
        self.unhealthy_threshold = unhealthy_threshold
        self._endpoints: dict[str, Endpoint] = {}
        self._routes: dict[str, tuple[str, ...]] = {}
        self._health: dict[str, dict[str, Any]] = {}
        # status() may call endpoint() while already holding the router lock.
        # RLock is reentrant: the same thread can acquire it more than once. Other
        # threads still wait, preventing simultaneous edits/reads of shared records.
        self._lock = threading.RLock()

    # Add a destination and initialize its cached health. This does not establish a
    # connection; unknown health means "not observed yet", not "known to be broken".
    def register_endpoint(self, endpoint: Endpoint) -> None:
        with self._lock:
            if endpoint.id in self._endpoints or endpoint.id in self._routes:
                raise ValueError(f"endpoint or route already exists: {endpoint.id}")
            self._endpoints[endpoint.id] = endpoint
            # None means no probe has established reachability/authentication yet.
            # Status fields:
            # state: unknown, healthy, degraded, unhealthy, or rejected.
            # reachable/authenticated: latest observation, not a guarantee about the future.
            # last_check_time: Unix timestamp in seconds; None before any observation.
            # latency_ms: elapsed time for the reported operation, not just cable latency.
            # consecutive_failures: transport failures counted since the last success.
            # last_error: most recent failure text; successful observations clear it.
            self._health[endpoint.id] = {
                "endpoint_id": endpoint.id, "host": endpoint.host, "port": endpoint.port,
                "state": "unknown", "reachable": None, "authenticated": None,
                "last_check_time": None, "latency_ms": None,
                "consecutive_failures": 0, "last_error": None,
            }

    # Create or replace a named route using already registered endpoint IDs. For example,
    # "workers" -> ("primary", "backup") preserves primary-first preference. Duplicate
    # entries are rejected to avoid inadvertently repeating a request at the same address.
    def register_route(self, name: str, endpoint_ids: Iterable[str]) -> None:
        # Preserve caller order: the first eligible endpoint is the preferred route.
        ids = tuple(endpoint_ids)
        with self._lock:
            if not name or name in self._endpoints:
                raise ValueError("route name must be nonempty and distinct from endpoints")
            if not ids or len(ids) != len(set(ids)) or any(i not in self._endpoints for i in ids):
                raise ValueError("route requires distinct registered endpoint IDs")
            self._routes[name] = ids

    # Resolve exactly one endpoint ID. Unknown IDs raise a structured low-level error;
    # this method does not interpret route names or filter an endpoint by its health.
    def endpoint(self, endpoint_id: str) -> Endpoint:
        with self._lock:
            try:
                return self._endpoints[endpoint_id]
            except KeyError as exc:
                raise NetworkError("route_unavailable", f"unknown endpoint: {endpoint_id}") from exc

    # Resolve an endpoint ID or route name into eligible destinations, in caller order.
    # Return an empty list when unknown/all excluded. Selection does not connect, probe,
    # balance load, or copy application state to the returned destinations.
    def candidates(self, target: str) -> list[Endpoint]:
        with self._lock:
            if target in self._endpoints:
                ids = (target,)
            else:
                ids = self._routes.get(target, ())
            # Unknown/degraded endpoints remain eligible; repeated transport failures
            # exclude unhealthy ones until a successful check restores their health.
            return [self._endpoints[i] for i in ids if self._health[i]["state"] != "unhealthy"]

    # Snapshot the full endpoint registry for monitoring. A new list avoids iterating
    # the live registry while another thread registers additional destinations.
    def endpoints(self) -> list[Endpoint]:
        with self._lock:
            # Include unhealthy endpoints so the monitor can detect their recovery.
            return list(self._endpoints.values())

    # Apply one observation to an endpoint's health, then return a copy of that record.
    # Healthy/degraded/unhealthy are routing observations, not process-exit information.
    # An authenticated success resets the transport failure count. Application rejection
    # is normally recorded as transport success by request(), not as endpoint failure.
    def record(self, endpoint_id: str, *, code: str = "ok", warning: str | None = None,
               latency_ms: float | None = None) -> dict[str, Any]:
        with self._lock:
            health = self._health[endpoint_id]
            # Wall-clock timestamps are for reporting; operation deadlines use monotonic time.
            health.update(last_check_time=time.time(), latency_ms=latency_ms, last_error=warning)
            if code == "ok":
                health.update(state="healthy", reachable=True, authenticated=True, consecutive_failures=0)
            elif code in _TRANSPORT_ERRORS:
                # One failure degrades health; crossing the threshold removes the
                # endpoint from normal routing without deleting its registration.
                health["consecutive_failures"] += 1
                health.update(reachable=False, authenticated=False)
                health["state"] = ("unhealthy" if health["consecutive_failures"] >= self.unhealthy_threshold
                                   else "degraded")
            elif code == "authentication_rejected":
                # A responding peer can be reachable yet fail authentication.
                health.update(state="rejected", reachable=True, authenticated=False)
            else:
                health.update(state="degraded", reachable=True)
            return dict(health)

    # With an ID return one health dict; with None return {endpoint_id: health_dict}.
    # No socket operation occurs. Nested validation calls are safe because of RLock.
    def status(self, endpoint_id: str | None = None) -> Any:
        # Return copies so callers cannot mutate shared health records accidentally.
        with self._lock:
            if endpoint_id is not None:
                self.endpoint(endpoint_id)
                return dict(self._health[endpoint_id])
            return {key: dict(value) for key, value in self._health.items()}


class HealthMonitor:
    """Optional background probes, including endpoints excluded from routing."""

    # Inject two callbacks: check(endpoint_id) performs a probe and endpoints() supplies
    # the current registry. These callbacks keep this component independent of Network.
    # Construction stores configuration only; call start() to create the background thread.
    def __init__(self, check: Callable[[str], Any], endpoints: Callable[[], list[Endpoint]],
                 interval: float = 5.0):
        if interval <= 0:
            raise ValueError("monitor interval must be positive")
        self._check, self._endpoints, self.interval = check, endpoints, interval
        # Event is a thread-safe stop flag that also supports interruptible waiting.
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # Start one monitoring loop, or do nothing if it is already running. daemon=True
    # means the thread alone cannot keep Python alive; explicit stop/close still cleans up.
    def start(self) -> None:
        with self._lock:
            # Repeated starts reuse the monitor rather than spawning duplicate loops.
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="network-health", daemon=True)
            self._thread.start()

    # Background thread entry point: probe a registry snapshot, then pause for interval.
    # The interval is a pause between sweeps, not an exact per-endpoint sampling period.
    # Network's check callback records results/events; injected callbacks should handle
    # their expected failures since an unexpected exception exits this loop.
    def _run(self) -> None:
        while not self._stop.is_set():
            # Probes are sequential and independently bounded by their network deadline.
            for endpoint in self._endpoints():
                if self._stop.is_set():
                    return
                self._check(endpoint.id)
            # Event.wait() makes the interval interruptible during shutdown.
            self._stop.wait(self.interval)

    # Signal the loop to stop, then wait up to timeout seconds for its thread to finish.
    # join() waits; it does not kill a thread or cancel a callback already executing.
    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout)


@dataclass
class _ClientConnection:
    socket: socket.socket | None = None
    # One exchange owns both send and receive so concurrent callers cannot consume
    # each other's replies on the same persistent connection.
    # default_factory calls threading.Lock for every new instance, avoiding one shared
    # lock across all endpoints. Any reflects the runtime lock implementation type.
    lock: Any = field(default_factory=threading.Lock)


# A type alias for application callbacks; this line describes a signature, not a function.
# Arguments: authenticated peer, operation name, application metadata, payload bytes.
# Return value: (response metadata mapping, response payload bytes). Network handles I/O.
Handler = Callable[[PeerIdentity, str, dict[str, Any], bytes], tuple[Mapping[str, Any], bytes]]


class Network:
    """Reusable facade with structured operational results and explicit lifecycle.

    connect() targets an endpoint. request() accepts an endpoint or ordered route
    name. retry_safe must be explicitly True to resend after possible delivery.
    Each request has a single overall deadline, shared by all failover attempts.
    status() reports cached health; check_connection() actively authenticates and
    pings a new socket. poll() retrieves events without probing or consuming replies.
    """

    # Assemble components and shared state for a local peer. The identity is the name
    # other peers expect to authenticate; credentials describe peers we trust. Server and
    # monitoring startup are separate methods, so constructing Network performs no I/O.
    def __init__(self, identity: str, credentials: Iterable[PeerCredential] = (),
                 authorization: AuthorizationPolicy | None = None,
                 config: NetworkConfig | None = None, *,
                 authenticator: Authenticator | None = None,
                 router: EndpointRouter | None = None):
        self.identity = identity
        self.config = config or NetworkConfig()
        # Callers can replace authentication/routing while retaining the facade API.
        self.authenticator = authenticator or Authenticator(identity, credentials)
        if self.authenticator.identity != identity:
            raise ValueError("authenticator identity differs from Network identity")
        self.authorization = authorization or AuthorizationPolicy()
        self.router = router or EndpointRouter(self.config.unhealthy_threshold)
        self.codec = FrameCodec(self.config.max_frame_bytes, self.config.request_timeout)
        self._handlers: dict[str, Handler] = {}
        # Outbound connections persist between requests, one slot per endpoint.
        self._clients: dict[str, _ClientConnection] = {}
        # Track inbound, outbound, and probe sockets together so close() wakes them all.
        self._sockets: set[socket.socket] = set()
        self._threads: set[threading.Thread] = set()
        # The listener accepts clients; connected sockets carry messages. Keep its handle
        # separate, and retain its last address so status can describe a closed listener.
        self._listener: socket.socket | None = None
        self._listener_address: tuple[str, int] | None = None
        self._lock = threading.RLock()
        # Closing is terminal for this instance; initialize a new Network to restart.
        self._stop = threading.Event()
        # Limit accepted connections independently of the listener's OS backlog.
        # A semaphore is a pool of permits: acquire takes one, release returns one.
        # BoundedSemaphore also catches accidental releases beyond the configured limit.
        self._connection_slots = threading.BoundedSemaphore(self.config.max_connections)
        # A bounded event queue prevents an unattended monitor from growing memory forever.
        self._events: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1024)
        self.monitor = HealthMonitor(self.check_connection, self.router.endpoints,
                                     self.config.monitoring_interval)

    @staticmethod
    def _result(*, code: str = "ok", warning: str | None = None,
                endpoint_id: str | None = None, request_id: str | None = None,
                attempts: list[dict[str, Any]] | None = None,
                delivery_state: str = "not_sent", **extra: Any) -> dict[str, Any]:
        # Operational failures are data: consumers choose how to display or handle them.
        # Delivery state describes the application request, not handshake/probe traffic.
        # Result fields:
        # ok/code/warning: boolean outcome, stable category, and optional explanation.
        # endpoint_id: selected destination; None when no endpoint was selected.
        # request_id: correlation ID for the exchange; None for non-request operations.
        # attempts: ordered per-endpoint outcomes from this call, not a cumulative log.
        # delivery_state: not_sent (no application send began), possibly_sent (execution
        #   is uncertain), or delivered (received a matching response, even if it denied
        #   the operation). "delivered" does not guarantee exactly-once execution.
        # Extra fields can include metadata/payload, decoded JSON data, or status.
        # Common failure codes: connection_refused, unreachable, timeout, disconnected,
        # authentication_rejected, permission_denied, protocol_failure, application_failure,
        # route_unavailable, invalid_payload, bind_failed, busy (local contention), closed.
        return {"ok": code == "ok", "code": code, "warning": warning,
                "endpoint_id": endpoint_id, "request_id": request_id,
                "attempts": attempts or [], "delivery_state": delivery_state, **extra}

    # Publish a local lifecycle/health event for poll(). Events are notifications rather
    # than messages sent to a peer; dropped old events make this a recent history, not
    # a durable audit log. All producers can call it without waiting for a consumer.
    def _event(self, kind: str, **data: Any) -> None:
        event = {"event": kind, "time": time.time(), **data}
        try:
            self._events.put_nowait(event)
        except queue.Full:
            # Retain recent events without blocking networking threads. Another producer
            # may race this eviction, so both empty/full outcomes are harmless here.
            try:
                self._events.get_nowait()
            except queue.Empty:
                pass
            try:
                self._events.put_nowait(event)
            except queue.Full:
                pass

    # Public wrapper for destination registration. Stores the address/expected identity;
    # actual connection and authentication happen on connect(), request(), or a probe.
    def register_endpoint(self, endpoint: Endpoint) -> None:
        self.router.register_endpoint(endpoint)

    # Public wrapper defining a preferred destination and alternatives for request().
    # Callers must ensure alternatives can handle the task and have necessary state.
    def register_route(self, name: str, endpoint_ids: Iterable[str]) -> None:
        self.router.register_route(name, endpoint_ids)

    # Register or replace a local server callback. An operation name is application
    # vocabulary (e.g. "evaluate"), not a shell command. A registered handler still
    # requires authorization, and may run concurrently on different client connections.
    def register_handler(self, operation: str, handler: Handler) -> None:
        if not operation or not callable(handler):
            raise ValueError("handler requires a nonempty operation and callable")
        with self._lock:
            self._handlers[operation] = handler

    # Add an active socket to centralized cleanup under the lifecycle lock, or close it
    # and raise if shutdown already began. Includes sockets still authenticating.
    def _track_socket(self, connection: socket.socket) -> None:
        with self._lock:
            # Shutdown may race a connect/accept; do not register sockets after close().
            if self._stop.is_set():
                _close_socket(connection)
                raise NetworkError("closed", "network is closed")
            self._sockets.add(connection)

    # Remove a socket from tracking and release it. discard() tolerates a previous
    # removal by concurrent shutdown; passing None makes failed initialization safe.
    def _drop_socket(self, connection: socket.socket | None) -> None:
        if connection is not None:
            with self._lock:
                self._sockets.discard(connection)
            _close_socket(connection)

    # Bridge health storage to event delivery, returning the updated endpoint snapshot.
    # This is local bookkeeping after an observation, not another network probe.
    def _record(self, endpoint_id: str, code: str = "ok", warning: str | None = None,
                latency_ms: float | None = None) -> dict[str, Any]:
        # Update the cached snapshot and publish the same observation to poll() consumers.
        health = self.router.record(endpoint_id, code=code, warning=warning, latency_ms=latency_ms)
        self._event("health", status=health)
        return health

    # Bind this peer's configured address and start accepting in a background thread.
    # Return a result dict with listener.host/port/listening. Repeated calls reuse the
    # existing listener; bind failures are returned without shutting down the Network.
    def start_server(self) -> dict[str, Any]:
        with self._lock:
            if self._stop.is_set():
                return self._result(code="closed", warning="network is closed")
            if self._listener is not None:
                return self._result(listener=self.status()["listener"])
            # AF_INET selects IPv4; SOCK_STREAM selects an ordered TCP byte stream.
            # Creating a socket allocates a local handle but has not bound an address yet.
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                # Allow rebinding after a previous listener exits; this does not bypass
                # bind() failures when another live listener already owns the address.
                listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                # bind() claims the local host/port. listen() enables incoming connections;
                # its backlog controls the OS queue, not the number of active handler threads.
                listener.bind((self.config.host, self.config.port))
                listener.listen(self.config.max_connections)
                # Periodically wake accept() to observe shutdown even without new clients.
                listener.settimeout(0.2)
            except OSError as exc:
                listener.close()
                return self._result(code="bind_failed", warning=str(exc))
            self._listener = listener
            # getsockname() exposes the actual port when configuration requested port zero.
            self._listener_address = listener.getsockname()
            thread = threading.Thread(target=self._accept_loop, args=(listener,),
                                      name=f"network-listener-{self.identity}", daemon=True)
            self._threads.add(thread)
            thread.start()
            self._event("listening", listener=self.status()["listener"])
            return self._result(listener=self.status()["listener"])

    # Listener thread entry point. Accept a new socket for each client and hand it to a
    # separate bounded handler thread. This loop itself never executes application tasks.
    def _accept_loop(self, listener: socket.socket) -> None:
        try:
            while not self._stop.is_set():
                try:
                    # accept() returns (connected socket, remote address). '_' intentionally
                    # ignores the address: an IP alone does not establish trusted identity.
                    connection, _ = listener.accept()
                except TimeoutError:
                    # This timeout is an idle polling tick, not a client failure.
                    continue
                except OSError as exc:
                    if not self._stop.is_set():
                        self._event("listener_failure", warning=str(exc))
                    return
                if not self._connection_slots.acquire(blocking=False):
                    # blocking=False returns immediately instead of waiting for capacity.
                    # Refuse excess clients promptly, before authentication or application
                    # execution. Keep the refusal send bounded so accepting can continue.
                    try:
                        self.codec.send(connection, {"kind": "refusal", "code": "connection_refused"},
                                        deadline=time.monotonic() + 0.2)
                    except NetworkError:
                        pass
                    finally:
                        _close_socket(connection)
                    self._event("connection_refused", warning="connection limit reached")
                    continue
                try:
                    self._track_socket(connection)
                    thread = threading.Thread(target=self._serve_connection, args=(connection,),
                                              name=f"network-peer-{self.identity}", daemon=True)
                    # target is the callable the new thread runs. args=(connection,) is a
                    # one-element tuple; without the comma it would not be an argument tuple.
                    with self._lock:
                        # Register and start under the same lock so shutdown can join every
                        # thread it sees without racing a not-yet-started thread.
                        self._threads.add(thread)
                        thread.start()
                except NetworkError:
                    self._drop_socket(connection)
                    self._connection_slots.release()
        finally:
            with self._lock:
                self._threads.discard(threading.current_thread())

    # Own one accepted connection until disconnect/shutdown: authenticate it, repeatedly
    # receive requests, send correlated replies, then release its socket and capacity.
    # Requests on this connection run sequentially; other connections have other threads.
    def _serve_connection(self, connection: socket.socket) -> None:
        peer: PeerIdentity | None = None
        try:
            try:
                # Authenticate exactly once; no application request is processed first.
                peer = self.authenticator.authenticate_server(
                    connection, self.codec, time.monotonic() + self.config.connect_timeout)
            except AuthenticationError:
                self.codec.send(connection, {"kind": "refusal", "code": "authentication_rejected"})
                raise
            self._event("connected", peer_identity=peer.identity, role=peer.role)
            while not self._stop.is_set():
                # The codec bounds partial frames while permitting fully idle connections.
                metadata, payload = self.codec.receive(connection)
                kind = metadata["kind"]
                request_id = metadata.get("request_id")
                # Correlation IDs pair a reply with its request; they do not provide server
                # deduplication or prevent execution of a deliberately repeated request.
                if not isinstance(request_id, str) or not request_id:
                    raise ProtocolError("request ID must be a nonempty string")
                if kind == "ping":
                    # Authenticated liveness checks do not require an application permission
                    # or call a handler, allowing roles with no allowed operations to probe.
                    self.codec.send(connection, {"kind": "pong", "request_id": request_id})
                    # "ping" asks "are you responding?"; "pong" confirms this request ID.
                    continue
                if kind != "request":
                    raise ProtocolError("expected request or health probe")
                operation, application_metadata = metadata.get("operation"), metadata.get("metadata", {})
                if not isinstance(operation, str) or not operation or not isinstance(application_metadata, dict):
                    raise ProtocolError("invalid operation or application metadata")
                # Application metadata is nested separately from protocol control fields.
                response, response_payload = self._handle(peer, operation, application_metadata, payload)
                # Echo the ID so the client can reject stale or unrelated responses.
                response.update(kind="response", request_id=request_id)
                self.codec.send(connection, response, response_payload)
        except NetworkError as exc:
            # Do not attempt to resume parsing after a broken frame: byte alignment may
            # be lost. Ordinary permission/handler failures instead travel as responses.
            if not self._stop.is_set():
                self._event("connection_closed", peer_identity=peer.identity if peer else None,
                            code=exc.code, warning=str(exc))
        finally:
            self._drop_socket(connection)
            # Release capacity regardless of authentication, parsing, or handler outcome.
            self._connection_slots.release()
            with self._lock:
                self._threads.discard(threading.current_thread())

    # Server application boundary: authorize, look up the callback, execute it, and
    # validate its output. Returns (protocol response fields, binary bytes), not a socket
    # result dict; _serve_connection adds the correlation fields and sends the response.
    def _handle(self, peer: PeerIdentity, operation: str, metadata: dict[str, Any],
                payload: bytes) -> tuple[dict[str, Any], bytes]:
        try:
            # Handler existence alone never grants permission to invoke it.
            if not self.authorization.allows(peer, operation, metadata):
                return {"ok": False, "code": "permission_denied", "warning": "operation is not permitted"}, b""
            with self._lock:
                handler = self._handlers.get(operation)
            if handler is None:
                return {"ok": False, "code": "application_failure", "warning": "unknown operation"}, b""
            # Run user code outside the registry lock so it cannot block unrelated
            # registrations or health probes while doing application work.
            response_metadata, response_payload = handler(peer, operation, metadata, payload)
            if not isinstance(response_metadata, Mapping):
                raise TypeError("handler response metadata must be a mapping")
            response = {"ok": True, "code": "ok", "warning": None, "metadata": dict(response_metadata)}
            # Validate callback output before sending any part of its response.
            # Locally generated request IDs are 32 characters; use that size here.
            self.codec.encode({**response, "kind": "response", "request_id": "0" * 32}, response_payload)
            return response, response_payload
        except Exception as exc:
            # Application exceptions become failure responses; the connection stays usable.
            self._event("handler_failure", operation=operation, warning=str(exc))
            return {"ok": False, "code": "application_failure", "warning": f"{type(exc).__name__}: {exc}"}, b""

    # Establish and authenticate one outbound TCP connection to exactly this endpoint.
    # Return a tracked, open socket on success; close partial connections on any failure.
    # No application request has been sent by this method, so failures precede task delivery.
    def _open(self, endpoint: Endpoint, deadline: float) -> socket.socket:
        if self._stop.is_set():
            raise NetworkError("closed", "network is closed")
        connection: socket.socket | None = None
        try:
            # create_connection resolves hostnames if needed and establishes TCP. DNS
            # resolution is performed by the OS and may not obey the socket timeout.
            connection = socket.create_connection((endpoint.host, endpoint.port), timeout=_remaining(deadline))
            # Track the socket before authentication so shutdown can interrupt a stalled peer.
            self._track_socket(connection)
            self.authenticator.authenticate_client(connection, endpoint.expected_peer_identity, self.codec, deadline)
            return connection
        except OSError as exc:
            self._drop_socket(connection)
            raise _socket_error(exc) from exc
        except Exception:
            self._drop_socket(connection)
            raise

    # Fetch/create the socket+lock container for one destination. setdefault returns the
    # existing entry when present. The outer registry lock keeps concurrent creation safe.
    def _slot(self, endpoint_id: str) -> _ClientConnection:
        with self._lock:
            # Create the per-endpoint lock even before establishing its first connection.
            return self._clients.setdefault(endpoint_id, _ClientConnection())

    # Open a persistent authenticated connection now rather than waiting for request().
    # Takes an endpoint ID, not a route name. Returns a structured result with status;
    # cached connections are reused, so use check_connection() for an actual fresh probe.
    def connect(self, endpoint_id: str) -> dict[str, Any]:
        if self._stop.is_set():
            return self._result(code="closed", warning="network is closed", endpoint_id=endpoint_id)
        started = time.monotonic()
        deadline = started + self.config.connect_timeout
        try:
            endpoint = self.router.endpoint(endpoint_id)
            slot = self._slot(endpoint_id)
            # Waiting for another caller to release the same connection consumes this
            # call's budget; failure means local contention, not a refused remote connection.
            if not slot.lock.acquire(timeout=_remaining(deadline)):
                raise NetworkError("busy", "connection is busy")
            try:
                if slot.socket is None:
                    slot.socket = self._open(endpoint, deadline)
            finally:
                slot.lock.release()
            # connect() can reuse an existing socket; check_connection() performs an
            # independent active probe when the caller needs a fresh liveness observation.
            health = self._record(endpoint_id, latency_ms=(time.monotonic() - started) * 1000)
            return self._result(endpoint_id=endpoint_id, status=health)
        except NetworkError as exc:
            # Waiting for a busy local connection is not evidence of endpoint ill health.
            if exc.code not in {"route_unavailable", "busy", "closed"}:
                self._record(endpoint_id, exc.code, str(exc))
            return self._result(code=exc.code, warning=str(exc), endpoint_id=endpoint_id)

    # Blocking client exchange: select an endpoint/route, send bytes, and wait for a reply.
    # metadata is a separate application dictionary; payload is already serialized by the
    # caller. The result contains response metadata/bytes or a structured failure.
    # retry_safe=True permits another execution after uncertain delivery; use it only when
    # duplicates are acceptable (often called an idempotent operation). The module does
    # not deduplicate, cancel timed-out remote work, or guarantee exactly-once execution.
    def request(self, target: str, operation: str, payload: bytes = b"",
                metadata: Mapping[str, Any] | None = None, *, retry_safe: bool = False,
                timeout: float | None = None) -> dict[str, Any]:
        # UUID4 supplies a random correlation identifier, rendered as 32 hex characters.
        # All attempts for this one call reuse the ID; each new call gets a fresh one.
        request_id = uuid.uuid4().hex
        if self._stop.is_set():
            return self._result(code="closed", warning="network is closed", request_id=request_id)
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("request timeout must be positive and finite")
        if type(retry_safe) is not bool:
            raise ValueError("retry_safe must be a boolean")
        if not isinstance(operation, str) or not operation:
            raise ValueError("operation must be a nonempty string")
        # Queueing, connecting, authenticating, and every failover attempt share this
        # deadline. Trying more endpoints must not multiply the caller's time budget.
        deadline = time.monotonic() + (timeout if timeout is not None else self.config.request_timeout)
        try:
            # Validate and encode before opening/sending so invalid input is never delivered.
            if metadata is not None and not isinstance(metadata, Mapping):
                raise ProtocolError("application metadata must be a mapping")
            frame = self.codec.encode({"kind": "request", "request_id": request_id,
                                       "operation": operation, "metadata": dict(metadata) if metadata is not None else {}}, payload)
        except ProtocolError as exc:
            return self._result(code="invalid_payload", warning=str(exc), request_id=request_id)
        candidates = self.router.candidates(target)
        if not candidates:
            return self._result(code="route_unavailable", warning="no healthy route candidates", request_id=request_id)
        attempts: list[dict[str, Any]] = []
        # Preserve uncertainty across attempts: a later connection refusal cannot undo
        # possible execution on an earlier endpoint.
        ever_sent = False
        for endpoint in candidates:
            sent = False
            started = time.monotonic()
            slot = self._slot(endpoint.id)
            acquired = False
            try:
                # Own the socket for the entire send-and-receive exchange, not just the
                # write. Otherwise another thread could accidentally consume our reply.
                acquired = slot.lock.acquire(timeout=_remaining(deadline))
                if not acquired:
                    raise NetworkError("busy", "connection is busy")
                if slot.socket is None:
                    # Each handshake has its own cap, within the overall request deadline.
                    slot.socket = self._open(endpoint, min(deadline, time.monotonic() + self.config.connect_timeout))
                # From this point, bytes may reach the peer even if sendall fails.
                _remaining(deadline)
                sent = ever_sent = True
                self.codec.send_encoded(slot.socket, frame, deadline=deadline)
                reply, response_payload = self.codec.receive(slot.socket, deadline=deadline)
                # Correlate replies and reject contradictory status fields rather than
                # returning another request's response or trusting malformed peer data.
                if (reply["kind"] != "response" or reply.get("request_id") != request_id
                        or not isinstance(reply.get("ok"), bool) or not isinstance(reply.get("code"), str)
                        or reply["ok"] != (reply["code"] == "ok")):
                    raise ProtocolError("invalid or mismatched response")
                if reply["ok"] and (reply["code"] != "ok" or not isinstance(reply.get("metadata"), dict)):
                    raise ProtocolError("invalid successful response")
                # A valid application failure still proves the authenticated transport works.
                self._record(endpoint.id, latency_ms=(time.monotonic() - started) * 1000)
                attempts.append({"endpoint_id": endpoint.id, "code": reply["code"], "delivery_state": "delivered"})
                return self._result(code=reply["code"], warning=reply.get("warning"), endpoint_id=endpoint.id,
                                    request_id=request_id, attempts=attempts, delivery_state="delivered",
                                    metadata=reply.get("metadata", {}), payload=response_payload)
            except NetworkError as exc:
                if acquired:
                    # Discard uncertain connections so a delayed reply cannot be mistaken
                    # for the next request's response.
                    self._drop_socket(slot.socket)
                    slot.socket = None
                if exc.code not in {"busy", "closed"}:
                    self._record(endpoint.id, exc.code, str(exc))
                attempts.append({"endpoint_id": endpoint.id, "code": exc.code,
                                 "delivery_state": "possibly_sent" if sent else "not_sent"})
                result = self._result(code=exc.code, warning=str(exc), endpoint_id=endpoint.id,
                                      request_id=request_id, attempts=attempts,
                                      delivery_state="possibly_sent" if ever_sent else "not_sent")
                if exc.code not in _TRANSPORT_ERRORS or (sent and not retry_safe):
                    # Fail over only on transport errors, and only with caller permission
                    # once sending started. Authentication/protocol failures stop here.
                    return result
            finally:
                # Release only if this call acquired the lock; another request may own it.
                if acquired:
                    slot.lock.release()
        # Candidates are exhausted. Keep the last failure plus the complete attempt list.
        return result

    # Adapter for callers using dictionaries: serialize to UTF-8 JSON, use request(),
    # and decode a successful JSON object into result['data']. The raw result['payload']
    # remains available. **options forwards metadata, retry_safe, timeout, etc. to request().
    def request_json(self, target: str, operation: str, data: Mapping[str, Any],
                     **options: Any) -> dict[str, Any]:
        """Send an application dictionary as UTF-8 JSON and decode its reply."""
        try:
            if not isinstance(data, Mapping):
                raise ValueError("JSON application data must be a mapping")
            encoded = json.dumps(dict(data), allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            return self._result(code="invalid_payload", warning=str(exc))
        result = self.request(target, operation, payload=encoded, **options)
        if result["ok"]:
            # JSON decoding is an application-format check after delivery, so failures
            # here keep the request's endpoint, attempts, and delivery state intact.
            try:
                decoded = json.loads(result["payload"].decode("utf-8"))
                if not isinstance(decoded, dict):
                    raise ValueError("JSON response must be an object")
                result["data"] = decoded
            except (ValueError, UnicodeError) as exc:
                result.update(ok=False, code="invalid_payload", warning=str(exc))
        return result

    # Register a dictionary-oriented callback instead of a bytes-oriented callback.
    # The wrapper performs decoding/encoding; authentication and permissions follow the
    # same path as any other handler. Decode/callback errors become application_failure.
    def register_json_handler(self, operation: str,
                              handler: Callable[[PeerIdentity, str, dict[str, Any], dict[str, Any]], Mapping[str, Any]]) -> None:
        # Adapt dictionaries at the application boundary while the transport stays binary.
        # This inner function captures 'handler' (a closure), allowing the registered
        # callback to remember which application function should receive decoded data.
        def wrapped(peer: PeerIdentity, name: str, metadata: dict[str, Any], payload: bytes
                    ) -> tuple[dict[str, Any], bytes]:
            data = json.loads(payload.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("JSON request must be an object")
            response = handler(peer, name, metadata, data)
            if not isinstance(response, Mapping):
                raise ValueError("JSON handler response must be a mapping")
            return {}, json.dumps(dict(response), allow_nan=False).encode("utf-8")
        self.register_handler(operation, wrapped)

    # Actively test one endpoint, even if routing currently excludes it. Connection,
    # authentication, and ping/pong all share connect_timeout. Return a result with the
    # observed health and always close this probe's socket; no application task is invoked.
    def check_connection(self, endpoint_id: str) -> dict[str, Any]:
        """Probe a fresh connection, including excluded/unhealthy endpoints."""
        if self._stop.is_set():
            return self._result(code="closed", warning="network is closed", endpoint_id=endpoint_id)
        started = time.monotonic()
        deadline = started + self.config.connect_timeout
        connection: socket.socket | None = None
        try:
            endpoint = self.router.endpoint(endpoint_id)
            # Use a separate short-lived connection: probing must never read a pending
            # reply or wait behind a long-running request on the persistent client socket.
            connection = self._open(endpoint, deadline)
            request_id = uuid.uuid4().hex
            self.codec.send(connection, {"kind": "ping", "request_id": request_id}, deadline=deadline)
            reply, payload = self.codec.receive(connection, deadline=deadline)
            if reply["kind"] != "pong" or reply.get("request_id") != request_id or payload:
                raise ProtocolError("invalid health probe response")
            health = self._record(endpoint_id, latency_ms=(time.monotonic() - started) * 1000)
            # An authenticated reply clears consecutive failures and restores eligibility.
            return self._result(endpoint_id=endpoint_id, status=health)
        except NetworkError as exc:
            health = None
            if exc.code != "route_unavailable":
                health = self._record(endpoint_id, exc.code, str(exc))
            return self._result(code=exc.code, warning=str(exc), endpoint_id=endpoint_id, status=health)
        finally:
            self._drop_socket(connection)

    # Enable periodic background checks for registered endpoints. Return immediately;
    # later observations appear in status() and poll(). Starting twice creates no extra loop.
    def start_monitoring(self) -> dict[str, Any]:
        with self._lock:
            if self._stop.is_set():
                return self._result(code="closed", warning="network is closed")
            self.monitor.start()
            return self._result()

    # With an ID return its health snapshot, or a failure dict for an unknown ID.
    # Without an ID return this instance's identity/closed flag, listener address, and
    # all endpoint health. The last listener address survives close(), with listening=False.
    def status(self, endpoint_id: str | None = None) -> dict[str, Any]:
        # Status is a cached snapshot; reading it does not initiate any network traffic.
        if endpoint_id is not None:
            try:
                return self.router.status(endpoint_id)
            except NetworkError as exc:
                return self._result(code=exc.code, warning=str(exc), endpoint_id=endpoint_id)
        with self._lock:
            listener = None
            if self._listener_address is not None:
                host, port = self._listener_address
                listener = {"host": host, "port": port, "listening": self._listener is not None}
            return {"identity": self.identity, "closed": self._stop.is_set(),
                    "listener": listener, "endpoints": self.router.status()}

    # Consume queued local notifications (up to 1024) and attach a current cached snapshot.
    # timeout=0 is nonblocking; a positive timeout waits for the first event. Returns
    # code='events' or 'idle', both successful. Drained events are not returned again.
    def poll(self, timeout: float = 0.0) -> dict[str, Any]:
        if timeout < 0:
            raise ValueError("poll timeout cannot be negative")
        events = []
        try:
            # Wait only for the first event, then drain immediately available events.
            # Network replies live on sockets and are never consumed by this event queue.
            events.append(self._events.get(timeout=timeout))
            while len(events) < 1024:
                events.append(self._events.get_nowait())
        except queue.Empty:
            pass
        return {"ok": True, "code": "events" if events else "idle", "events": events,
                "status": self.status()}

    # Terminal shutdown: reject new work, release all socket resources, and wait within
    # one budget for managed threads. Safe to call repeatedly or from a handler. Threads
    # still executing caller code may outlive that budget; join() cannot forcibly stop them.
    def close(self) -> None:
        with self._lock:
            # Atomically prevent new work and detach resources. Repeated close() calls
            # still join outstanding threads even if another caller started cleanup.
            first_close = not self._stop.is_set()
            self._stop.set()
            listener, self._listener = self._listener, None
            sockets = list(self._sockets)
            self._sockets.clear()
        deadline = time.monotonic() + self.config.shutdown_timeout
        # Close sockets before joining to unblock accept/recv and pending health probes.
        # All joins share one budget, rather than waiting the full timeout per thread.
        _close_socket(listener)
        for connection in sockets:
            _close_socket(connection)
        self.monitor.stop(max(0.0, deadline - time.monotonic()))
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            # A handler may close its own Network. Joining that thread would deadlock;
            # already-running callbacks otherwise finish cooperatively or outlive the budget.
            if thread is not threading.current_thread():
                thread.join(max(0.0, deadline - time.monotonic()))
        if first_close:
            self._event("closed")

    # Support 'with Network(...) as network:' by returning this object. Entering does
    # not automatically start a server or monitor; the caller chooses those roles.
    def __enter__(self) -> "Network":
        return self

    # 'with' invokes this on normal exit or exceptions. exc contains exception type,
    # value, and traceback (or three None values). Returning None does not suppress the
    # caller's exception; it only guarantees that Network cleanup is attempted.
    def __exit__(self, *exc: Any) -> None:
        self.close()
