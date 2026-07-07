#include "mpi_sim.h"
#include "trace.h"

// These headers support the runtime's error messages, threads, formatting, allocation, and filesystem checks.
#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

// This struct is the hidden runtime state behind the opaque handle from the header.
// It stores everything the simulator needs while it is running.
struct mpi_sim_runtime {
    int world_size;                       // Number of simulated ranks.
    int debug_enabled;                    // Whether logging is active.
    char log_dir[256];                    // Directory where logs live.
    char log_file[256];                   // Full log file path.
    FILE *log_fp;                         // Open log file handle.
    pthread_mutex_t log_mutex;            // Protects log writes.
    pthread_mutex_t runtime_mutex;        // Protects runtime counters/state.
    pthread_mutex_t barrier_mutex;        // Protects the barrier state.
    pthread_cond_t barrier_cond;          // Wakes ranks waiting at the barrier.
    int barrier_expected;                 // How many ranks must reach the barrier.
    int barrier_count;                    // How many ranks are currently waiting.
    int barrier_generation;               // Barrier round counter.
    int initialized_threads;             // How many rank threads initialized.
    int *thread_initialized;             // Per-rank initialization flags.
    pthread_t *threads;                  // OS thread handles for each rank.
    pthread_mutex_t *mailbox_mutexes;     // One mutex per rank mailbox.
    pthread_cond_t *mailbox_conds;       // One condition variable per mailbox.
    mpi_sim_message_t **mailboxes;        // Per-rank linked-list queues of messages.
};

// This struct is thread-local state for the current simulated rank.
typedef struct mpi_sim_tls {
    mpi_sim_runtime_t *runtime;          // Runtime this thread belongs to.
    int rank;                            // Rank id for this thread.
    int active;                          // Whether the thread is inside the simulator.
} mpi_sim_tls_t;

// This struct bundles the arguments needed when starting a worker thread.
typedef struct mpi_sim_thread_ctx {
    mpi_sim_runtime_t *runtime;          // Shared runtime pointer.
    int rank;                            // Rank assigned to this worker.
    mpi_sim_entry_fn entry;              // User callback to execute.
    void *user_data;                     // Arbitrary user payload.
} mpi_sim_thread_ctx_t;

// Each OS thread gets its own TLS copy so ranks do not overwrite each other's state.
static __thread mpi_sim_tls_t g_tls = {0};

// Each thread keeps its own last error text.
static __thread char g_last_error[256] = {0};

// Fallback runtime pointer used when code runs outside an active rank thread.
static mpi_sim_runtime_t *g_runtime = NULL;

// Formats an error message into the current thread's error buffer.
static void set_last_error(const char *fmt, ...) {
    va_list args;                        // Variable argument list for formatting.
    va_start(args, fmt);                 // Start reading the extra arguments.
    vsnprintf(g_last_error, sizeof(g_last_error), fmt, args); // Write formatted text safely.
    va_end(args);                        // Finish using the variable argument list.
}

// Returns the last error text for the current thread.
const char *mpi_sim_last_error(void) {
    return g_last_error[0] ? g_last_error : "no error"; // Default text when nothing was recorded.
}

// Creates the log directory if it is missing.
static void ensure_log_dir(const char *dir) {
    if (!dir || !dir[0]) {               // Skip empty or missing paths.
        return;
    }

    struct stat st;                      // File metadata holder.
    if (stat(dir, &st) == 0 && S_ISDIR(st.st_mode)) { // Directory already exists.
        return;
    }

    mkdir(dir, 0755);                    // Best-effort directory creation.
}

// Writes one log line when debug logging is enabled.
static void log_message(mpi_sim_runtime_t *runtime, const char *fmt, ...) {
    if (!runtime || !runtime->debug_enabled || !runtime->log_fp) { // Logging disabled or unavailable.
        return;
    }

    pthread_mutex_lock(&runtime->log_mutex); // Keep log lines from interleaving.
    va_list args;                             // Variable arguments for the format string.
    va_start(args, fmt);                      // Start formatting.
    vfprintf(runtime->log_fp, fmt, args);     // Print the formatted line.
    va_end(args);                             // Finish formatting.
    fputc('\n', runtime->log_fp);             // Add a newline so each log entry is separate.
    fflush(runtime->log_fp);                  // Push the line to disk immediately.
    pthread_mutex_unlock(&runtime->log_mutex); // Release the log lock.
}

// Frees a linked list of queued messages.
static void free_message_queue(mpi_sim_message_t *head) {
    while (head) {                       // Walk until the list ends.
        mpi_sim_message_t *next = head->next; // Save next before freeing current node.
        free(head->data);                // Free payload memory.
        free(head);                      // Free the message node itself.
        head = next;                     // Move to the next node.
    }
}

// Appends one message to the destination rank's queue.
static void push_message(mpi_sim_runtime_t *runtime, int dest, mpi_sim_message_t *message) {
    mpi_sim_message_t **head = &runtime->mailboxes[dest]; // Pointer to the mailbox head pointer.
    if (!*head) {                        // Empty queue case.
        *head = message;                 // Put the message in as the first node.
        return;
    }

    mpi_sim_message_t *tail = *head;     // Start at the first node.
    while (tail->next) {                 // Walk to the end.
        tail = tail->next;
    }
    tail->next = message;                // Link the new node at the end.
}

// Finds and removes the first matching message from a mailbox.
// Negative source or tag means "match anything".
static mpi_sim_message_t *find_message(mpi_sim_message_t **head, int source, int tag) {
    mpi_sim_message_t *prev = NULL;      // Previous node while searching.
    mpi_sim_message_t *cur = *head;       // Current node under inspection.

    while (cur) {                        // Keep scanning until the queue ends.
        int source_match = (source < 0 || cur->source == source); // Match exact source or wildcard.
        int tag_match = (tag < 0 || cur->tag == tag);             // Match exact tag or wildcard.
        if (source_match && tag_match) { // Found a usable message.
            if (prev) {                  // Removing from the middle or end.
                prev->next = cur->next;
            } else {                     // Removing the first node.
                *head = cur->next;
            }
            cur->next = NULL;            // Detach returned node from the queue.
            return cur;                  // Hand the message back to the caller.
        }
        prev = cur;                      // Advance the previous pointer.
        cur = cur->next;                 // Advance to the next node.
    }

    return NULL;                         // Nothing matched.
}

// Picks the runtime associated with the current thread if available.
static mpi_sim_runtime_t *runtime_from_tls(void) {
    if (g_tls.active) {                  // Active worker thread case.
        return g_tls.runtime;
    }
    return g_runtime;                    // Fallback for non-worker contexts.
}

// Allocates and initializes a new runtime object.
mpi_sim_runtime_t *mpi_sim_runtime_create(const mpi_sim_config_t *config) {
    if (!config || config->world_size <= 0) { // Reject missing or invalid setup.
        set_last_error("invalid runtime configuration");
        return NULL;
    }

    mpi_sim_runtime_t *runtime = (mpi_sim_runtime_t *)calloc(1, sizeof(*runtime)); // Zeroed runtime allocation.
    if (!runtime) {                     // Allocation failure.
        set_last_error("out of memory creating runtime");
        return NULL;
    }

    runtime->world_size = config->world_size; // Copy world size from config.
    runtime->debug_enabled = config->debug_enabled; // Copy debug flag.
    strncpy(runtime->log_dir, config->log_dir ? config->log_dir : "logs", sizeof(runtime->log_dir) - 1); // Default log dir.
    strncpy(runtime->log_file, config->log_file ? config->log_file : "logs/mpi_sim.log", sizeof(runtime->log_file) - 1); // Default log file.

    pthread_mutex_init(&runtime->log_mutex, NULL);     // Set up log locking.
    pthread_mutex_init(&runtime->runtime_mutex, NULL);  // Set up runtime-state locking.
    pthread_mutex_init(&runtime->barrier_mutex, NULL);  // Set up barrier locking.
    pthread_cond_init(&runtime->barrier_cond, NULL);    // Set up barrier wakeups.
    runtime->barrier_expected = runtime->world_size;    // All ranks must reach the barrier.

    runtime->thread_initialized = (int *)calloc((size_t)runtime->world_size, sizeof(int));                // Per-rank init flags.
    runtime->threads = (pthread_t *)calloc((size_t)runtime->world_size, sizeof(pthread_t));               // Per-rank thread handles.
    runtime->mailbox_mutexes = (pthread_mutex_t *)calloc((size_t)runtime->world_size, sizeof(pthread_mutex_t)); // Per-rank mailbox locks.
    runtime->mailbox_conds = (pthread_cond_t *)calloc((size_t)runtime->world_size, sizeof(pthread_cond_t));     // Per-rank mailbox wakeups.
    runtime->mailboxes = (mpi_sim_message_t **)calloc((size_t)runtime->world_size, sizeof(mpi_sim_message_t *)); // Per-rank queues.

    if (!runtime->thread_initialized || !runtime->threads || !runtime->mailbox_mutexes ||
        !runtime->mailbox_conds || !runtime->mailboxes) { // Check every allocation.
        set_last_error("out of memory creating runtime resources");
        mpi_sim_runtime_destroy(runtime); // Clean up partially created runtime.
        return NULL;
    }

    for (int i = 0; i < runtime->world_size; ++i) { // Initialize each rank's mailbox primitives.
        pthread_mutex_init(&runtime->mailbox_mutexes[i], NULL);
        pthread_cond_init(&runtime->mailbox_conds[i], NULL);
    }

    ensure_log_dir(runtime->log_dir);    // Make sure the log directory exists.
    runtime->log_fp = fopen(runtime->log_file, "a"); // Open log file in append mode.
    if (!runtime->log_fp) {              // Handle file open failure.
        set_last_error("unable to open log file '%s': %s", runtime->log_file, strerror(errno));
        mpi_sim_runtime_destroy(runtime); // Clean up if logging cannot start.
        return NULL;
    }

    g_runtime = runtime;

    log_message(runtime,
                "[runtime] created world_size=%d debug=%d",
                runtime->world_size,
                runtime->debug_enabled);
    
    trace_init("trace.json");
    
    return runtime;
}

// Destroys the runtime and all resources it owns.
void mpi_sim_runtime_destroy(mpi_sim_runtime_t *runtime) {
    if (!runtime) {                      // Nothing to destroy.
        return;
    }

    if (runtime->log_fp) {               // Log destruction if logging is active.
        log_message(runtime, "[runtime] destroying");
    }

    if (runtime->mailboxes) {            // Free queued messages for each rank.
        for (int i = 0; i < runtime->world_size; ++i) {
            free_message_queue(runtime->mailboxes[i]);
        }
    }

    if (runtime->mailbox_mutexes) {      // Destroy each mailbox mutex.
        for (int i = 0; i < runtime->world_size; ++i) {
            pthread_mutex_destroy(&runtime->mailbox_mutexes[i]);
        }
    }

    if (runtime->mailbox_conds) {        // Destroy each mailbox condition variable.
        for (int i = 0; i < runtime->world_size; ++i) {
            pthread_cond_destroy(&runtime->mailbox_conds[i]);
        }
    }

    free(runtime->thread_initialized);   // Free init flags.
    free(runtime->threads);              // Free thread handles array.
    free(runtime->mailbox_mutexes);      // Free mailbox mutex array.
    free(runtime->mailbox_conds);        // Free mailbox condition array.
    free(runtime->mailboxes);            // Free mailbox queue pointers.

    if (runtime->log_fp) {               // Close log file if it was opened.
        fclose(runtime->log_fp);
    }

    pthread_mutex_destroy(&runtime->log_mutex);     // Destroy log mutex.
    pthread_mutex_destroy(&runtime->runtime_mutex);  // Destroy runtime mutex.
    pthread_mutex_destroy(&runtime->barrier_mutex);  // Destroy barrier mutex.
    pthread_cond_destroy(&runtime->barrier_cond);    // Destroy barrier condition variable.

    if (g_runtime == runtime) {          // Clear global pointer if it refers to this runtime.
        g_runtime = NULL;
    }
    trace_close();

    free(runtime);                       // Finally free the runtime object itself.
}

// Entry function for each OS thread representing one simulated rank.
static void *thread_entry(void *arg) {
    mpi_sim_thread_ctx_t *ctx = (mpi_sim_thread_ctx_t *)arg; // Recover the thread context.
    if (mpi_sim_init(ctx->runtime, ctx->rank) != 0) {        // Initialize this rank first.
        log_message(ctx->runtime, "[rank %d] init failed: %s", ctx->rank, mpi_sim_last_error()); // Report failure.
        return NULL;                                          // Stop this thread.
    }
    trace_event(
        "THREAD_START",
        "Runtime",
        "i",
        ctx->rank,
        0
    );

    log_message(ctx->runtime, "[rank %d] started", ctx->rank); // Note start of user code.
    ctx->entry(ctx->user_data);                                // Call the user's function.
    log_message(ctx->runtime, "[rank %d] finished", ctx->rank); // Note completion.

    g_tls.active = 0;                                          // Mark this thread as inactive.
    return NULL;                                                // Thread exits cleanly.
}

// Initializes simulator state for one rank.
int mpi_sim_init(mpi_sim_runtime_t *runtime, int rank) {
    if (!runtime || rank < 0 || rank >= runtime->world_size) { // Validate rank and runtime.
        set_last_error("invalid rank during init");
        return -1;
    }

    g_tls.runtime = runtime;           // Remember the runtime in thread-local storage.
    g_tls.rank = rank;                 // Remember which rank this thread represents.
    g_tls.active = 1;                  // Mark the thread as active inside the simulator.

    pthread_mutex_lock(&runtime->runtime_mutex); // Protect shared counters.
    runtime->thread_initialized[rank] = 1;       // Record that this rank is ready.
    runtime->initialized_threads++;              // Count one more initialized thread.
    pthread_mutex_unlock(&runtime->runtime_mutex); // Release the shared lock.

    log_message(runtime, "[rank %d] init complete", rank); // Debug trace.
    return 0;                                         // Success.
}

// Marks the current simulated rank as finished.
int mpi_sim_finalize(void) {
    mpi_sim_runtime_t *runtime = runtime_from_tls(); // Find the current runtime.
    if (!runtime) {                                  // Reject calls without active runtime.
        set_last_error("finalize called without runtime");
        return -1;
    }

    log_message(runtime, "[rank %d] finalize", g_tls.rank); // Log the finalization step.
    g_tls.active = 0;                                         // Mark the thread inactive.
    return 0;                                                 // Success.
}

// Returns how many ranks the runtime was configured to simulate.
int mpi_sim_comm_size(void) {
    mpi_sim_runtime_t *runtime = runtime_from_tls(); // Get active runtime if possible.
    return runtime ? runtime->world_size : -1;       // Return the size or an error value.
}

// Returns the current simulated rank for this thread.
int mpi_sim_comm_rank(void) {
    return g_tls.active ? g_tls.rank : -1;           // No active rank means "unknown".
}

// Sends a copy of the given buffer to another rank.
int mpi_sim_send(const void *buffer, size_t count, int destination, int tag) {
    mpi_sim_runtime_t *runtime = runtime_from_tls(); // Find the active runtime.
    if (!runtime || !buffer) {                       // Require both runtime and payload.
        set_last_error("send called with invalid arguments");
        return -1;
    }
    if (destination < 0 || destination >= runtime->world_size) { // Validate destination rank.
        set_last_error("send destination out of range");
        return -1;
    }

    mpi_sim_message_t *message = (mpi_sim_message_t *)calloc(1, sizeof(*message)); // Allocate a new message node.
    if (!message) {                                      // Allocation failed.
        set_last_error("out of memory allocating message");
        return -1;
    }

    if (count > 0) {                                     // Only copy payload when there is one.
        message->data = malloc(count);                   // Allocate payload storage.
        if (!message->data) {                            // Payload allocation failed.
            free(message);                               // Free the node before returning.
            set_last_error("out of memory copying message payload");
            return -1;
        }
        memcpy(message->data, buffer, count);            // Copy the payload bytes.
    }
    message->size = count;                               // Record payload size.
    message->source = mpi_sim_comm_rank();               // Record sender rank.
    message->tag = tag;                                  // Record message tag.

    pthread_mutex_lock(&runtime->mailbox_mutexes[destination]); // Lock the destination mailbox.
    push_message(runtime, destination, message);                  // Enqueue the new message.
    pthread_cond_signal(&runtime->mailbox_conds[destination]);    // Wake one waiting receiver.
    pthread_mutex_unlock(&runtime->mailbox_mutexes[destination]);  // Unlock the mailbox.

    log_message(runtime, "[rank %d] send -> %d tag=%d size=%zu", message->source, destination, tag, count); // Trace send.
    return 0;                                                    // Success.
}

// Receives a matching message, copies its payload into the caller's buffer, and optionally returns status.
int mpi_sim_recv(void *buffer, size_t count, int source, int tag, mpi_sim_status_t *status) {
    mpi_sim_runtime_t *runtime = runtime_from_tls(); // Get active runtime.
    if (!runtime || !buffer) {                      // Validate inputs.
        set_last_error("recv called with invalid arguments");
        return -1;
    }

    int rank = mpi_sim_comm_rank();                 // Identify this receiver.
    pthread_mutex_lock(&runtime->mailbox_mutexes[rank]); // Lock this rank's mailbox.

    mpi_sim_message_t *message = NULL;              // Placeholder for a matching message.
    while ((message = find_message(&runtime->mailboxes[rank], source, tag)) == NULL) { // Wait until one appears.
        pthread_cond_wait(&runtime->mailbox_conds[rank], &runtime->mailbox_mutexes[rank]); // Sleep until signaled.
    }

    pthread_mutex_unlock(&runtime->mailbox_mutexes[rank]); // Done with mailbox queue.

    size_t copy_size = message->size < count ? message->size : count; // Copy only what fits.
    memcpy(buffer, message->data, copy_size);                               // Move bytes into user buffer.

    if (status) {                                                           // Fill status if caller provided one.
        status->source = message->source;
        status->tag = message->tag;
        status->count = message->size;
    }

    log_message(runtime, "[rank %d] recv <- %d tag=%d size=%zu", rank, message->source, message->tag, message->size); // Trace receive.
    free(message->data);                                                     // Free payload copy.
    free(message);                                                           // Free message node.
    return 0;                                                                // Success.
}

// Broadcasts a buffer from the root rank to every other rank.
int mpi_sim_bcast(void *buffer, size_t count, int root) {
    mpi_sim_runtime_t *runtime = runtime_from_tls(); // Get active runtime.
    if (!runtime || !buffer) {                      // Validate inputs.
        set_last_error("bcast called with invalid arguments");
        return -1;
    }

    int rank = mpi_sim_comm_rank();                 // Determine who we are.
    if (rank == root) {                             // Root rank sends to everyone else.
        for (int i = 0; i < runtime->world_size; ++i) { // Loop over every rank.
            if (i == root) {                        // Skip self-send.
                continue;
            }
            if (mpi_sim_send(buffer, count, i, 0) != 0) { // Forward the data to each rank.
                return -1;                          // Stop if any send fails.
            }
        }
        log_message(runtime, "[rank %d] broadcasted %zu bytes", rank, count); // Record the broadcast.
        return 0;                                   // Root finished successfully.
    }

    return mpi_sim_recv(buffer, count, root, 0, NULL); // Non-root ranks wait for the root message.
}

// Synchronization point: no rank may continue until every rank arrives here.
int mpi_sim_barrier(void) {
    mpi_sim_runtime_t *runtime = runtime_from_tls(); // Get active runtime.
    if (!runtime) {                                 // Require simulator state.
        set_last_error("barrier not ready");
        return -1;
    }

    log_message(runtime, "[rank %d] entering barrier", mpi_sim_comm_rank()); // Log arrival.
    pthread_mutex_lock(&runtime->barrier_mutex);   // Protect barrier counters.
    int generation = runtime->barrier_generation;  // Remember the current barrier round.
    runtime->barrier_count++;                      // One more rank has arrived.
    if (runtime->barrier_count == runtime->barrier_expected) { // Last rank to arrive.
        runtime->barrier_generation++;             // Advance to the next barrier round.
        runtime->barrier_count = 0;                // Reset the waiting count.
        pthread_cond_broadcast(&runtime->barrier_cond); // Wake everyone waiting.
    } else {
        while (generation == runtime->barrier_generation) { // Wait until the round changes.
            pthread_cond_wait(&runtime->barrier_cond, &runtime->barrier_mutex); // Sleep until released.
        }
    }
    pthread_mutex_unlock(&runtime->barrier_mutex); // Release the barrier lock.
    log_message(runtime, "[rank %d] leaving barrier", mpi_sim_comm_rank()); // Log exit.
    return 0;                                      // Success.
}

// Launches one worker thread per rank, runs the user callback, and waits for all workers to finish.
int mpi_sim_run(mpi_sim_runtime_t *runtime, mpi_sim_entry_fn entry, void *user_data) {
    if (!runtime || !entry) {                      // Validate required inputs.
        set_last_error("invalid run arguments");
        return -1;
    }

    mpi_sim_thread_ctx_t *contexts = (mpi_sim_thread_ctx_t *)calloc((size_t)runtime->world_size, sizeof(*contexts)); // Thread argument array.
    if (!contexts) {                               // Allocation failed.
        set_last_error("out of memory creating thread contexts");
        return -1;
    }

    int created = 0;                               // Track how many threads were successfully launched.
    for (int i = 0; i < runtime->world_size; ++i) { // Create one thread per rank.
        contexts[i].runtime = runtime;             // Shared runtime pointer.
        contexts[i].rank = i;                      // Rank number for this worker.
        contexts[i].entry = entry;                 // User callback to run.
        contexts[i].user_data = user_data;         // Same user data for each worker.
        if (pthread_create(&runtime->threads[i], NULL, thread_entry, &contexts[i]) != 0) { // Start worker thread.
            set_last_error("failed to create thread %d", i); // Report which thread failed.
            created = i;                           // Only the earlier threads exist.
            for (int j = 0; j < created; ++j) {    // Join threads that already started.
                pthread_join(runtime->threads[j], NULL);
            }
            free(contexts);                        // Release temporary thread contexts.
            return -1;                             // Stop on failure.
        }
        created = i + 1;                           // Count this thread as created.
    }

    for (int i = 0; i < created; ++i) {            // Wait for every worker to finish.
        pthread_join(runtime->threads[i], NULL);
    }

    free(contexts);                                // Release the context array.
    return 0;                                      // Success.
}
