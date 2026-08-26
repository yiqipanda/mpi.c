# mpi.c Description

This document explains the full execution flow of the MPI-style simulator in plain English.
It covers:

- the program hierarchy
- what gets allocated
- what gets initialized
- how control moves from `main()` into the worker threads
- how `send`, `recv`, `bcast`, and `barrier` behave
- why the code uses the conventions it does

## 1. What This Project Is

`mpi.c` is a small MPI-like simulator built on top of `pthreads`.

The main idea is:

- one runtime object represents the whole simulated world
- one OS thread is created for each simulated MPI rank
- each thread runs the same worker function
- threads communicate through per-rank message queues
- synchronization is done with mutexes and condition variables

So instead of a real MPI cluster, this project simulates ranks inside one process.

## 2. Program Hierarchy

The code is split into three main layers:

### Public API layer

`include/mpi_sim.h`

- declares the public types
- exposes the runtime functions
- hides the implementation details behind an opaque handle

### Runtime implementation layer

`src/mpi_sim.cpp`

- creates and destroys the runtime
- starts and joins worker threads
- implements message passing and synchronization
- stores all private runtime state

### Demo / application layer

`main.cpp`

- parses the command line
- creates the runtime config
- starts the simulation
- runs a demo worker that uses the API
- cleans everything up at the end

## 3. Core Data Structures

### `mpi_sim_config_t`

This is the input configuration for the runtime.

It stores:

- `world_size`
- `debug_enabled`
- `log_dir`
- `log_file`

### `mpi_sim_runtime_t`

This is the hidden runtime object.

It stores:

- global simulator settings
- logging state
- barrier state
- thread bookkeeping
- per-rank mailbox locks
- per-rank condition variables
- per-rank message queues

This type is opaque in the header so user code cannot depend on internal fields.

### `mpi_sim_tls_t`

This is thread-local state.

Each OS thread gets its own copy, which stores:

- the runtime pointer
- the current rank
- whether the thread is active

### `mpi_sim_thread_ctx_t`

This is the small context object passed to each newly created worker thread.

It stores:

- the shared runtime pointer
- the rank number for that thread
- the user callback
- user data

### `mpi_sim_message_t`

This is one queued message.

It stores:

- source rank
- tag
- payload size
- payload pointer
- next pointer for the linked list

### `mpi_sim_status_t`

This is optional receive metadata.

It reports:

- source rank
- tag
- number of bytes in the received message

## 4. Complete Execution Flow

### Step 1: `main()` builds a config

`main.cpp` sets:

- the world size
- whether debug logging is enabled
- the log directory
- the log file path

This is the setup phase.

### Step 2: the runtime is created

`mpi_sim_runtime_create()` does the heavy lifting:

- validates the config
- allocates the runtime object
- copies config values into the runtime
- initializes mutexes and condition variables
- allocates arrays for rank bookkeeping and mailboxes
- initializes each rank mailbox lock and condition variable
- creates the log directory if needed
- opens the log file
- stores the runtime in the global fallback pointer

At this point the simulator is ready to launch threads.

### Step 3: `mpi_sim_run()` starts one thread per rank

The runtime spawns `world_size` worker threads.

For each rank:

- it fills a `mpi_sim_thread_ctx_t`
- it sets the rank number
- it passes the same user callback to every thread
- it passes the same user data to every thread
- it creates the OS thread with `pthread_create()`

So `mpi_sim_run()` is the launcher for the simulated MPI world.

### Step 4: each worker enters `thread_entry()`

Each worker thread starts in `thread_entry()`.

That function:

- reads the thread context
- calls `mpi_sim_init()` for that rank
- logs that the rank started
- calls the user callback
- logs that the rank finished
- marks the thread inactive

This is the bridge between the runtime and user code.

Inside `mpi_sim_init()`, the rank is initialized like this:

- the runtime pointer is stored in thread-local storage
- the rank number is stored in thread-local storage
- the thread is marked active
- `thread_initialized[rank]` is set
- `initialized_threads` is incremented under `runtime_mutex`

So `mpi_sim_init()` is where the worker becomes a real simulated rank.

### Step 5: the user callback runs as if it were MPI rank code

Inside the callback, each thread can call:

- `mpi_sim_comm_rank()`
- `mpi_sim_comm_size()`
- `mpi_sim_send()`
- `mpi_sim_recv()`
- `mpi_sim_bcast()`
- `mpi_sim_barrier()`
- `mpi_sim_finalize()`

From the callback’s point of view, it behaves like a normal MPI rank.

### Step 6: `mpi_sim_run()` waits for all ranks to finish

After all worker threads are created, the main thread joins them.

That means:

- the simulator does not return until every rank thread exits
- all communication must complete before the run is over

### Step 7: the runtime is destroyed

Back in `main.cpp`, the runtime is cleaned up with `mpi_sim_runtime_destroy()`.

That function:

- frees all queued messages
- destroys all mailbox locks and condition variables
- frees all allocated arrays
- closes the log file
- destroys the runtime mutexes
- clears the global runtime pointer if it points to this runtime
- frees the runtime object itself

## 5. Allocation and Initialization Map

| Location | What happens | Why it matters |
| --- | --- | --- |
| `mpi_sim_runtime_create()` | `calloc()` allocates the runtime | zeroed memory makes startup safer |
| `mpi_sim_runtime_create()` | copies `world_size`, logging settings, and paths | keeps runtime self-contained |
| `mpi_sim_runtime_create()` | initializes `log_mutex`, `runtime_mutex`, `barrier_mutex`, and `barrier_cond` | these protect shared state |
| `mpi_sim_runtime_create()` | allocates `thread_initialized`, `threads`, `mailbox_mutexes`, `mailbox_conds`, `mailboxes` | one set of structures per rank |
| `mpi_sim_runtime_create()` | initializes each rank’s mailbox mutex and condition variable | each rank has isolated message handling |
| `mpi_sim_runtime_create()` | ensures the log directory exists and opens the log file | debug output can be written immediately |
| `mpi_sim_init()` | records the runtime/rank in thread-local storage and updates init counters | the thread becomes an active rank |
| `mpi_sim_run()` | allocates the per-thread context array | each worker needs its own startup data |
| `mpi_sim_send()` | allocates a message node and optional payload buffer | sends are copied, not shared by pointer |
| `mpi_sim_recv()` | copies data into the caller’s buffer and frees the message | the receiver owns the copy it gets |

## 6. Message-Passing Flow

### `mpi_sim_send()`

This function:

- validates the runtime and payload
- checks that the destination rank is valid
- allocates a new message node
- allocates and copies the payload when `count > 0`
- records source rank and tag
- locks the destination mailbox
- appends the message to the queue
- signals the destination condition variable

Important result:

- the sender keeps its own buffer
- the receiver gets an independent copied message

### `mpi_sim_recv()`

This function:

- validates inputs
- locks the current rank’s mailbox
- searches the queue for a matching message
- waits on the condition variable if nothing matches yet
- removes the message from the queue
- copies the payload into the caller’s buffer
- fills `status` if the caller provided one
- frees the message

Important result:

- `recv` blocks until a matching message exists
- the queue is searched by source and tag
- negative source or tag acts like a wildcard

### `mpi_sim_bcast()`

Broadcast is implemented as a convenience wrapper.

- if the current rank is the root, it sends to every other rank
- if the current rank is not the root, it waits to receive from the root

So broadcast is not a separate transport mechanism.
It is built from `send` and `recv`.

### `mpi_sim_barrier()`

Barrier is the synchronization gate.

The flow is:

- each arriving rank locks the barrier mutex
- it increments the waiting count
- if it is the last expected rank, it advances the generation and wakes everyone
- otherwise it sleeps until the generation changes

The generation counter is important because it prevents a thread from waking up for the wrong barrier round.

## 7. Why These Conventions Exist

### Opaque runtime handle

The header only exposes `mpi_sim_runtime_t` as an incomplete type.

Why:

- user code should not depend on private implementation details
- the internal layout can change without breaking the public API
- it keeps the interface cleaner

### `extern "C"`

The header uses C linkage when included from C++.

Why:

- the functions can be called from both C and C++
- symbol names stay stable
- it avoids name mangling issues

### Thread-local storage

`g_tls` and `g_last_error` are thread-local.

Why:

- each OS thread represents a separate rank
- each rank needs its own identity
- each rank should have its own last-error text
- this avoids ranks overwriting each other’s state

### Per-rank mailbox locks

Each rank has its own mailbox mutex and condition variable.

Why:

- different ranks can communicate independently
- locking is more precise than one giant global lock
- it reduces unnecessary contention

### Linked-list message queues

Messages are stored as linked lists.

Why:

- simple to append
- simple to remove one matching message
- easy to support wildcard matching on receive

### Condition variables

The code uses condition variables instead of polling.

Why:

- waiting threads sleep efficiently
- the simulator does not waste CPU spinning
- receivers wake up only when new messages arrive

### Barrier generation counter

The barrier uses a generation number.

Why:

- it separates one barrier round from the next
- it avoids incorrect wakeups
- it makes the barrier reusable

### Separate `finalize()` and `destroy()`

`mpi_sim_finalize()` ends one rank.
`mpi_sim_runtime_destroy()` ends the whole simulator.

Why:

- rank lifetime and runtime lifetime are different
- worker threads need a way to stop individually
- the main program needs a way to clean up globally

## 8. Key Points To Remember

- `main.cpp` creates the runtime and owns the top-level flow
- `mpi_sim_run()` creates one thread per rank
- every worker thread runs the same callback
- `send` copies data into a mailbox
- `recv` waits until a matching message appears
- `bcast` is built from send/recv
- `barrier` blocks until all ranks arrive
- thread-local storage keeps rank identity separate
- the runtime must not be destroyed until all worker threads are done

## 9. Demo Worker Flow

The demo worker in `main.cpp` shows how the API is meant to be used:

- rank 0 creates a greeting
- rank 0 broadcasts the greeting
- non-root ranks receive and print it
- rank 0 and rank 1 do a ping-pong exchange
- every rank enters the barrier
- every rank finalizes

This demo is useful because it touches all the main primitives in one place.

## 10. One-Sentence Mental Model

Think of the simulator as:

> a single process that launches one thread per rank, gives each thread its own identity, and lets them exchange copied messages through locked mailboxes until they all synchronize and exit.
