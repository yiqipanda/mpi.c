#include "mpi_sim_pid.h" // Public PID-simulator API header.
#include "trace.h" // Trace instrumentation API used by this implementation.

#include <cerrno> // errno support for system-call failure messages.
#include <cstdarg> // variable-argument formatting support.
#include <cstdio> // stdio file I/O support.
#include <cstdlib> // allocation and free support.
#include <cstring> // memory and string utility functions.
#include <new> // std::nothrow allocation support.
#include <mutex> // C++ mutex support for log serialization.
#include <signal.h> // signal constants and kill().
#include <sys/file.h> // flock() support for file locking.
#include <sys/stat.h> // stat() and directory type checks.
#include <sys/types.h> // POSIX type definitions including pid_t.
#include <sys/wait.h> // waitpid() status macros and declarations.
#include <unistd.h> // fork(), getpid(), and _exit().

// These headers provide the C and POSIX primitives this file needs:
// error reporting, printf-style formatting, allocation, mutexes, fork/wait, and PID queries.
// ============================================================================
// Shared contract
// ============================================================================

// Opaque runtime state owned by the master process.
// Callers only see the pointer type in the header; the actual fields live here.
struct mpi_sim_pid_runtime { // Opaque runtime state owned by the master process.
    int world_size; // Number of workers to fork.
    int debug_enabled; // Whether debug logging is enabled.
    int run_generation; // Incremented each time launch is called.
    int launch_complete; // True once all workers have been forked.
    int reap_complete; // True once all workers have been reaped.
    pid_t master_pid; // OS PID of the master process that created this runtime.
    char log_dir[256]; // Directory used for logs.
    char log_file[256]; // Full log file path.
    FILE *log_fp; // Open file handle for log output.
    std::mutex log_mutex; // C++ mutex protecting log writes.
    mpi_sim_pid_worker_info_t *registry; // Launch metadata indexed by slot.
    mpi_sim_pid_worker_status_t *statuses; // Exit status metadata indexed by slot.
    pid_t *child_pids; // Raw child PIDs in slot order.
    int launched_children; // Number of successfully forked children.
    int reaped_children; // Number of children collected with waitpid.
}; // End of the runtime-state struct.

// Thread-local worker context.
// thread_local means each thread gets its own independent copy of this struct.
typedef struct mpi_sim_pid_tls { // Thread-local worker context definition.
    mpi_sim_pid_runtime_t *runtime; // Runtime this worker belongs to.
    int launch_slot; // Slot number that behaves like a rank.
    int generation; // Launch generation number.
    pid_t pid; // Real OS process ID.
    int active; // True after init and before finalize.
    int finalized; // True once finalize has been called.
} mpi_sim_pid_tls_t; // End of the thread-local worker context type.

// Current-thread worker context.
static thread_local mpi_sim_pid_tls_t g_tls = {}; // Current-thread worker context storage.
// Current-thread last-error buffer.
static thread_local char g_last_error[256] = {0}; // Current-thread last-error buffer.
// Master-side fallback pointer when no worker context is active.
static mpi_sim_pid_runtime_t *g_runtime = NULL; // Master-side fallback runtime pointer.

// Formats a message into the thread-local error buffer.
static void set_last_error(const char *fmt, ...) // Function definition starts here.
{ // Begin block.
    va_list args; // This object stores the optional arguments that follow fmt.
    va_start(args, fmt); // Tell the formatting code where the extra arguments begin.
    vsnprintf(g_last_error, sizeof(g_last_error), fmt, args); // Write the formatted error into the thread-local buffer.
    va_end(args); // Finish using the variable-argument list.
} // End block.

// Returns the last error message for the current thread.
const char *mpi_sim_pid_last_error(void) // Function definition starts here.
{ // Begin block.
    return g_last_error[0] ? g_last_error : "no error"; // Return the stored error string, or a default message if none was set.
} // End block.

// Makes the log directory if it does not already exist.
static void ensure_log_dir(const char *dir) // Function definition starts here.
 { // Begin block.
    if (!dir || !dir[0]) { // Skip empty or missing directory paths.
        return; // There is no directory to create.
    } // End block.

    struct stat st; // Temporary storage for file metadata returned by stat().
    if (stat(dir, &st) == 0 && S_ISDIR(st.st_mode)) { // If the path already exists and is a directory...
        return; // ...then there is nothing more to do.
    } // End block.

    mkdir(dir, 0755); // Best-effort creation with ordinary directory permissions.
} // End block.

// Writes one log line when debug logging is enabled.
// The variadic argument list allows printf-style formatting.
static void log_message(mpi_sim_pid_runtime_t *runtime, const char *fmt, ...) // Function definition starts here.
 { // Begin block.
    if (!runtime || !runtime->debug_enabled || !runtime->log_fp) { // Do not log if the runtime is missing, disabled, or closed.
        return; // Logging is optional and should never stop execution.
    } // End block.

    // std::lock_guard is RAII: it locks in this scope and unlocks automatically at the end.
    std::lock_guard<std::mutex> lock(runtime->log_mutex); // Lock the log mutex for the remainder of this scope.
    const int fd = fileno(runtime->log_fp); // Ask stdio for the raw file descriptor behind the FILE*.
    if (fd >= 0) { // Only use flock if the descriptor is valid.
        // flock adds an operating-system file lock on top of the in-process mutex.
        flock(fd, LOCK_EX); // Take an exclusive file lock so multiple processes do not mix their writes.
    } // End block.

    va_list args; // Variable-argument state for the printf-style formatter.
    va_start(args, fmt); // Start consuming the extra arguments passed to log_message().
    vfprintf(runtime->log_fp, fmt, args); // Write the formatted text to the log file.
    va_end(args); // End the variable-argument processing.
    fputc('\n', runtime->log_fp); // Add a newline so each log entry stays on its own line.
    fflush(runtime->log_fp); // Flush immediately so the log stays current.

    if (fd >= 0) { // Release the file lock if we took one.
        flock(fd, LOCK_UN); // Unlock the log file for other writers.
    } // End block.
} // End block.

// Chooses the runtime for the current execution context.
// Workers use thread-local state; the master falls back to a global pointer.
static mpi_sim_pid_runtime_t *runtime_from_tls(void) // Function definition starts here.
 { // Begin block.
    if (g_tls.active) { // If a worker context is active, use its runtime pointer.
        return g_tls.runtime; // Return the worker-local runtime.
    } // End block.
    return g_runtime; // Otherwise use the master-side fallback runtime.
} // End block.

// Checks whether the caller is running in the master process.
static int is_master_process(const mpi_sim_pid_runtime_t *runtime) // Function definition starts here.
 { // Begin block.
    return runtime && getpid() == runtime->master_pid; // Only the process whose PID matches master_pid is the master.
} // End block.

// Clears per-slot launch and status state before starting a new run.
static void reset_statuses(mpi_sim_pid_runtime_t *runtime) // Function definition starts here.
 { // Begin block.
    if (!runtime) { // Ignore a null runtime pointer.
        return; // There is nothing to reset.
    } // End block.

    memset(runtime->registry, 0, sizeof(*runtime->registry) * (size_t)runtime->world_size); // Clear launch metadata for every slot.
    memset(runtime->statuses, 0, sizeof(*runtime->statuses) * (size_t)runtime->world_size); // Clear exit status records for every slot.
    memset(runtime->child_pids, 0, sizeof(*runtime->child_pids) * (size_t)runtime->world_size); // Clear the raw PID list.
    runtime->launched_children = 0; // Reset the number of launched children.
    runtime->reaped_children = 0; // Reset the number of reaped children.
    runtime->launch_complete = 0; // Mark the current run as not launched.
    runtime->reap_complete = 0; // Mark the current run as not reaped.
} // End block.

// Maps a raw waitpid status to our higher-level worker status record.
static int record_child_status(mpi_sim_pid_runtime_t *runtime, pid_t pid, int raw_status, int *slot_out) // Function definition starts here.
 { // Begin block.
    if (!runtime) { // Reject a missing runtime pointer.
        return -1; // Signal failure.
    } // End block.

    for (int i = 0; i < runtime->world_size; ++i) { // Search every launch slot for the matching child PID.
        if (runtime->registry[i].pid != pid) { // Skip slots that belong to different children.
            continue; // Keep searching.
        } // End block.

        mpi_sim_pid_worker_status_t *status = &runtime->statuses[i]; // Point at the status record for this launch slot.
        status->pid = pid; // Store the child's real PID.
        status->launch_slot = runtime->registry[i].launch_slot; // Copy the original launch slot.
        status->generation = runtime->registry[i].generation; // Copy the launch generation.
        status->exited = WIFEXITED(raw_status); // Record whether the child exited normally.
        status->exit_code = WIFEXITED(raw_status) ? WEXITSTATUS(raw_status) : -1; // Store the exit code when one exists.
        status->signaled = WIFSIGNALED(raw_status); // Record whether the child died from a signal.
        status->signal_number = WIFSIGNALED(raw_status) ? WTERMSIG(raw_status) : 0; // Store the signal number when one exists.
        if (slot_out) { // The caller may want the index of the matching slot.
            *slot_out = i; // Return the matching slot index.
        } // End block.
        runtime->reaped_children++; // Count this child as reaped.
        return 0; // Signal success.
    } // End block.

    return -1; // No registry entry matched this PID.
} // End block.

// Sends SIGTERM to any child process that was already launched.
// This is a cleanup path used when launch or destroy needs to abort mid-run.
static void terminate_children(mpi_sim_pid_runtime_t *runtime) // Function definition starts here.
 { // Begin block.
    if (!runtime) { // Ignore a null runtime pointer.
        return; // There are no children to terminate.
    } // End block.

    for (int i = 0; i < runtime->world_size; ++i) { // Walk every possible child slot.
        pid_t pid = runtime->registry[i].pid; // Read the child PID stored in this slot.
        if (pid > 0) { // Only signal valid child processes.
            kill(pid, SIGTERM); // Ask the child to terminate gracefully.
        } // End block.
    } // End block.
} // End block.

// Runs inside each forked child process.
// The child initializes itself, runs the user callback, finalizes, and exits without returning.
static void child_process_main(mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_entry_fn entry, void *user_data) // Function definition starts here.
 { // Begin block.
    if (mpi_sim_pid_init(runtime, launch_slot) != 0) { // Initialize the worker context before doing any worker work.
        log_message(runtime, "[worker %d pid=%d] init failed: %s", launch_slot, (int)getpid(), mpi_sim_pid_last_error()); // Log the initialization failure.
        // _exit is the safe child-process exit after fork.
        _exit(1); // Leave the child immediately because it cannot continue safely.
    } // End block.

    trace_event("PID_WORKER_START", "Process", "i", (int)getpid(), launch_slot); // Record a trace event for worker startup.
    log_message(runtime, "[worker %d pid=%d] started generation=%d", launch_slot, (int)getpid(), g_tls.generation); // Log that the worker started.

    // The user callback is the worker payload.
    if (entry) { // Only call the callback if the caller supplied one.
        entry(user_data); // Run the user-supplied worker function.
    } // End block.

    mpi_sim_pid_finalize(); // Clear worker-local state after the callback returns.
    log_message(runtime, "[worker %d pid=%d] finished", launch_slot, (int)getpid()); // Log that this worker finished.
    trace_event("PID_WORKER_END", "Process", "i", (int)getpid(), launch_slot); // Record a trace event for worker shutdown.
    _exit(0); // Exit the child process cleanly.
} // End block.

// ============================================================================
// Master-only
// ============================================================================

// Creates the master runtime and allocates all bookkeeping structures.
// new (std::nothrow) returns NULL instead of throwing if allocation fails.
mpi_sim_pid_runtime_t *mpi_sim_pid_runtime_create(const mpi_sim_pid_config_t *config) // Function definition starts here.
 { // Begin block.
    if (!config || config->world_size <= 0) { // Reject missing configuration or a non-positive worker count.
        set_last_error("invalid runtime configuration"); // Save a human-readable error message.
        return NULL; // Report failure to the caller.
    } // End block.

    mpi_sim_pid_runtime_t *runtime = new (std::nothrow) mpi_sim_pid_runtime_t(); // Allocate the runtime object without throwing if memory is exhausted.
    if (!runtime) { // Check whether allocation succeeded.
        set_last_error("out of memory creating runtime"); // Explain why the allocation failed.
        return NULL; // Report failure to the caller.
    } // End block.

    // Copy the configuration into runtime-owned storage so it remains valid after return.
    runtime->world_size = config->world_size; // Store the configured worker count.
    runtime->debug_enabled = config->debug_enabled; // Store the debug flag.
    runtime->master_pid = getpid(); // Remember which process created this runtime.
    // strncpy is used with an explicit size limit to avoid overrunning the fixed buffers.
    strncpy(runtime->log_dir, config->log_dir ? config->log_dir : "logs", sizeof(runtime->log_dir) - 1); // Copy or default the log directory path.
    strncpy(runtime->log_file, config->log_file ? config->log_file : "logs/mpi_sim_pid.log", sizeof(runtime->log_file) - 1); // Copy or default the log file path.

    // These arrays are indexed by launch slot, so they preserve stable per-worker records.
    runtime->registry = (mpi_sim_pid_worker_info_t *)calloc((size_t)runtime->world_size, sizeof(*runtime->registry)); // Allocate the launch registry table.
    runtime->statuses = (mpi_sim_pid_worker_status_t *)calloc((size_t)runtime->world_size, sizeof(*runtime->statuses)); // Allocate the exit-status table.
    runtime->child_pids = (pid_t *)calloc((size_t)runtime->world_size, sizeof(*runtime->child_pids)); // Allocate raw PID storage.
    if (!runtime->registry || !runtime->statuses || !runtime->child_pids) { // Check that all three allocations succeeded.
        set_last_error("out of memory creating runtime resources"); // Explain that one of the allocations failed.
        mpi_sim_pid_runtime_destroy(runtime); // Clean up the partially built runtime.
        return NULL; // Report failure to the caller.
    } // End block.

    // Create the log directory before opening the log file.
    ensure_log_dir(runtime->log_dir); // Make sure the directory exists before logging starts.
    runtime->log_fp = fopen(runtime->log_file, "a"); // Open the log file in append mode.
    if (!runtime->log_fp) { // Check whether the file opened successfully.
        set_last_error("unable to open log file '%s': %s", runtime->log_file, strerror(errno)); // Save the system error text.
        mpi_sim_pid_runtime_destroy(runtime); // Clean up the partially built runtime.
        return NULL; // Report failure to the caller.
    } // End block.
    setvbuf(runtime->log_fp, nullptr, _IONBF, 0); // Disable stdio buffering so each log line appears immediately.

    // Save a master-side pointer so helper functions still have access outside worker TLS.
    g_runtime = runtime; // Remember this runtime for non-worker helper calls.
    trace_init("trace.json"); // Initialize the trace output file.
    log_message(runtime, "[master pid=%d] runtime created world_size=%d", (int)runtime->master_pid, runtime->world_size); // Log the runtime creation event.
    return runtime; // Return the fully initialized runtime object.
} // End block.

// Repeatedly waits for any child process and records its exit information.
static int reap_children_blocking(mpi_sim_pid_runtime_t *runtime) // Function definition starts here.
 { // Begin block.
    while (runtime->reaped_children < runtime->launched_children) { // Keep waiting until every launched child has been collected.
        int raw_status = 0; // Storage for the waitpid status word.
        pid_t pid = waitpid(-1, &raw_status, 0); // Wait for any child to exit or stop.
        if (pid < 0) { // Handle waitpid failure.
            if (errno == EINTR) { // The wait was interrupted by a signal.
                continue; // Retry the wait.
            } // End block.
            set_last_error("waitpid failed: %s", strerror(errno)); // Save the operating-system error text.
            return -1; // Report failure to the caller.
        } // End block.

        int slot = -1; // Placeholder for the launch slot matched to this PID.
        if (record_child_status(runtime, pid, raw_status, &slot) != 0) { // Translate the raw wait status into a registry entry.
            set_last_error("reaped unknown child pid=%d", (int)pid); // Explain that the PID was not found in the launch registry.
            return -1; // Report failure to the caller.
        } // End block.
        log_message(runtime, // Log the reaping event with all the important values.
                    "[master pid=%d] reaped slot=%d pid=%d exit=%d signal=%d", // Format string for the log line.
                    (int)getpid(), // PID of the master process doing the reaping.
                    slot, // Launch slot that matched the child PID.
                    (int)pid, // The actual child PID that just exited.
                    runtime->statuses[slot].exit_code, // The exit code when the child exited normally.
                    runtime->statuses[slot].signal_number); // The signal number when the child was killed by a signal.
    } // End block.

    runtime->reap_complete = 1; // Mark the run as fully reaped.
    return 0; // Report success.
} // End block.

// Destroys the runtime and releases all owned resources.
// If the master forgot to reap children, this cleanup path terminates them first.
void mpi_sim_pid_runtime_destroy(mpi_sim_pid_runtime_t *runtime) // Function definition starts here.
 { // Begin block.
    if (!runtime) { // Ignore a null runtime pointer.
        return; // There is nothing to destroy.
    } // End block.

    // Make shutdown safe even if the caller never reaped the children.
    if (is_master_process(runtime) && runtime->launch_complete && !runtime->reap_complete) { // If the master is shutting down before reaping, clean up children first.
        terminate_children(runtime); // Ask launched children to terminate.
        (void)reap_children_blocking(runtime); // Wait for those children so they do not become zombies.
    } // End block.

    // Close the log file before freeing the rest of the runtime storage.
    if (runtime->log_fp) { // Close the log file if it was opened.
        log_message(runtime, "[master pid=%d] runtime destroying", (int)getpid()); // Log the shutdown event.
        fclose(runtime->log_fp); // Close the file handle.
        runtime->log_fp = NULL; // Clear the pointer so later code knows it is closed.
    } // End block.

    // Trace output is only owned by the master process.
    if (is_master_process(runtime)) { // Only the master owns trace output.
        trace_close(); // Flush and close the trace file.
    } // End block.

    // Free the dynamic arrays and clear the fallback runtime pointer if needed.
    free(runtime->registry); // Release the launch registry array.
    free(runtime->statuses); // Release the status array.
    free(runtime->child_pids); // Release the raw child PID array.
    if (g_runtime == runtime) { // If the global fallback points at this runtime...
        g_runtime = NULL; // ...clear it before destroying the object.
    } // End block.
    delete runtime; // Destroy the runtime object itself.
} // End block.

// Forks one child process per launch slot.
// The parent records bookkeeping; the child immediately jumps to child_process_main.
int mpi_sim_pid_launch(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data) // Function definition starts here.
 { // Begin block.
    if (!runtime || !entry) { // Reject missing runtime or missing worker callback.
        set_last_error("invalid launch arguments"); // Save a readable error message.
        return -1; // Report failure.
    } // End block.
    if (!is_master_process(runtime)) { // Only the master is allowed to fork workers.
        set_last_error("launch is master-only"); // Explain the rule violation.
        return -1; // Report failure.
    } // End block.
    if (runtime->launch_complete && !runtime->reap_complete) { // Disallow overlapping launches before the previous run is reaped.
        set_last_error("launch called while children are still running"); // Explain why the call is rejected.
        return -1; // Report failure.
    } // End block.

    reset_statuses(runtime); // Clear per-slot state from the previous run.
    // A generation number lets repeated launches be distinguished from each other.
    runtime->run_generation++; // Advance the generation counter so this run can be distinguished.

    for (int slot = 0; slot < runtime->world_size; ++slot) { // Fork one child for each launch slot.
        // Record slot and generation first so both parent and child see the same launch identity.
        runtime->registry[slot].launch_slot = slot; // Store the launch slot before forking.
        runtime->registry[slot].generation = runtime->run_generation; // Store the current launch generation.

        pid_t pid = fork(); // Duplicate the current process into a parent and a child.
        if (pid < 0) { // Handle fork failure.
            set_last_error("fork failed for slot %d: %s", slot, strerror(errno)); // Save the operating-system error text.
            // If a fork fails mid-run, clean up any children that already exist.
            terminate_children(runtime); // Terminate any children that were already created.
            (void)reap_children_blocking(runtime); // Reap the children we just terminated.
            return -1; // Report failure.
        } // End block.

        if (pid == 0) { // Child process path.
            // Child path: do not return to the master loop.
            child_process_main(runtime, slot, entry, user_data); // Run the worker entry point in the child.
        } // End block.

        // Parent path: remember the child's real PID in both record arrays.
        runtime->registry[slot].pid = pid; // Store the child's PID in the launch registry.
        runtime->child_pids[slot] = pid; // Store the child's PID in the raw PID array too.
        runtime->launched_children++; // Count one more successfully launched child.
        trace_event("PID_WORKER_LAUNCH", "Process", "i", (int)pid, slot); // Emit a trace event for the launch.
    } // End block.

    runtime->launch_complete = 1; // Mark the launch phase as complete.
    log_message(runtime, "[master pid=%d] launched generation=%d workers=%d", (int)getpid(), runtime->run_generation, runtime->world_size); // Log the launch summary.
    return 0; // Report success.
} // End block.

// Waits for the children from the most recent launch to finish.
int mpi_sim_pid_reap(mpi_sim_pid_runtime_t *runtime) // Function definition starts here.
 { // Begin block.
    if (!runtime) { // Reject a missing runtime pointer.
        set_last_error("invalid reap arguments"); // Save a readable error message.
        return -1; // Report failure.
    } // End block.
    if (!is_master_process(runtime)) { // Only the master may reap the children it created.
        set_last_error("reap is master-only"); // Explain the rule violation.
        return -1; // Report failure.
    } // End block.
    if (!runtime->launch_complete) { // Do not reap before launch has happened.
        set_last_error("reap called before launch"); // Explain the call order error.
        return -1; // Report failure.
    } // End block.
    if (runtime->reap_complete) { // If the children were already reaped, there is nothing left to do.
        return 0; // Treat repeated reap calls as success.
    } // End block.

    int rc = reap_children_blocking(runtime); // Wait for all children and collect their exit statuses.
    if (rc == 0) { // Only log the summary if reaping succeeded.
        log_message(runtime, "[master pid=%d] reaped generation=%d", (int)getpid(), runtime->run_generation); // Log that the whole generation finished.
    } // End block.
    return rc; // Return the success or failure code from the blocking reap helper.
} // End block.

// Convenience wrapper that runs launch followed by reap.
int mpi_sim_pid_run(mpi_sim_pid_runtime_t *runtime, mpi_sim_pid_entry_fn entry, void *user_data) // Function definition starts here.
 { // Begin block.
    if (mpi_sim_pid_launch(runtime, entry, user_data) != 0) { // Launch the workers first.
        return -1; // Stop early if launch failed.
    } // End block.
    return mpi_sim_pid_reap(runtime); // Then wait for them to finish.
} // End block.

// Returns the master-owned launch registry as a direct read-only pointer.
const mpi_sim_pid_worker_info_t *mpi_sim_pid_registry(const mpi_sim_pid_runtime_t *runtime, size_t *count) // Function definition starts here.
 { // Begin block.
    if (count) { // The caller may ask how many entries are available.
        *count = runtime ? (size_t)runtime->world_size : 0; // Report the array length, or zero for a null runtime.
    } // End block.
    return runtime ? runtime->registry : NULL; // Return the launch registry array, or NULL if no runtime was provided.
} // End block.

// Returns the master-owned final status array as a direct read-only pointer.
const mpi_sim_pid_worker_status_t *mpi_sim_pid_statuses(const mpi_sim_pid_runtime_t *runtime, size_t *count) // Function definition starts here.
 { // Begin block.
    if (count) { // The caller may ask how many entries are available.
        *count = runtime ? (size_t)runtime->world_size : 0; // Report the array length, or zero for a null runtime.
    } // End block.
    return runtime ? runtime->statuses : NULL; // Return the status array, or NULL if no runtime was provided.
} // End block.

// Copies one launch record into caller-owned storage.
int mpi_sim_pid_worker_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_info_t *info) // Function definition starts here.
 { // Begin block.
    if (!runtime || !info || launch_slot < 0 || launch_slot >= runtime->world_size) { // Validate the runtime, output pointer, and slot index.
        set_last_error("invalid worker lookup"); // Save a readable error message.
        return -1; // Report failure.
    } // End block.
    *info = runtime->registry[launch_slot]; // Copy the requested launch record into caller-provided storage.
    return 0; // Report success.
} // End block.

// Copies one final exit-status record into caller-owned storage.
int mpi_sim_pid_worker_status_lookup(const mpi_sim_pid_runtime_t *runtime, int launch_slot, mpi_sim_pid_worker_status_t *status) // Function definition starts here.
 { // Begin block.
    if (!runtime || !status || launch_slot < 0 || launch_slot >= runtime->world_size) { // Validate the runtime, output pointer, and slot index.
        set_last_error("invalid worker status lookup"); // Save a readable error message.
        return -1; // Report failure.
    } // End block.
    *status = runtime->statuses[launch_slot]; // Copy the requested final status into caller-provided storage.
    return 0; // Report success.
} // End block.

// ============================================================================
// Worker-only
// ============================================================================

// Initializes thread-local worker state in the child process.
int mpi_sim_pid_init(mpi_sim_pid_runtime_t *runtime, int launch_slot) // Function definition starts here.
 { // Begin block.
    if (!runtime || launch_slot < 0 || launch_slot >= runtime->world_size) { // Validate the runtime pointer and the requested launch slot.
        set_last_error("invalid pid init arguments"); // Save a readable error message.
        return -1; // Report failure.
    } // End block.
    if (is_master_process(runtime)) { // The master process must not initialize itself as a worker.
        set_last_error("pid init is worker-only"); // Explain the rule violation.
        return -1; // Report failure.
    } // End block.

    // Treat re-initializing the same runtime/slot as harmless.
    if (g_tls.active) { // If the worker context is already active...
        if (g_tls.runtime == runtime && g_tls.launch_slot == launch_slot) { // ...and it matches this same runtime/slot...
            return 0; // ...then initialization is already done.
        } // End block.
        set_last_error("pid init called while worker context already active"); // Explain that a different worker context is already bound.
        return -1; // Report failure.
    } // End block.

    // Save the worker identity into thread-local storage.
    g_tls.runtime = runtime; // Remember which runtime this worker belongs to.
    g_tls.launch_slot = launch_slot; // Remember the worker's launch slot.
    g_tls.generation = runtime->run_generation; // Remember which launch generation this worker belongs to.
    g_tls.pid = getpid(); // Remember the real OS PID of this worker process.
    g_tls.active = 1; // Mark the worker context as active.
    g_tls.finalized = 0; // Mark finalize as not yet called.

    log_message(runtime, "[worker %d pid=%d] init complete generation=%d", launch_slot, (int)g_tls.pid, g_tls.generation); // Log that the worker context has been initialized.
    return 0; // Report success.
} // End block.

// Clears worker context after the user callback has finished.
int mpi_sim_pid_finalize(void) // Function definition starts here.
 { // Begin block.
    mpi_sim_pid_runtime_t *runtime = runtime_from_tls(); // Look up the runtime for the current context.
    if (!runtime) { // Reject calls made when no runtime is available.
        set_last_error("finalize called without runtime"); // Save a readable error message.
        return -1; // Report failure.
    } // End block.
    if (is_master_process(runtime)) { // The master process must not use the worker finalize path.
        set_last_error("pid finalize is worker-only"); // Explain the rule violation.
        return -1; // Report failure.
    } // End block.
    if (!g_tls.active) { // If the worker is already inactive...
        // Finalize is idempotent, so calling it twice is okay.
        return 0; // Treat the second call as success.
    } // End block.

    log_message(runtime, "[worker %d pid=%d] finalize", g_tls.launch_slot, (int)g_tls.pid); // Log that this worker is finalizing.
    // Mark this worker context as inactive now that the callback is done.
    g_tls.active = 0; // Clear the active flag.
    // Remember that finalize was reached at least once.
    g_tls.finalized = 1; // Mark the worker as finalized.
    return 0; // Report success.
} // End block.

// Returns the simulated world size visible from the current context.
int mpi_sim_pid_comm_size(void) // Function definition starts here.
 { // Begin block.
    // Read the world size from whichever runtime is active for this context.
    mpi_sim_pid_runtime_t *runtime = runtime_from_tls(); // Get the runtime for the current context.
    // Return -1 when no runtime has been associated with this process.
    return runtime ? runtime->world_size : -1; // Return the configured world size, or -1 if unavailable.
} // End block.

// Returns the launch slot for the current worker, or -1 if no worker is active.
int mpi_sim_pid_comm_rank(void) // Function definition starts here.
 { // Begin block.
    // The launch slot acts like an MPI rank in this simulator.
    return g_tls.active ? g_tls.launch_slot : -1; // Return the current worker's launch slot, or -1 if no worker is active.
} // End block.

// Returns the real OS process ID for this process.
pid_t mpi_sim_pid_os_pid(void) // Function definition starts here.
 { // Begin block.
    // getpid() is the POSIX call that asks the OS for the current PID.
    return getpid(); // Return the real operating-system process ID.
} // End block.
