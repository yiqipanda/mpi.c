#include "mpi_sim.h"


#include <cstdlib>
#include <cstdio>
#include <cstring>

struct demo_state {
    int world_size;
};

static void demo_worker(void *user_data) {
    demo_state *state = static_cast<demo_state *>(user_data);
    (void)state;

    int rank = mpi_sim_comm_rank();
    int size = mpi_sim_comm_size();

    char message[128] = {0};
    if (rank == 0) {
        std::snprintf(message, sizeof(message), "hello from rank %d of %d", rank, size);
    }

    mpi_sim_bcast(message, sizeof(message), 0);

    if (rank != 0) {
        std::printf("rank %d received broadcast: %s\n", rank, message);
    } else {
        std::printf("rank %d broadcasted: %s\n", rank, message);
    }

    if (rank == 0 && size > 1) {
        const char *ping = "ping";
        mpi_sim_send(ping, std::strlen(ping) + 1, 1, 7);
        char pong[16] = {0};
        mpi_sim_recv(pong, sizeof(pong), 1, 8, nullptr);
        std::printf("rank 0 received reply: %s\n", pong);
    } else if (rank == 1) {
        char ping[16] = {0};
        mpi_sim_recv(ping, sizeof(ping), 0, 7, nullptr);
        const char *pong = "pong";
        mpi_sim_send(pong, std::strlen(pong) + 1, 0, 8);
    }

    mpi_sim_barrier();
    mpi_sim_finalize();
}

int main(int argc, char **argv) {
    int world_size = 4;
#ifdef MPI_SIM_DEBUG
    int debug_enabled = 1;
#else
    int debug_enabled = 0;
#endif

    if (argc > 1) {
        world_size = std::atoi(argv[1]);
    }
    if (world_size <= 0) {
        std::fprintf(stderr, "world size must be positive\n");
        return 1;
    }

    mpi_sim_config_t config = {};
    config.world_size = world_size;
    config.debug_enabled = debug_enabled;
    config.log_dir = "logs";
    config.log_file = "logs/mpi_sim.log";

    mpi_sim_runtime_t *runtime = mpi_sim_runtime_create(&config);
    if (!runtime) {
        std::fprintf(stderr, "failed to create runtime: %s\n", mpi_sim_last_error());
        return 1;
    }

    demo_state state = {world_size};
    int rc = mpi_sim_run(runtime, demo_worker, &state);
    if (rc != 0) {
        std::fprintf(stderr, "simulation failed: %s\n", mpi_sim_last_error());
        mpi_sim_runtime_destroy(runtime);
        return 1;
    }

    mpi_sim_runtime_destroy(runtime);
    return 0;
}