#include "mpi_sim_pid.h"
#include "trace.h"

#include <cerrno>
#include <cstdarg>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <new>
#include <mutex>
#include <signal.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>

// ============================================================================
// Shared contract
// ============================================================================

struct mpi_sim_pid_runtime {
    int world_size;
    int debug_enabled;
    int run_generation;
    int launch_complete;
    int reap_complete;
    pid_t master_pid;
    char log_dir[256];
    char log_file[256];
    FILE *log_fp;
    std::mutex log_mutex;
    mpi_sim_pid_worker_info_t *registry;
    mpi_sim_pid_worker_status_t *statuses;
    pid_t *child_pids;
    int launched_children;
    int reaped_children;
};

typedef struct mpi_sim_pid_tls {
    mpi_sim_pid_runtime_t *runtime;
    int launch_slot;
    int generation;
    pid_t pid;
    int active;
    int finalized;
} mpi_sim_pid_tls_t;

static thread_local mpi_sim_pid_tls_t g_tls = {};
static thread_local char g_last_error[256] = {0};
static mpi_sim_pid_runtime_t *g_runtime = NULL;

static void set_last_error(const char *fmt, ...)
{
    va_list args;
    va_start(args, fmt);
    vsnprintf(g_last_error, sizeof(g_last_error), fmt, args);
    va_end(args);
}

const char *mpi_sim_pid_last_error(void)
{
    return g_last_error[0] ? g_last_error : "no error";
}

static void ensure_log_dir(const char *dir)
{
    if (!dir || !dir[0]) {
        return;
    }

    struct stat st;
    if (stat(dir, &st) == 0 && S_ISDIR(st.st_mode)) {
        return;
    }

    mkdir(dir, 0755);
}

static void log_message(mpi_sim_pid_runtime_t *runtime, const char *fmt, ...)
{
    if (!runtime || !runtime->debug_enabled || !runtime->log_fp) {
        return;
    }

    std::lock_guard<std::mutex> lock(runtime->log_mutex);
    const int fd = fileno(runtime->log_fp);
    if (fd >= 0) {
        flock(fd, LOCK_EX);
    }

    va_list args;
    va_start(args, fmt);
    vfprintf(runtime->log_fp, fmt, args);
    va_end(args);
    fputc('\n', runtime->log_fp);
    fflush(runtime->log_fp);

    if (fd >= 0) {
        flock(fd, LOCK_UN);
    }
}

static mpi_sim_pid_runtime_t *runtime_from_tls(void)
{
    if (g_tls.active) {
        return g_tls.runtime;
    }
    return g_runtime;
}

static int is_master_process(const mpi_sim_pid_runtime_t *runtime)
{
    return runtime && getpid() == runtime->master_pid;
}

static void reset_statuses(mpi_sim_pid_runtime_t *runtime)
{
    if (!runtime) {
        return;
    }

    memset(runtime->registry, 0, sizeof(*runtime->registry) * (size_t)runtime->world_size);
    memset(runtime->statuses, 0, sizeof(*runtime->statuses) * (size_t)runtime->world_size);
    memset(runtime->child_pids, 0, sizeof(*runtime->child_pids) * (size_t)runtime->world_size);
    runtime->launched_children = 0;
    runtime->reaped_children = 0;
    runtime->launch_complete = 0;
    runtime->reap_complete = 0;
}

static int record_child_status(mpi_sim_pid_runtime_t *runtime, pid_t pid, int raw_status, int *slot_out)
{
    if (!runtime) {
        return -1;
    }

    for (int i = 0; i < runtime->world_size; ++i) {
        if (runtime->registry[i].pid != pid) {
            continue;
        }

        mpi_sim_pid_worker_status_t *status = &runtime->statuses[i];
        status->pid = pid;
        status->launch_slot = runtime->registry[i].launch_slot;
        status->generation = runtime->registry[i].generation;
        status->exited = WIFEXITED(raw_status);
        status->exit_code = WIFEXITED(raw_status) ? WEXITSTATUS(raw_status) : -1;
        status->signaled = WIFSIGNALED(raw_status);
        status->signal_number = WIFSIGNALED(raw_status) ? WTERMSIG(raw_status) : 0;
        if (slot_out) {
            *slot_out = i;
        }
        runtime->reaped_children++;
        return 0;
    }

    return -1;
}

static void terminate_children(mpi_sim_pid_runtime_t *runtime)
{
    if (!runtime) {
        return;
    }

    for (int i = 0; i < runtime->world_size; ++i) {
        pid_t pid = runtime->registry[i].pid;
        if (pid > 0) {
            kill(pid, SIGTERM);
        }
    }
}

static void child_process_main(mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_entry_fn entry, void *user_data)
{
    if (mpi_sim_pid_init(runtime, launch_slot) != 0) {
        log_message(runtime, "[worker %d pid=%d] init failed: %s", launch_slot, (int)getpid(), mpi_sim_pid_last_error());
        _exit(1);
    }

    trace_event("PID_WORKER_START", "Process", "i", (int)getpid(), launch_slot);
    log_message(runtime, "[worker %d pid=%d] started generation=%d", launch_slot, (int)getpid(), g_tls.generation);

    if (entry) {
        entry(user_data);
    }

    mpi_sim_pid_finalize();
    log_message(runtime, "[worker %d pid=%d] finished", launch_slot, (int)getpid());
    trace_event("PID_WORKER_END", "Process", "i", (int)getpid(), launch_slot);
    _exit(0);
}

// ============================================================================
// Master-only
// ============================================================================

mpi_sim_pid_runtime_t *mpi_sim_pid_runtime_create(const mpi_sim_pid_config_t *config)
{
    if (!config || config->world_size <= 0) {
        set_last_error("invalid runtime configuration");
        return NULL;
    }

    mpi_sim_pid_runtime_t *runtime = new (std::nothrow) mpi_sim_pid_runtime_t();
    if (!runtime) {
        set_last_error("out of memory creating runtime");
        return NULL;
    }

    runtime->world_size = config->world_size;
    runtime->debug_enabled = config->debug_enabled;
    runtime->master_pid = getpid();
    strncpy(runtime->log_dir, config->log_dir ? config->log_dir : "logs", sizeof(runtime->log_dir) - 1);
    strncpy(runtime->log_file, config->log_file ? config->log_file : "logs/mpi_sim_pid.log", sizeof(runtime->log_file) - 1);

    runtime->registry = (mpi_sim_pid_worker_info_t *)calloc((size_t)runtime->world_size, sizeof(*runtime->registry));
    runtime->statuses = (mpi_sim_pid_worker_status_t *)calloc((size_t)runtime->world_size, sizeof(*runtime->statuses));
    runtime->child_pids = (pid_t *)calloc((size_t)runtime->world_size, sizeof(*runtime->child_pids));
    if (!runtime->registry || !runtime->statuses || !runtime->child_pids) {
        set_last_error("out of memory creating runtime resources");
        mpi_sim_pid_runtime_destroy(runtime);
        return NULL;
    }

    ensure_log_dir(runtime->log_dir);
    runtime->log_fp = fopen(runtime->log_file, "a");
    if (!runtime->log_fp) {
        set_last_error("unable to open log file '%s': %s", runtime->log_file, strerror(errno));
        mpi_sim_pid_runtime_destroy(runtime);
        return NULL;
    }
    setvbuf(runtime->log_fp, nullptr, _IONBF, 0);

    g_runtime = runtime;
    trace_init("trace.json");
    log_message(runtime, "[master pid=%d] runtime created world_size=%d", (int)runtime->master_pid, runtime->world_size);
    return runtime;
}

static int reap_children_blocking(mpi_sim_pid_runtime_t *runtime)
{
    while (runtime->reaped_children < runtime->launched_children) {
        int raw_status = 0;
        pid_t pid = waitpid(-1, &raw_status, 0);
        if (pid < 0) {
            if (errno == EINTR) {
                continue;
            }
            set_last_error("waitpid failed: %s", strerror(errno));
            return -1;
        }

        int slot = -1;
        if (record_child_status(runtime, pid, raw_status, &slot) != 0) {
            set_last_error("reaped unknown child pid=%d", (int)pid);
            return -1;
        }
        log_message(runtime,
                    "[master pid=%d] reaped slot=%d pid=%d exit=%d signal=%d",
                    (int)getpid(),
                    slot,
                    (int)pid,
                    runtime->statuses[slot].exit_code,
                    runtime->statuses[slot].signal_number);
    }

    runtime->reap_complete = 1;
    return 0;
}

void mpi_sim_pid_runtime_destroy(mpi_sim_pid_runtime_t *runtime)
{
    if (!runtime) {
        return;
    }

    if (is_master_process(runtime) && runtime->launch_complete && !runtime->reap_complete) {
        terminate_children(runtime);
        (void)reap_children_blocking(runtime);
    }

    if (runtime->log_fp) {
        log_message(runtime, "[master pid=%d] runtime destroying", (int)getpid());
        fclose(runtime->log_fp);
        runtime->log_fp = NULL;
    }

    if (is_master_process(runtime)) {
        trace_close();
    }

    free(runtime->registry);
    free(runtime->statuses);
    free(runtime->child_pids);
    if (g_runtime == runtime) {
        g_runtime = NULL;
    }
    delete runtime;
}

int mpi_sim_pid_launch(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data)
{
    if (!runtime || !entry) {
        set_last_error("invalid launch arguments");
        return -1;
    }
    if (!is_master_process(runtime)) {
        set_last_error("launch is master-only");
        return -1;
    }
    if (runtime->launch_complete && !runtime->reap_complete) {
        set_last_error("launch called while children are still running");
        return -1;
    }

    reset_statuses(runtime);
    runtime->run_generation++;

    for (int slot = 0; slot < runtime->world_size; ++slot) {
        runtime->registry[slot].launch_slot = slot;
        runtime->registry[slot].generation = runtime->run_generation;

        pid_t pid = fork();
        if (pid < 0) {
            set_last_error("fork failed for slot %d: %s", slot, strerror(errno));
            terminate_children(runtime);
            (void)reap_children_blocking(runtime);
            return -1;
        }

        if (pid == 0) {
            child_process_main(runtime, slot, entry, user_data);
        }

        runtime->registry[slot].pid = pid;
        runtime->child_pids[slot] = pid;
        runtime->launched_children++;
        trace_event("PID_WORKER_LAUNCH", "Process", "i", (int)pid, slot);
    }

    runtime->launch_complete = 1;
    log_message(runtime, "[master pid=%d] launched generation=%d workers=%d", (int)getpid(), runtime->run_generation, runtime->world_size);
    return 0;
}

int mpi_sim_pid_reap(mpi_sim_pid_runtime_t *runtime)
{
    if (!runtime) {
        set_last_error("invalid reap arguments");
        return -1;
    }
    if (!is_master_process(runtime)) {
        set_last_error("reap is master-only");
        return -1;
    }
    if (!runtime->launch_complete) {
        set_last_error("reap called before launch");
        return -1;
    }
    if (runtime->reap_complete) {
        return 0;
    }

    int rc = reap_children_blocking(runtime);
    if (rc == 0) {
        log_message(runtime, "[master pid=%d] reaped generation=%d", (int)getpid(), runtime->run_generation);
    }
    return rc;
}

int mpi_sim_pid_run(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data)
{
    if (mpi_sim_pid_launch(runtime, entry, user_data) != 0) {
        return -1;
    }
    return mpi_sim_pid_reap(runtime);
}

const mpi_sim_pid_worker_info_t *mpi_sim_pid_registry(const mpi_sim_pid_runtime_t *runtime, size_t *count)
{
    if (count) {
        *count = runtime ? (size_t)runtime->world_size : 0;
    }
    return runtime ? runtime->registry : NULL;
}

const mpi_sim_pid_worker_status_t *mpi_sim_pid_statuses(const mpi_sim_pid_runtime_t *runtime, size_t *count)
{
    if (count) {
        *count = runtime ? (size_t)runtime->world_size : 0;
    }
    return runtime ? runtime->statuses : NULL;
}

int mpi_sim_pid_worker_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_info_t *info)
{
    if (!runtime || !info || launch_slot < 0 || launch_slot >= runtime->world_size) {
        set_last_error("invalid worker lookup");
        return -1;
    }
    *info = runtime->registry[launch_slot];
    return 0;
}

int mpi_sim_pid_worker_status_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_status_t *status)
{
    if (!runtime || !status || launch_slot < 0 || launch_slot >= runtime->world_size) {
        set_last_error("invalid worker status lookup");
        return -1;
    }
    *status = runtime->statuses[launch_slot];
    return 0;
}

// ============================================================================
// Worker-only
// ============================================================================

int mpi_sim_pid_init(mpi_sim_pid_runtime_t *runtime, int launch_slot)
{
    if (!runtime || launch_slot < 0 || launch_slot >= runtime->world_size) {
        set_last_error("invalid pid init arguments");
        return -1;
    }
    if (is_master_process(runtime)) {
        set_last_error("pid init is worker-only");
        return -1;
    }

    if (g_tls.active) {
        if (g_tls.runtime == runtime && g_tls.launch_slot == launch_slot) {
            return 0;
        }
        set_last_error("pid init called while worker context already active");
        return -1;
    }

    g_tls.runtime = runtime;
    g_tls.launch_slot = launch_slot;
    g_tls.generation = runtime->run_generation;
    g_tls.pid = getpid();
    g_tls.active = 1;
    g_tls.finalized = 0;

    log_message(runtime, "[worker %d pid=%d] init complete generation=%d", launch_slot, (int)g_tls.pid, g_tls.generation);
    return 0;
}

int mpi_sim_pid_finalize(void)
{
    mpi_sim_pid_runtime_t *runtime = runtime_from_tls();
    if (!runtime) {
        set_last_error("finalize called without runtime");
        return -1;
    }
    if (is_master_process(runtime)) {
        set_last_error("pid finalize is worker-only");
        return -1;
    }
    if (!g_tls.active) {
        return 0;
    }

    log_message(runtime, "[worker %d pid=%d] finalize", g_tls.launch_slot, (int)g_tls.pid);
    g_tls.active = 0;
    g_tls.finalized = 1;
    return 0;
}

int mpi_sim_pid_comm_size(void)
{
    mpi_sim_pid_runtime_t *runtime = runtime_from_tls();
    return runtime ? runtime->world_size : -1;
}

int mpi_sim_pid_comm_rank(void)
{
    return g_tls.active ? g_tls.launch_slot : -1;
}

pid_t mpi_sim_pid_os_pid(void)
{
    return getpid();
}
