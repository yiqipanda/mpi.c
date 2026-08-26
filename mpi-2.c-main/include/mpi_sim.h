// Include guard: prevents this header from being processed more than once in the same compile.
#ifndef MPI_SIM_H
#define MPI_SIM_H

// Standard C type used for sizes and counts.
#include <stddef.h>

// When included from C++, keep the function names compatible with C linkage.
#ifdef __cplusplus
extern "C" {
#endif

// A single queued message in the simulator.
// This groups message metadata with the payload and a link to the next queued message.
typedef struct mpi_sim_message {
    int source;
    int tag;
    size_t size;
    void *data;
    struct mpi_sim_message *next;
} mpi_sim_message_t;

// Status information returned after a receive completes.
// This is separate from the message itself because it describes the result of the operation.
typedef struct mpi_sim_status {
    int source;
    int tag;
    size_t count;
} mpi_sim_status_t;

// Opaque runtime handle.
// The real fields live in the implementation file so users only interact with the API.
typedef struct mpi_sim_runtime mpi_sim_runtime_t;

// Function pointer type for running user code inside the simulator.
typedef void (*mpi_sim_entry_fn)(void *user_data);

// Configuration values used when creating the runtime.
// Putting these together in one struct keeps setup tidy and easy to extend later.
typedef struct mpi_sim_config {
    int world_size;
    int debug_enabled;
    const char *log_dir;
    const char *log_file;
} mpi_sim_config_t;

// Create and destroy the simulator runtime object.
mpi_sim_runtime_t *mpi_sim_runtime_create(const mpi_sim_config_t *config);
void mpi_sim_runtime_destroy(mpi_sim_runtime_t *runtime);

// Run the user callback inside the simulator environment.
int mpi_sim_run(mpi_sim_runtime_t *runtime, mpi_sim_entry_fn entry, void *user_data);

// Initialize or shut down the simulated MPI state for the current rank.
int mpi_sim_init(mpi_sim_runtime_t *runtime, int rank);
int mpi_sim_finalize(void);

// Query the size of the simulated world and the current rank.
int mpi_sim_comm_size(void);
int mpi_sim_comm_rank(void);

// Core message-passing operations used by the simulator.
int mpi_sim_send(const void *buffer, size_t count, int destination, int tag);
int mpi_sim_recv(void *buffer, size_t count, int source, int tag, mpi_sim_status_t *status);
int mpi_sim_bcast(void *buffer, size_t count, int root);
int mpi_sim_barrier(void);

// Return the most recent error message, if one was recorded.
const char *mpi_sim_last_error(void);

// End of the C/C++ linkage wrapper.
#ifdef __cplusplus
}
#endif

// End of include guard.
#endif
