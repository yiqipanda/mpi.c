#ifndef MPI_SIM_PID_H
#define MPI_SIM_PID_H

#include <stddef.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

// Opaque runtime handle.
// The implementation owns the process table, logging state, and child-tracking arrays.
typedef struct mpi_sim_pid_runtime mpi_sim_pid_runtime_t;

// Callback invoked in each forked worker process.
// The runtime passes the same user_data pointer to every worker.
typedef void (*mpi_sim_pid_entry_fn)(void *user_data);

// Configuration for creating a PID-backed simulation runtime.
// This keeps launch parameters together instead of passing a long argument list.
typedef struct mpi_sim_pid_config {
    int world_size;
    int debug_enabled;
    const char *log_dir;
    const char *log_file;
} mpi_sim_pid_config_t;

// Stable information about a launched worker.
// This exists so the master can expose the original worker mapping by launch slot.
typedef struct mpi_sim_pid_worker_info {
    pid_t pid;
    int launch_slot;
    int generation;
} mpi_sim_pid_worker_info_t;

// Final status captured when a worker exits.
// This separate struct exists because exit state is only known after reaping children.
typedef struct mpi_sim_pid_worker_status {
    pid_t pid;
    int launch_slot;
    int generation;
    int exited;
    int exit_code;
    int signaled;
    int signal_number;
} mpi_sim_pid_worker_status_t;

// Create and destroy a runtime instance.
// Creation allocates bookkeeping structures and opens the log file.
mpi_sim_pid_runtime_t *mpi_sim_pid_runtime_create(const mpi_sim_pid_config_t *config);
void mpi_sim_pid_runtime_destroy(mpi_sim_pid_runtime_t *runtime);

// Launch the worker set, wait for it to finish, or do both in one call.
// `run` is just the launch + reap convenience wrapper.
int mpi_sim_pid_launch(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data);
int mpi_sim_pid_reap(mpi_sim_pid_runtime_t *runtime);
int mpi_sim_pid_run(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data);

// Worker-side lifecycle hooks.
// `init` binds the current process to a launch slot; `finalize` clears that worker context.
int mpi_sim_pid_init(mpi_sim_pid_runtime_t *runtime, int launch_slot);
int mpi_sim_pid_finalize(void);

// Query the active simulated world size and the current worker rank.
// These are only meaningful once a worker context has been initialized.
int mpi_sim_pid_comm_size(void);
int mpi_sim_pid_comm_rank(void);
pid_t mpi_sim_pid_os_pid(void);

// Read-only accessors for the master's launch registry and final exit statuses.
// The lookup helpers copy a single record into caller-provided storage.
const mpi_sim_pid_worker_info_t *mpi_sim_pid_registry(const mpi_sim_pid_runtime_t *runtime, size_t *count);
const mpi_sim_pid_worker_status_t *mpi_sim_pid_statuses(const mpi_sim_pid_runtime_t *runtime, size_t *count);
int mpi_sim_pid_worker_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_info_t *info);
int mpi_sim_pid_worker_status_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_status_t *status);

// Return the most recent thread-local error string.
const char *mpi_sim_pid_last_error(void);

#ifdef __cplusplus
}
#endif

#endif
