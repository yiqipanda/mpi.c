# Python package migration options

This is a recommendation for the proposed coordinator and persistent worker application, not a description of features already implemented. The runnable demo has one local main process and three local workers. It uses standard-library TCP sockets, newline-delimited JSON, an allowlist of serializable classes, in-memory function tables, and a custom log buffer. See [network.py](../src/network.py), [message.py](../src/message.py), [registered_types.py](../src/registered_types.py), [other_process.py](../src/other_process.py), and [log_buffer.py](../src/log_buffer.py). The longer-term engine and cache semantics are described in [FUTUREWORKS.md](FUTUREWORKS.md).

The recommendations below are based on the linked **official package documentation**. They establish that the cited APIs fit specific parts of this system; they are not a claim that the proposed stack has been installed, benchmarked, or integrated. No dependency or source file was changed for this review.

## Recommended starting stack

| Package | Proposed use | Replaces or supports | When to add |
| --- | --- | --- | --- |
| [grpcio](https://grpc.io/docs/languages/python/basics/), [grpcio-tools](https://grpc.io/docs/languages/python/quickstart/), [protobuf](https://protobuf.dev/reference/python/python-generated/) | Typed coordinator/worker RPCs, generated client/server interfaces, TLS, deadlines, status codes | Most of network.py and message.py | When moving beyond the socket demo |
| [grpcio-health-checking](https://grpc.github.io/grpc/python/grpc_health_checking.html) | Standard worker readiness reporting | Part of worker availability checks | With gRPC if workers expose an RPC server |
| [pydantic](https://docs.pydantic.dev/latest/concepts/models/) and [pydantic-settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) | Validate submitted directives and deployment settings | Hand-written input/config checks and hard-coded host/port values | Early |
| [structlog](https://www.structlog.org/en/stable/standard-library.html) | Structured events with run, call, and worker IDs | Event construction in log.py and formatting in log_buffer.py | Early |
| [tenacity](https://tenacity.readthedocs.io/en/latest/) | Bounded backoff for startup and selected transient operations | The fixed retry loop in otherNetwork.connect() | Only where gRPC retry is insufficient |
| [diskcache](https://grantjenks.com/docs/diskcache/tutorial.html) | Optional local, run-scoped data/result cache | Future cache; request_table is currently only retained function state | After cache semantics are defined |
| [SQLAlchemy](https://docs.sqlalchemy.org/en/20/orm/session_basics.html) and [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html) | Durable coordinator metadata and schema migrations | Future run/call/worker records, not the worker result cache | Only if state must survive restarts |

This is one coherent route, not an instruction to install everything immediately. In particular, protobuf is the wire schema for the gRPC route; pydantic is for local configuration and submitted program metadata. The two have different jobs. **Before building that route**, compare it with [Ray](https://docs.ray.io/en/latest/ray-core/actors.html), which could provide the entire distributed execution layer. [msgspec](https://msgspec.dev/structs) is a lighter **alternative** to the gRPC/Protocol Buffers route for the existing JSON protocol, not an extra serializer to add on top of it.

## Architectural alternative to pilot first: Ray

Ray is a more consequential package choice than a serializer. Its [remote tasks](https://docs.ray.io/en/latest/ray-core/tasks.html) run functions asynchronously in worker processes; its [actors](https://docs.ray.io/en/latest/ray-core/actors.html) keep state in a specific worker process and run methods serially by default. Its [object store](https://docs.ray.io/en/latest/ray-core/objects.html) holds immutable values referenced by object IDs. Those documented features map closely to the planned persistent resolution engine, repeated evaluations, and reuse of data. Ray's [on-premises guide](https://docs.ray.io/en/latest/cluster/vms/user-guides/launching-clusters/on-premises.html) explicitly describes installing it on machines that share a network and starting a head node plus worker nodes.

An API sketch based on the documented actor methods shows why this merits a pilot:

~~~python
import ray

@ray.remote
class Engine:
    def __init__(self, recipe_registry):
        self.recipes = recipe_registry
        self.run_data = {}

    def evaluate(self, recipe_id, inputs):
        return self.recipes[recipe_id](inputs)

engine = Engine.remote(registry)
result_ref = engine.evaluate.remote("matrix_columns_v1", partition)
result = ray.get(result_ref)
~~~

This could remove much of the custom socket, message, worker-process, and result-collection code. It would **not** supply the project's partition contracts, safe cache keys, versioned memory, or mutation replay. Ray's [actor fault-tolerance guide](https://docs.ray.io/en/latest/ray-core/fault_tolerance/actors.html) explicitly says an actor restart does not restore application state automatically. It also would make the product depend on Ray's cluster model and deployment. The [Ray security guide](https://docs.ray.io/en/latest/ray-security/index.html) says standard Ray clusters expect trusted code and a controlled network and use cloudpickle for arbitrary Python objects. Evaluate Ray as an **alternative to a custom gRPC execution layer**, not as an automatic companion to it. **Research required: high** for deployment, resource placement, data movement, restart behavior, and security. A small pilot with two machines and a stateful engine would make this decision evidence-based.

## 1. Service communication: grpcio, grpcio-tools, protobuf

**Why it fits.** The demo manually accepts sockets, frames messages with newlines, serializes parameters, correlates replies, and checks OK/ERR strings. The [gRPC Python tutorial](https://grpc.io/docs/languages/python/basics/) documents defining an RPC service in a .proto file, generating Python stubs with grpcio-tools, and implementing Python clients and servers. The [generated-code guide](https://grpc.io/docs/languages/python/generated-code/) identifies the generated message, stub, servicer, and registration objects. This directly covers the transport and message plumbing now in MainNetwork, otherNetwork, and Message.

An **illustrative** contract for a future worker could be:

~~~proto
syntax = "proto3";

service Worker {
  rpc Evaluate(EvaluateRequest) returns (EvaluateReply);
  rpc ClearRun(ClearRunRequest) returns (ClearRunReply);
}

message EvaluateRequest {
  string run_id = 1;
  string call_id = 2;
  string recipe_id = 3;
  string recipe_version = 4;
  bytes input = 5;
}
message EvaluateReply {
  string call_id = 1;
  bytes result = 2;
}
message ClearRunRequest { string run_id = 1; }
message ClearRunReply {}
~~~

The bytes fields are deliberately opaque here. A real handoff must specify the input and result schema, size limits, and how large datasets are transferred. Protocol Buffers does **not** serialize arbitrary Python function objects or provision code on workers. Prefer a versioned recipe/function identifier on the wire and a separate, explicit mechanism for installing or distributing trusted code.

**Security and reliability.** gRPC documents [TLS and optional mutual certificate authentication](https://grpc.io/docs/guides/auth/); its [Python API](https://grpc.github.io/grpc/python/grpc.html) provides ssl_server_credentials, ssl_channel_credentials, secure ports, and secure channels. This can protect traffic when workers move off localhost, provided the project also defines certificate issuance, rotation, and which certificate identities may register. gRPC [deadlines](https://grpc.io/docs/guides/deadlines/) and [status codes](https://grpc.io/docs/guides/status-codes/) replace indefinite waits and ad hoc error strings. Its [cancellation guide](https://grpc.io/docs/guides/cancellation/) says server computation must cooperate with cancellation; cancelling an RPC does not automatically stop a long-running calculation. The [AsyncIO API](https://grpc.github.io/grpc/python/grpc_asyncio.html) can support overlapping requests, but its documentation warns that blocking computation on the event-loop thread can starve other RPCs. Run CPU-bound functions in a separate process or otherwise isolate them from the RPC loop.

**What remains ours.** Worker authorization, call IDs, idempotency, partition planning, scheduling, local fallback, and state replay remain application logic. Define whether the coordinator calls worker servers directly or workers maintain outbound channels; the demo's connection direction alone does not settle the product topology. gRPC's [retry guide](https://grpc.io/docs/guides/retry/) supports configured retries but also explains why most calls are not retried by default. A retried state mutation must be safe under the same stable call ID.

**Research required: high.** The author should study .proto compatibility/versioning, Python sync versus AsyncIO server behavior, mutual TLS deployment, flow control for large values, deadline propagation in the chosen topology, and the threat model for submitted code before committing to this stack.

### Worker readiness: grpcio-health-checking

The [Python health service API](https://grpc.github.io/grpc/python/grpc_health_checking.html) provides HealthServicer.set(service, status) and Check/Watch. The [gRPC health-check guide](https://grpc.io/docs/guides/health-checking/) describes clients withholding requests until a service reports healthy. A persistent worker can report serving status only after its engine, recipe version, and assigned data are ready. This is a readiness signal, **not** recovery or proof that a particular function will succeed. It is useful when workers expose gRPC servers; if workers only initiate outbound connections, the application needs an equivalent heartbeat/state protocol. **Research required: medium** for lifecycle and channel behavior.

## 2. Configuration and directive validation: pydantic, pydantic-settings

HOST, PORT, and worker IDs are currently literals in [main_process.py](../src/main_process.py) and [other_process.py](../src/other_process.py). [Pydantic Settings](https://docs.pydantic.dev/latest/concepts/pydantic_settings/) documents typed BaseSettings models loaded from environment variables or secrets files. [Pydantic models](https://docs.pydantic.dev/latest/concepts/models/) validate structured data. These are suitable for deployment configuration and for metadata extracted from supported decorators in a submitted Python file:

~~~python
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MPI_")
    coordinator_host: str
    coordinator_port: int = Field(ge=1, le=65535)
    worker_id: int = Field(ge=1)

# Reads MPI_COORDINATOR_HOST, MPI_COORDINATOR_PORT, and MPI_WORKER_ID.
settings = WorkerSettings()
~~~

The snippet follows the documented BaseSettings/SettingsConfigDict and Field APIs. A separate model could validate a directive such as recipe_id, recipe_version, and cache_policy after the application extracts it. Pydantic does not parse Python source, determine whether partitioning is mathematically valid, or make executing submitted Python safe. Use an explicit allowlist of recipes and verify their contract. **Research required: low to medium** for settings precedence, strict validation, and secrets handling.

## 3. Event logging: structlog

The current Log and LogBuffer types sort events, transfer log batches, acknowledge them, and render RFC 5424 text. [structlog's standard-library integration](https://www.structlog.org/en/stable/standard-library.html) and [API documentation](https://www.structlog.org/en/stable/api.html) show structured fields, JSON rendering, and context-local values. A worker could emit logger.info("partition_finished", run_id=..., call_id=..., worker_id=..., elapsed_s=...) instead of building event strings manually. This makes records easier to filter and correlate across computers.

Keep a distinction between **operational logs** and the **ordered mutation record** proposed in FUTUREWORKS.md. structlog formats/emits events; it does not guarantee central delivery, acknowledgement, or replay. If the product needs one combined log, choose a collector or explicit forwarding protocol. If it needs recovery, persist state changes in a transactional store. **Research required: low** for local JSON logs; **medium** if log shipping or audit guarantees are required.

## 4. Bounded retries: tenacity

[Tenacity](https://tenacity.readthedocs.io/en/latest/) documents retry_if_exception_type, wait_exponential, stop_after_attempt, and async retrying. These can replace the fixed 50-attempt, 0.1-second connection loop in [network.py](../src/network.py), or handle temporary file/database failures. A retry policy should name the exception, attempt limit, backoff, and overall deadline.

Do **not** add Tenacity around every Evaluate call. Use gRPC's documented retry policy where it fits, and retry only operations whose semantics are safe. A lost reply does not prove that evaluation or mutation failed. State-changing calls require stable IDs and deduplication before retries. **Research required: medium** for failure classification and idempotency.

## 5. Run-scoped worker cache: diskcache

[DiskCache's tutorial](https://grantjenks.com/docs/diskcache/tutorial.html) documents a filesystem/SQLite-backed Cache with atomic operations, cross-process access, expiration, size limits, and eviction policies. This fits the proposed persistent worker that may keep input data and deterministic results for one program run. The application still has to build the key from the function identity and code version, normalized inputs, and concrete versions of retained data, as specified in [FUTUREWORKS.md](FUTUREWORKS.md). Use a directory per run and clear it when the run ends or the worker is reassigned. DiskCache is local to a machine; it is not the coordinator's record of which worker owns data.

**Security qualification.** DiskCache's [serialization documentation](https://grantjenks.com/docs/diskcache/tutorial.html#disk) says strings and bytes are native, while other types may use Pickle. Python [warns against unpickling untrusted data](https://docs.python.org/3/library/pickle.html). Store string/hash keys and already validated bytes values, or define a safe custom serializer. Protect the cache directory from tampering. Check support on the selected Python version before adoption; the referenced DiskCache documentation is older than the current environment. **Research required: high** for cache invalidation, eviction, serialization, and restart behavior.

## 6. Durable metadata, only if required: SQLAlchemy and Alembic

The demo has no database. If the product must preserve worker registrations, run history, call IDs, versioned memory, or the ordered update record across coordinator restarts, use a relational database rather than treating logs or worker caches as the source of truth. [SQLAlchemy's session guide](https://docs.sqlalchemy.org/en/20/orm/session_basics.html#framing-out-a-begin-commit-rollback-block) documents a transaction context that commits on success and rolls back on error; that fits recording a call outcome and its state update atomically. [Alembic](https://alembic.sqlalchemy.org/en/latest/tutorial.html) documents versioned schema migration scripts for SQLAlchemy applications.

One coordinator with local persistence could begin with SQLite; a multi-coordinator design would need a separate database decision. No external database service is required by the current demo, and SQLAlchemy is not necessary merely to hold a small in-memory recipe registry. The executable partition and combination implementations should remain versioned Python code; the database may store identifiers, versions, and metadata. **Research required: high** for the persistence boundary, schema, transaction isolation, recovery, and deployment of migrations.

## 7. Observability after multiple machines: OpenTelemetry

[OpenTelemetry Python](https://opentelemetry.io/docs/languages/python/instrumentation/) documents spans using opentelemetry-api and opentelemetry-sdk. [Context propagation](https://opentelemetry.io/docs/concepts/context-propagation/) can connect coordinator dispatch, worker evaluation, and result collection in one trace. This is useful once plain logs cannot explain cross-machine delays. It complements structlog: traces show causal timing, while logs describe events. It requires an exporter/collector choice and an operational destination; the SDK alone is not a monitoring service. **Research required: high** for propagation, sampling, data retention, and deployment. Defer until the network execution path works.

## 8. Development checks: pip-audit and Hypothesis

| Package | Documented capability and project use | Limit |
| --- | --- | --- |
| [pip-audit](https://github.com/pypa/pip-audit#readme) | Audit the eventual environment, requirements, or project lockfile for known dependency vulnerabilities in CI or release preparation. | It reports **known dependency vulnerabilities**; its own security model says it is not static analysis or a guarantee that code is safe. It does not sandbox submitted Python. |
| [Hypothesis](https://hypothesis.readthedocs.io/en/latest/tutorial/introduction.html) | Generate many legal inputs with @given to check that orchestrate(partition(x)) equals direct evaluation, including uneven partitions and empty legal ranges. Also test serialization round trips. | Property tests need a correct oracle and fast functions. The current demo deliberately sleeps, so remove artificial delay in test fixtures rather than running thousands of slow evaluations. |

These are development dependencies, not coordinator/worker runtime services. **Research required: low** for basic use; designing meaningful partition invariants is application work.

## 9. Workload-specific speed: NumPy

For numeric programs, [numpy.matmul](https://numpy.org/doc/stable/reference/generated/numpy.matmul.html) implements matrix products and can use optimized BLAS. It could replace the nested Python dot-product loop in [matrix.py](../src/matrix.py) for a real matrix workload. This is **not** a general speedup for the orchestration application. The demo sleeps 0.1 seconds per output entry, so its timing cannot establish a NumPy or distributed-computing performance benefit. Array conversion and network transfer may outweigh parallel gains on small inputs. Make NumPy an optional dependency for numerical recipes and benchmark representative workloads. **Research required: medium** for data format and performance measurements.

## Alternative if retaining the current JSON transport: msgspec

[msgspec Structs](https://msgspec.dev/structs) document typed, validated decoding of JSON (and MessagePack). A msgspec.Struct envelope could replace much of [message.py](../src/message.py)'s hand-written primitive tags and dispatch logic and may reduce serialization overhead. This is a plausible **prototype migration** if changing the transport is premature. It does not provide socket framing, TLS, authentication, RPC deadlines, or worker lifecycle, so keeping raw sockets would leave those concerns to the application. Do not combine it with Protocol Buffers for the same wire messages without a measured reason. **Research required: medium** for tagged unions, schema evolution, and compatibility.

## Recommended migration order and decisions

1. Pilot Ray's actor/object-store route against one persistent engine and one partitioned function; then choose Ray or the custom gRPC/Protocol Buffers route. Define a stable call/worker/run contract, including code version, payload format, error states, idempotency, and trust boundary. Package choice cannot define these semantics.
2. Externalize and validate coordinator/worker settings with pydantic-settings. Add structured local events with structlog.
3. Prototype one Evaluate call using gRPC/Protocol Buffers, with TLS, deadline, and a documented failure response. Keep the demo's existing behavior as a comparison while migrating.
4. Add worker readiness and bounded retries only after specifying which calls may be repeated. Keep computation off the RPC event loop.
5. Add local caching only after proving deterministic cache keys and invalidation. Add SQLAlchemy/Alembic only after deciding what must survive a restart.
6. Add tracing and numeric acceleration when multi-machine traces or benchmarks show a concrete need.

The recommended starting stack does not supply a secure sandbox for arbitrary submitted Python. Standard Ray clusters also expect trusted code and a controlled network; Ray has a [separate sandbox feature](https://docs.ray.io/en/latest/ray-core/sandboxes.html) whose deployment and guarantees would require their own review. The first product boundary should explicitly say that submitted code and participating machines are trusted. If untrusted submissions are a requirement, isolation and resource controls need a separate deployment/security design. The final handoff must also decide how code and dependencies reach workers, how certificates are issued for a gRPC deployment, which database (if any) is operated, and how supported package versions are pinned and upgraded.
