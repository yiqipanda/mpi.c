# Python Prototype: Future Architecture Plan

## 1. Purpose and Scope

This document describes the planned evolution of the Python prototype in
`prototype/`. It does not define future work for the pthread-based C/C++
runtime.

The current prototype models a program as a fixed hierarchy of one-shot tasks.
The target architecture instead treats each simulated computer as a persistent
resolution engine: an interactive execution environment that can repeatedly
evaluate program functions, accept requests from other computers, retain useful
program-run state, and reuse results when doing so is safe.

The central goals are to:

- evaluate the same function repeatedly with different parameters without
  recreating its complete execution environment;
- distribute an evaluation according to the computers that are actually
  available, even though the programmer cannot know that number in advance;
- let workers delegate function evaluations to child workers through an API and
  await their results;
- retain useful data and evaluation history for one program run without making
  mutation or memoization behavior ambiguous.

## 2. Current Prototype

The current execution path is `Manager` to `Worker` to a pooled `Runner`:

- `Manager` constructs a hard-coded task tree and assigns each task to one
  worker.
- Each task describes one main program invocation, optional child tasks, and an
  optional orchestration program.
- A worker temporarily leases a runner from `RunnerPool` for each execution
  stage.
- A runner launches the assigned Python file as a one-shot subprocess, captures
  its output, and returns to the pool.
- A parent worker performs its own main task, polls the shared task objects until
  its children finish, and then runs a separate orchestration program to combine
  the results.
- Runner health checks, retries, resets, and stale-runner handling provide basic
  recovery behavior.

Tasks are therefore created once and their programs are run once. Workers do
not provide API channels to their child workers. The hierarchy exists as a tree
of task records rather than as a network of persistent computers that can ask
one another to evaluate functions. Runners do not retain initial memory,
function-call history, memoized results, or a specialized execution purpose.

## 3. Target Execution Model

### 3.1 Workers and Resolution Engines

A worker will remain the logical orchestrator for an assigned computer and its
child workers. The computer itself will be represented by a persistent
resolution engine rather than a subprocess that is created for one task and
then discarded.

A resolution engine should be thought of as an interactive Haskell-like
program: while it is alive, a caller can repeatedly ask it to evaluate a
function with particular parameters through an API. The engine returns the
result without requiring the caller to perform that computation itself.

At the beginning of a program run, the manager will inspect the available
computers and assign:

- the worker hierarchy;
- one resolution engine to each worker;
- an initial purpose and initial memory to each engine.

An engine retains this assignment for the duration of the program run. Its
purpose is an affinity, not a restriction: requests should be routed to the
engine whose purpose and retained data best match the function, but an engine
may evaluate other functions when local fallback is required.

Program code is small compared with computation data, so every engine may
receive every function used by the program. Engines are specialized through
their assigned purpose, memory, and cached records rather than by being denied
access to other functions.

The hierarchy and engine purposes remain fixed during a run. An individual
evaluation may use only a subset of the assigned child workers. When the run
ends, engine state, caches, histories, and purpose assignments are cleared or
the engine is explicitly reassigned.

> **Decision:** The worker and computer remain separate concepts. A worker
> orchestrates its persistent resolution engine and its child workers.

## 4. Evaluation API and Orchestration

### 4.1 Parent-Child Communication

Workers will communicate through an explicit parent-child request/response API.
A worker evaluating a function may issue several child requests concurrently,
continue its own portion of the work, and then await the correlated results
before combining them.

Completing one evaluation does not end the worker's assignment. Its result may
direct the worker to request another function evaluation, allowing computation
to proceed as a sequence of API calls rather than as one fixed, one-shot task.

Communication remains within the logical hierarchy: a worker sends evaluation
requests to its assigned children rather than addressing arbitrary engines.
This makes ownership, cancellation, and failure propagation follow the same
structure as computation.

Each evaluation request conceptually contains:

- a stable call ID, also used as an idempotency key;
- the requested function and its code version;
- all explicitly supplied arguments;
- a version selector for every omitted argument;
- a deadline and cancellation context.

Each response is correlated with its call ID and contains either a successful
result or a structured error. The exact transport, serialization format, and
surface syntax remain implementation decisions.

### 4.2 Ordering, Cancellation, and Failure

Each resolution engine processes its incoming request queue serially. Parallel
execution occurs across engines, while per-engine serialization gives memory
updates, version selection, and cache access a deterministic order.

Cancellation propagates down the hierarchy. Cancelling or timing out a parent
evaluation cancels every unfinished descendant request created on its behalf.
A parent must never wait indefinitely for a failed or cancelled child.

If a child engine fails, the runtime first attempts to replace and recover it.
If recovery attempts are exhausted, the parent evaluates that partition locally
when its engine can do so. If local evaluation is not compatible, the parent
returns a structured failure.

State-changing calls use their stable call IDs to prevent duplicate mutation.
When an engine receives an already completed mutation ID, it returns the
recorded outcome instead of applying the update again.

## 5. Versioned Memory and Evaluation Records

### 5.1 Initial and Updated Memory

Each resolution engine begins a program run with assigned initial memory. APIs
may explicitly read or update named values in that memory. Every update appends
an immutable version to that parameter's history instead of silently replacing
the only recorded value.

When a function call omits a parameter, the caller must specify which stored
version supplies it. For example, `--recent` selects the most recent version,
while an exact version ID selects a particular historical value. An omitted
parameter without a selector is an error. A selector such as `--recent` is
resolved to a concrete version when the engine accepts the request, so later
updates cannot change the meaning of an in-flight call.

> **Considered alternative:** An omitted argument could have silently reused
> the value from the previous evaluation. That sticky-parameter model is not the
> selected behavior because it hides data dependencies and makes concurrent
> calls ambiguous. Previous values remain usable, but only through an explicit
> version selector.

> **Mutation constraint:** Engines may update their memory over time through
> APIs, but every mutation must be named, versioned, ordered, and recorded. The
> engine must not expose untracked mutable state to evaluations.

### 5.2 Memoization

It is unnecessary to evaluate the same deterministic function with the same
effective inputs more than once. Each engine therefore maintains a local cache
for the current program run.

An evaluation cache key contains:

- the function identity and code version;
- the complete normalized argument set;
- the concrete version of every retained value used by the evaluation.

Only deterministic function evaluations are memoized. State-update APIs are
never memoized. Calls with the same explicit arguments but different relevant
memory versions are different evaluations and cannot share a result.

The engine also keeps an ordered API and state-update record. This record is
useful for tracing the computation and for recovery. A replacement engine can
load the original initial memory and replay deterministic updates in order.
Cached evaluation results may be recomputed rather than recovered.

## 6. Partitioning Contracts

### 6.1 Motivation

A programmer cannot know in advance how many computers will be available to a
program. A partitioning contract describes how an evaluation may be divided
with respect to its parameters without fixing the number of computers.

The motivating example is:

\[
h(x,y_{0:N})=h(x,y_{0:a})+h(x,y_{b:N})
\]

The selected interpretation uses contiguous, non-overlapping, half-open ranges:
`[0:a)` and `[a:N)`. In that interpretation, the second boundary `b` is `a`.
The analyzer may generalize the two-way illustration into any useful number of
non-overlapping partitions.

### 6.2 Contract Contents

A partitionable function has one active contract. The contract defines:

- which parameter may be partitioned;
- the legal rule for producing non-overlapping parameter ranges;
- how partial results are combined;
- the combiner's identity value, when empty input is possible;
- whether result order must be preserved;
- whether associativity permits regrouping partial results;
- estimated computation cost and partitioning overhead.

These reduction properties are part of the contract because a combiner such as
addition can be regrouped safely, while other combiners may require a strict
source-range order.

> **Superseded proposal:** An earlier version allowed more than one partitioning
> contract per function. The active design uses exactly one contract per
> partitionable function, so the analyzer chooses partition count and boundaries
> rather than choosing among alternative contracts.

### 6.3 Analyzer Behavior

For each evaluation, the partitioning analyzer considers the input size, the
healthy child engines assigned to the program, the expected computation cost,
and the overhead of delegation and combination.

The analyzer will:

- keep the entire evaluation local when the function has no contract;
- keep it local when partitioning is unlikely to improve runtime;
- divide a legal range into N non-overlapping partitions when delegation is
  worthwhile;
- use only the number of assigned child engines that provides a useful benefit;
- delegate the partitions that current capacity permits and compute any
  remaining portion on the requesting engine;
- combine partial results according to the contract's ordering and algebraic
  guarantees.

The analyzer does not wait for an ideal computer count and does not reject an
otherwise valid evaluation merely because fewer computers are available.

## 7. Implementation Roadmap (Not Author Written)

### Stage 1: Persistent Engine Lifecycle

- Introduce explicit worker and engine identities.
- Give each worker a persistent engine for one program run.
- Define assignment, initialization, reset, replacement, and teardown states.

### Stage 2: Evaluation Channel

- Replace one-shot runner invocation with an awaitable parent-child
  request/response channel.
- Add call correlation, structured results and errors, deadlines, and cascading
  cancellation.
- Serialize each engine's incoming requests while allowing concurrency across
  child engines.

### Stage 3: Versioned State and Reuse

- Add initial memory and immutable per-parameter version histories.
- Require explicit selectors for omitted arguments.
- Add ordered mutation logs, idempotency tracking, and local evaluation caches.
- Recover replacement engines by replaying deterministic state updates.

### Stage 4: Partitioning Contracts and Analysis

- Define the single-contract representation and its reduction guarantees.
- Implement cost-aware N-way partitioning over the available child engines.
- Support local execution for indivisible work and insufficient-capacity
  fallback.

### Stage 5: Analyzer-Generated Programs

- Replace the hard-coded demo task tree with program-start analysis.
- Assign the worker hierarchy, resolution-engine purposes, and initial memory
  from the available computers.
- Drive child evaluation and result orchestration through contracts and the new
  API.

### Stage 6: Recovery and Observability

- Complete replay recovery and local fallback behavior.
- Ensure failures terminate with structured errors rather than leaving parents
  waiting indefinitely.
- Trace calls, resolved state versions, cache decisions, partition plans,
  retries, cancellations, and recovery steps.

## 8. Future Acceptance Scenarios

The future implementation should cover at least these scenarios:

- the same deterministic function, code version, arguments, and state versions
  produce a local cache hit;
- changing a relevant state version prevents an incorrect cache hit;
- an omitted parameter without a selector is rejected;
- `--recent` and an exact version ID resolve to the intended stored values;
- N-way partitioning produces the same result as an unpartitioned evaluation;
- a non-regroupable combiner preserves its declared range order;
- an evaluation remains local when partitioning overhead exceeds its benefit;
- insufficient child capacity delegates what is useful and computes the
  remainder locally;
- a failed engine is reconstructed from initial memory and its ordered update
  log;
- a repeated mutation call ID does not apply the mutation twice;
- an unrecoverable child evaluation falls back locally or returns a structured
  error;
- cancelling a parent evaluation cancels all unfinished descendant calls;
- program teardown clears or explicitly reassigns engine state and caches.

## 9. Deferred Implementation Details

This proposal intentionally does not yet select:

- the request transport or serialization format;
- the concrete Python API and CLI spelling beyond the `--recent` example;
- cache size limits and eviction policy;
- the initial calibration method for cost and partitioning-overhead estimates.

These choices do not change the execution, state, partitioning, or recovery
semantics defined above.
