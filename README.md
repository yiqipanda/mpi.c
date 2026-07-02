# mpi.c

A minimal MPI-style simulator built on top of pthreads.

## What it includes

- `MPI_Init`-style runtime setup through `mpi_sim_runtime_create`
- `send`, `recv`, `bcast`, and `barrier`
- per-rank worker threads
- verbose DEBUG logging to `logs/mpi_sim.log`

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

