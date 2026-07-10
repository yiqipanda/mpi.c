#ifndef MPI_SIM_PID_H
#define MPI_SIM_PID_H

#include <stddef.h>
#include <sys/types.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct mpi_sim_pid_runtime mpi_sim_pid_runtime_t;

typedef void (*mpi_sim_pid_entry_fn)(void *user_data);

typedef struct mpi_sim_pid_config {
    int world_size;
    int debug_enabled;
    const char *log_dir;
    const char *log_file;
} mpi_sim_pid_config_t;

typedef struct mpi_sim_pid_worker_info {
    pid_t pid;
    int launch_slot;
    int generation;
} mpi_sim_pid_worker_info_t;

typedef struct mpi_sim_pid_worker_status {
    pid_t pid;
    int launch_slot;
    int generation;
    int exited;
    int exit_code;
    int signaled;
    int signal_number;
} mpi_sim_pid_worker_status_t;

mpi_sim_pid_runtime_t *mpi_sim_pid_runtime_create(const mpi_sim_pid_config_t *config);
void mpi_sim_pid_runtime_destroy(mpi_sim_pid_runtime_t *runtime);

int mpi_sim_pid_launch(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data);
int mpi_sim_pid_reap(mpi_sim_pid_runtime_t *runtime);
int mpi_sim_pid_run(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data);

int mpi_sim_pid_init(mpi_sim_pid_runtime_t *runtime, int launch_slot);
int mpi_sim_pid_finalize(void);

int mpi_sim_pid_comm_size(void);
int mpi_sim_pid_comm_rank(void);
pid_t mpi_sim_pid_os_pid(void);

const mpi_sim_pid_worker_info_t *mpi_sim_pid_registry(const mpi_sim_pid_runtime_t *runtime, size_t *count);
const mpi_sim_pid_worker_status_t *mpi_sim_pid_statuses(const mpi_sim_pid_runtime_t *runtime, size_t *count);
int mpi_sim_pid_worker_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_info_t *info);
int mpi_sim_pid_worker_status_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_status_t *status);

const char *mpi_sim_pid_last_error(void);

#ifdef __cplusplus
}
#endif

#endif
