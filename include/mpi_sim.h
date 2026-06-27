#ifndef MPI_SIM_H
#define MPI_SIM_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mpi_sim_message {
    int source;
    int tag;
    size_t size;
    void *data;
    struct mpi_sim_message *next;
} mpi_sim_message_t;

typedef struct mpi_sim_status {
    int source;
    int tag;
    size_t count;
} mpi_sim_status_t;

typedef struct mpi_sim_runtime mpi_sim_runtime_t;

typedef void (*mpi_sim_entry_fn)(void *user_data);

typedef struct mpi_sim_config {
    int world_size;
    int debug_enabled;
    const char *log_dir;
    const char *log_file;
} mpi_sim_config_t;

mpi_sim_runtime_t *mpi_sim_runtime_create(const mpi_sim_config_t *config);
void mpi_sim_runtime_destroy(mpi_sim_runtime_t *runtime);

int mpi_sim_run(mpi_sim_runtime_t *runtime, mpi_sim_entry_fn entry, void *user_data);

int mpi_sim_init(mpi_sim_runtime_t *runtime, int rank);
int mpi_sim_finalize(void);

int mpi_sim_comm_size(void);
int mpi_sim_comm_rank(void);

int mpi_sim_send(const void *buffer, size_t count, int destination, int tag);
int mpi_sim_recv(void *buffer, size_t count, int source, int tag, mpi_sim_status_t *status);
int mpi_sim_bcast(void *buffer, size_t count, int root);
int mpi_sim_barrier(void);

const char *mpi_sim_last_error(void);

#ifdef __cplusplus
}
#endif

#endif
