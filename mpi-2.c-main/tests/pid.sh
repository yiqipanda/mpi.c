# End-to-end smoke testing for mpi_sim_pid.cpp. This test is not run by default, but can be run manually.
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/pid-tests.XXXXXX")"

cleanup() {
    rm -rf "$tmpdir"
}
trap cleanup EXIT

cat >"$tmpdir/pid_test.cpp" <<'CPP'
#include "mpi_sim_pid.h"

#include <chrono>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <thread>
#include <vector>

struct worker_state {
    int world_size;
};

static void worker(void *user_data)
{
    worker_state *state = static_cast<worker_state *>(user_data);
    int slot = mpi_sim_pid_comm_rank();
    pid_t pid = mpi_sim_pid_os_pid();
    if (slot < 0 || slot >= state->world_size) {
        std::cerr << "invalid worker slot\n";
        std::exit(1);
    }

    std::cout << "worker slot=" << slot << " pid=" << static_cast<long long>(pid) << "\n";
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
    mpi_sim_pid_finalize();
}

static void expect(bool condition, const char *message)
{
    if (!condition) {
        std::cerr << "pid test failed: " << message << "\n";
        std::exit(1);
    }
}

int main()
{
    mpi_sim_pid_config_t config{};
    config.world_size = 3;
    config.debug_enabled = 0;
    config.log_dir = "logs";
    config.log_file = "logs/mpi_sim_pid.log";

    mpi_sim_pid_runtime_t *runtime = mpi_sim_pid_runtime_create(&config);
    expect(runtime != nullptr, "runtime creation failed");

    worker_state state{config.world_size};
    expect(mpi_sim_pid_launch(runtime, worker, &state) == 0, "launch failed");

    size_t count = 0;
    const mpi_sim_pid_worker_info_t *registry = mpi_sim_pid_registry(runtime, &count);
    expect(registry != nullptr, "registry pointer missing");
    expect(count == static_cast<size_t>(config.world_size), "registry count mismatch");

    std::vector<mpi_sim_pid_worker_info_t> snapshot(registry, registry + count);
    std::this_thread::sleep_for(std::chrono::milliseconds(50));

    size_t count_again = 0;
    const mpi_sim_pid_worker_info_t *registry_again = mpi_sim_pid_registry(runtime, &count_again);
    expect(registry_again != nullptr, "registry reread failed");
    expect(count_again == count, "registry count changed");

    for (size_t i = 0; i < count; ++i) {
        expect(registry_again[i].pid == snapshot[i].pid, "registry pid changed after launch");
        expect(registry_again[i].launch_slot == snapshot[i].launch_slot, "registry slot changed after launch");
        expect(registry_again[i].generation == snapshot[i].generation, "registry generation changed after launch");
        expect(registry_again[i].pid > 0, "registry recorded an invalid pid");
    }

    expect(mpi_sim_pid_reap(runtime) == 0, "reap failed");

    const mpi_sim_pid_worker_status_t *statuses = mpi_sim_pid_statuses(runtime, &count_again);
    expect(statuses != nullptr, "status pointer missing");
    expect(count_again == count, "status count mismatch");
    for (size_t i = 0; i < count_again; ++i) {
        expect(statuses[i].pid == snapshot[i].pid, "status pid mismatch");
        expect(statuses[i].launch_slot == snapshot[i].launch_slot, "status slot mismatch");
        expect(statuses[i].generation == snapshot[i].generation, "status generation mismatch");
        expect(statuses[i].exited == 1, "child did not exit normally");
        expect(statuses[i].exit_code == 0, "child exit code not clean");
        expect(statuses[i].signaled == 0, "child was signaled");
    }

    mpi_sim_pid_runtime_destroy(runtime);
    std::cout << "mpi_sim_pid smoke test passed\n";
    return 0;
}
CPP

c++ -std=c++17 -Wall -Wextra -Werror -I"$repo_root/include" \
    "$repo_root/src/mpi_sim_pid.cpp" "$repo_root/src/trace.cpp" \
    "$tmpdir/pid_test.cpp" -pthread -o "$tmpdir/pid_test"

"$tmpdir/pid_test"
