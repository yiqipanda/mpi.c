# mpi.c

A minimal MPI-style simulator built on top of pthreads.

## What it includes

- `MPI_Init`-style runtime setup through `mpi_sim_runtime_create`
- `send`, `recv`, `bcast`, and `barrier`
- per-rank worker threads
- verbose DEBUG logging to `logs/mpi_sim.log`

- **MPI-style Runtime:** Initialization through `mpi_sim_runtime_create`.
- **Communication:** Implements `send`, `recv`, `bcast`, and `barrier`.
- **Concurrency:** Managed per-rank worker threads using `pthreads`.
- **Logging:** Detailed execution logs stored in `logs/mpi_sim.log`.
- **Tracing:** Performance and event tracing via `trace.json`.

## Build

With Ninja:

```bash
cmake --preset ninja
cmake --build --preset ninja
```

With Make:

```bash
make
```

To enable verbose DEBUG logging from the CLI, pass:

```bash
cmake -DDEBUG=ON -S . -B build-ninja -G Ninja
```

## Run

```bash
./build-ninja/mpi_sim_demo 4
```

Or with the Makefile wrapper:

```bash
make run
```

The demo writes detailed logs to `logs/mpi_sim.log` when DEBUG mode is enabled.

---

# MPI Simulator Project (Extended Documentation)

## Overview
This project is a lightweight simulator for Message Passing Interface (MPI) primitives, implemented in C++ using `pthreads`. It provides a realistic environment for testing distributed algorithms on a single machine by simulating multiple ranks as separate threads.

## Key Features
- **MPI API Emulation**: Implements `send`, `recv`, `bcast`, `barrier`, `comm_rank`, and `comm_size`.
- **Thread-Based Parallelism**: Each rank runs in its own OS thread.
- **Message Queuing**: Asynchronous message passing with per-rank mailboxes and condition variables.
- **Chrome Trace Support**: Generates `trace.json` compatible with `chrome://tracing` for performance analysis.
- **Visual Analytics**: Includes a Python-based Dash dashboard for simulation visualization.

## Project Structure
- `include/`: API definitions (`mpi_sim.h`).
- `src/`: Core implementation logic.
- `agents/`: AI agent configurations for various tasks.
- `scripts/`: Utility scripts for building and running.
- `viz/`: (New) Dash-based visualization application.
- `logs/`: Runtime logs and trace files.

## Architecture
The simulator follows a layered architecture:
1.  **Application Layer**: User-defined worker functions using the `mpi_sim` API.
2.  **API Layer**: The public interface providing MPI-like functions.
3.  **Runtime Layer**: Manages thread lifecycles, message buffers, and synchronization primitives.
4.  **Tracing/Logging Layer**: Captures events and writes them to files for post-mortem analysis.

## Visualization Dashboard
The project includes a Dash-based visualization tool to analyze simulation behavior.

### Prerequisites
- Python 3.8+
- Dependencies: `dash`, `pandas`, `plotly`

### Running the Dashboard
```bash
pip install -r requirements.txt
python viz/app.py
```

## SOLID & Clean Code Principles
- **Single Responsibility**: Each class and module has one clear purpose (e.g., `trace.cpp` handles only tracing).
- **Interface Segregation**: The public API (`mpi_sim.h`) hides implementation details via opaque handles.
- **Dependency Inversion**: High-level simulation logic depends on abstractions, not direct OS calls where possible.
- **Readability**: Consistent naming conventions and detailed comments.

