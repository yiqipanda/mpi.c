#include "mpi_sim.h"

#include <errno.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/types.h>

struct mpi_sim_runtime {
    int world_size;
    int debug_enabled;
    char log_dir[256];
    char log_file[256];
    FILE *log_fp;
    pthread_mutex_t log_mutex;
    pthread_mutex_t runtime_mutex;
    pthread_mutex_t barrier_mutex;
    pthread_cond_t barrier_cond;
    int barrier_expected;
    int barrier_count;
    int barrier_generation;
    int initialized_threads;
    int *thread_initialized;
    pthread_t *threads;
    pthread_mutex_t *mailbox_mutexes;
    pthread_cond_t *mailbox_conds;
    mpi_sim_message_t **mailboxes;
};

typedef struct mpi_sim_tls {
    mpi_sim_runtime_t *runtime;
    int rank;
    int active;
} mpi_sim_tls_t;

typedef struct mpi_sim_thread_ctx {
    mpi_sim_runtime_t *runtime;
    int rank;
    mpi_sim_entry_fn entry;
    void *user_data;
} mpi_sim_thread_ctx_t;

static __thread mpi_sim_tls_t g_tls = {0};
static __thread char g_last_error[256] = {0};
static mpi_sim_runtime_t *g_runtime = NULL;

static void set_last_error(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(g_last_error, sizeof(g_last_error), fmt, args);
    va_end(args);
}

const char *mpi_sim_last_error(void) {
    return g_last_error[0] ? g_last_error : "no error";
}

static void ensure_log_dir(const char *dir) {
    if (!dir || !dir[0]) {
        return;
    }

    struct stat st;
    if (stat(dir, &st) == 0 && S_ISDIR(st.st_mode)) {
        return;
    }

    mkdir(dir, 0755);
}

static void log_message(mpi_sim_runtime_t *runtime, const char *fmt, ...) {
    if (!runtime || !runtime->debug_enabled || !runtime->log_fp) {
        return;
    }

    pthread_mutex_lock(&runtime->log_mutex);
    va_list args;
    va_start(args, fmt);
    vfprintf(runtime->log_fp, fmt, args);
    va_end(args);
    fputc('\n', runtime->log_fp);
    fflush(runtime->log_fp);
    pthread_mutex_unlock(&runtime->log_mutex);
}

static void free_message_queue(mpi_sim_message_t *head) {
    while (head) {
        mpi_sim_message_t *next = head->next;
        free(head->data);
        free(head);
        head = next;
    }
}

static void push_message(mpi_sim_runtime_t *runtime, int dest, mpi_sim_message_t *message) {
    mpi_sim_message_t **head = &runtime->mailboxes[dest];
    if (!*head) {
        *head = message;
        return;
    }

    mpi_sim_message_t *tail = *head;
    while (tail->next) {
        tail = tail->next;
    }
    tail->next = message;
}

static mpi_sim_message_t *find_message(mpi_sim_message_t **head, int source, int tag) {
    mpi_sim_message_t *prev = NULL;
    mpi_sim_message_t *cur = *head;

    while (cur) {
        int source_match = (source < 0 || cur->source == source);
        int tag_match = (tag < 0 || cur->tag == tag);
        if (source_match && tag_match) {
            if (prev) {
                prev->next = cur->next;
            } else {
                *head = cur->next;
            }
            cur->next = NULL;
            return cur;
        }
        prev = cur;
        cur = cur->next;
    }

    return NULL;
}

static mpi_sim_runtime_t *runtime_from_tls(void) {
    if (g_tls.active) {
        return g_tls.runtime;
    }
    return g_runtime;
}

mpi_sim_runtime_t *mpi_sim_runtime_create(const mpi_sim_config_t *config) {
    if (!config || config->world_size <= 0) {
        set_last_error("invalid runtime configuration");
        return NULL;
    }

    mpi_sim_runtime_t *runtime = (mpi_sim_runtime_t *)calloc(1, sizeof(*runtime));
    if (!runtime) {
        set_last_error("out of memory creating runtime");
        return NULL;
    }

    runtime->world_size = config->world_size;
    runtime->debug_enabled = config->debug_enabled;
    strncpy(runtime->log_dir, config->log_dir ? config->log_dir : "logs", sizeof(runtime->log_dir) - 1);
    strncpy(runtime->log_file, config->log_file ? config->log_file : "logs/mpi_sim.log", sizeof(runtime->log_file) - 1);

    pthread_mutex_init(&runtime->log_mutex, NULL);
    pthread_mutex_init(&runtime->runtime_mutex, NULL);
    pthread_mutex_init(&runtime->barrier_mutex, NULL);
    pthread_cond_init(&runtime->barrier_cond, NULL);
    runtime->barrier_expected = runtime->world_size;

    runtime->thread_initialized = (int *)calloc((size_t)runtime->world_size, sizeof(int));
    runtime->threads = (pthread_t *)calloc((size_t)runtime->world_size, sizeof(pthread_t));
    runtime->mailbox_mutexes = (pthread_mutex_t *)calloc((size_t)runtime->world_size, sizeof(pthread_mutex_t));
    runtime->mailbox_conds = (pthread_cond_t *)calloc((size_t)runtime->world_size, sizeof(pthread_cond_t));
    runtime->mailboxes = (mpi_sim_message_t **)calloc((size_t)runtime->world_size, sizeof(mpi_sim_message_t *));

    if (!runtime->thread_initialized || !runtime->threads || !runtime->mailbox_mutexes ||
        !runtime->mailbox_conds || !runtime->mailboxes) {
        set_last_error("out of memory creating runtime resources");
        mpi_sim_runtime_destroy(runtime);
        return NULL;
    }

    for (int i = 0; i < runtime->world_size; ++i) {
        pthread_mutex_init(&runtime->mailbox_mutexes[i], NULL);
        pthread_cond_init(&runtime->mailbox_conds[i], NULL);
    }

    ensure_log_dir(runtime->log_dir);
    runtime->log_fp = fopen(runtime->log_file, "a");
    if (!runtime->log_fp) {
        set_last_error("unable to open log file '%s': %s", runtime->log_file, strerror(errno));
        mpi_sim_runtime_destroy(runtime);
        return NULL;
    }

    g_runtime = runtime;
    log_message(runtime, "[runtime] created world_size=%d debug=%d", runtime->world_size, runtime->debug_enabled);
    return runtime;
}

void mpi_sim_runtime_destroy(mpi_sim_runtime_t *runtime) {
    if (!runtime) {
        return;
    }

    if (runtime->log_fp) {
        log_message(runtime, "[runtime] destroying");
    }

    if (runtime->mailboxes) {
        for (int i = 0; i < runtime->world_size; ++i) {
            free_message_queue(runtime->mailboxes[i]);
        }
    }

    if (runtime->mailbox_mutexes) {
        for (int i = 0; i < runtime->world_size; ++i) {
            pthread_mutex_destroy(&runtime->mailbox_mutexes[i]);
        }
    }

    if (runtime->mailbox_conds) {
        for (int i = 0; i < runtime->world_size; ++i) {
            pthread_cond_destroy(&runtime->mailbox_conds[i]);
        }
    }

    free(runtime->thread_initialized);
    free(runtime->threads);
    free(runtime->mailbox_mutexes);
    free(runtime->mailbox_conds);
    free(runtime->mailboxes);

    if (runtime->log_fp) {
        fclose(runtime->log_fp);
    }

    pthread_mutex_destroy(&runtime->log_mutex);
    pthread_mutex_destroy(&runtime->runtime_mutex);
    pthread_mutex_destroy(&runtime->barrier_mutex);
    pthread_cond_destroy(&runtime->barrier_cond);

    if (g_runtime == runtime) {
        g_runtime = NULL;
    }

    free(runtime);
}

static void *thread_entry(void *arg) {
    mpi_sim_thread_ctx_t *ctx = (mpi_sim_thread_ctx_t *)arg;
    if (mpi_sim_init(ctx->runtime, ctx->rank) != 0) {
        log_message(ctx->runtime, "[rank %d] init failed: %s", ctx->rank, mpi_sim_last_error());
        return NULL;
    }

    log_message(ctx->runtime, "[rank %d] started", ctx->rank);
    ctx->entry(ctx->user_data);
    log_message(ctx->runtime, "[rank %d] finished", ctx->rank);

    g_tls.active = 0;
    return NULL;
}

int mpi_sim_init(mpi_sim_runtime_t *runtime, int rank) {
    if (!runtime || rank < 0 || rank >= runtime->world_size) {
        set_last_error("invalid rank during init");
        return -1;
    }

    g_tls.runtime = runtime;
    g_tls.rank = rank;
    g_tls.active = 1;

    pthread_mutex_lock(&runtime->runtime_mutex);
    runtime->thread_initialized[rank] = 1;
    runtime->initialized_threads++;
    pthread_mutex_unlock(&runtime->runtime_mutex);

    log_message(runtime, "[rank %d] init complete", rank);
    return 0;
}

int mpi_sim_finalize(void) {
    mpi_sim_runtime_t *runtime = runtime_from_tls();
    if (!runtime) {
        set_last_error("finalize called without runtime");
        return -1;
    }

    log_message(runtime, "[rank %d] finalize", g_tls.rank);
    g_tls.active = 0;
    return 0;
}

int mpi_sim_comm_size(void) {
    mpi_sim_runtime_t *runtime = runtime_from_tls();
    return runtime ? runtime->world_size : -1;
}

int mpi_sim_comm_rank(void) {
    return g_tls.active ? g_tls.rank : -1;
}

int mpi_sim_send(const void *buffer, size_t count, int destination, int tag) {
    mpi_sim_runtime_t *runtime = runtime_from_tls();
    if (!runtime || !buffer) {
        set_last_error("send called with invalid arguments");
        return -1;
    }
    if (destination < 0 || destination >= runtime->world_size) {
        set_last_error("send destination out of range");
        return -1;
    }

    mpi_sim_message_t *message = (mpi_sim_message_t *)calloc(1, sizeof(*message));
    if (!message) {
        set_last_error("out of memory allocating message");
        return -1;
    }

    if (count > 0) {
        message->data = malloc(count);
        if (!message->data) {
            free(message);
            set_last_error("out of memory copying message payload");
            return -1;
        }
        memcpy(message->data, buffer, count);
    }
    message->size = count;
    message->source = mpi_sim_comm_rank();
    message->tag = tag;

    pthread_mutex_lock(&runtime->mailbox_mutexes[destination]);
    push_message(runtime, destination, message);
    pthread_cond_signal(&runtime->mailbox_conds[destination]);
    pthread_mutex_unlock(&runtime->mailbox_mutexes[destination]);

    log_message(runtime, "[rank %d] send -> %d tag=%d size=%zu", message->source, destination, tag, count);
    return 0;
}

int mpi_sim_recv(void *buffer, size_t count, int source, int tag, mpi_sim_status_t *status) {
    mpi_sim_runtime_t *runtime = runtime_from_tls();
    if (!runtime || !buffer) {
        set_last_error("recv called with invalid arguments");
        return -1;
    }

    int rank = mpi_sim_comm_rank();
    pthread_mutex_lock(&runtime->mailbox_mutexes[rank]);

    mpi_sim_message_t *message = NULL;
    while ((message = find_message(&runtime->mailboxes[rank], source, tag)) == NULL) {
        pthread_cond_wait(&runtime->mailbox_conds[rank], &runtime->mailbox_mutexes[rank]);
    }

    pthread_mutex_unlock(&runtime->mailbox_mutexes[rank]);

    size_t copy_size = message->size < count ? message->size : count;
    memcpy(buffer, message->data, copy_size);

    if (status) {
        status->source = message->source;
        status->tag = message->tag;
        status->count = message->size;
    }

    log_message(runtime, "[rank %d] recv <- %d tag=%d size=%zu", rank, message->source, message->tag, message->size);
    free(message->data);
    free(message);
    return 0;
}

int mpi_sim_bcast(void *buffer, size_t count, int root) {
    mpi_sim_runtime_t *runtime = runtime_from_tls();
    if (!runtime || !buffer) {
        set_last_error("bcast called with invalid arguments");
        return -1;
    }

    int rank = mpi_sim_comm_rank();
    if (rank == root) {
        for (int i = 0; i < runtime->world_size; ++i) {
            if (i == root) {
                continue;
            }
            if (mpi_sim_send(buffer, count, i, 0) != 0) {
                return -1;
            }
        }
        log_message(runtime, "[rank %d] broadcasted %zu bytes", rank, count);
        return 0;
    }

    return mpi_sim_recv(buffer, count, root, 0, NULL);
}

int mpi_sim_barrier(void) {
    mpi_sim_runtime_t *runtime = runtime_from_tls();
    if (!runtime) {
        set_last_error("barrier not ready");
        return -1;
    }

    log_message(runtime, "[rank %d] entering barrier", mpi_sim_comm_rank());
    pthread_mutex_lock(&runtime->barrier_mutex);
    int generation = runtime->barrier_generation;
    runtime->barrier_count++;
    if (runtime->barrier_count == runtime->barrier_expected) {
        runtime->barrier_generation++;
        runtime->barrier_count = 0;
        pthread_cond_broadcast(&runtime->barrier_cond);
    } else {
        while (generation == runtime->barrier_generation) {
            pthread_cond_wait(&runtime->barrier_cond, &runtime->barrier_mutex);
        }
    }
    pthread_mutex_unlock(&runtime->barrier_mutex);
    log_message(runtime, "[rank %d] leaving barrier", mpi_sim_comm_rank());
    return 0;
}

int mpi_sim_run(mpi_sim_runtime_t *runtime, mpi_sim_entry_fn entry, void *user_data) {
    if (!runtime || !entry) {
        set_last_error("invalid run arguments");
        return -1;
    }

    mpi_sim_thread_ctx_t *contexts = (mpi_sim_thread_ctx_t *)calloc((size_t)runtime->world_size, sizeof(*contexts));
    if (!contexts) {
        set_last_error("out of memory creating thread contexts");
        return -1;
    }

    int created = 0;
    for (int i = 0; i < runtime->world_size; ++i) {
        contexts[i].runtime = runtime;
        contexts[i].rank = i;
        contexts[i].entry = entry;
        contexts[i].user_data = user_data;
        if (pthread_create(&runtime->threads[i], NULL, thread_entry, &contexts[i]) != 0) {
            set_last_error("failed to create thread %d", i);
            created = i;
            for (int j = 0; j < created; ++j) {
                pthread_join(runtime->threads[j], NULL);
            }
            free(contexts);
            return -1;
        }
        created = i + 1;
    }

    for (int i = 0; i < created; ++i) {
        pthread_join(runtime->threads[i], NULL);
    }

    free(contexts);
    return 0;
}
