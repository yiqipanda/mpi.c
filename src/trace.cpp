#include "trace.h"

#include <chrono>
#include <mutex>

static FILE *g_trace = nullptr;
static std::mutex g_mutex;
static bool g_first = true;

static auto g_start = std::chrono::steady_clock::now();

static long long timestamp_us()
{
    using namespace std::chrono;

    return duration_cast<microseconds>(
        steady_clock::now() - g_start
    ).count();
}

void trace_init(const char *filename)
{
    std::lock_guard<std::mutex> lock(g_mutex);

    g_trace = fopen(filename, "w");

    if (!g_trace)
        return;

    fprintf(g_trace, "[\n");

    fflush(g_trace);

    g_first = true;

    g_start = std::chrono::steady_clock::now();
}

void trace_close()
{
    std::lock_guard<std::mutex> lock(g_mutex);

    if (!g_trace)
        return;

    fprintf(g_trace, "\n]\n");

    fclose(g_trace);

    g_trace = nullptr;
}

void trace_event(
    const char *name,
    const char *category,
    const char *phase,
    int pid,
    int tid)
{
    std::lock_guard<std::mutex> lock(g_mutex);

    if (!g_trace)
        return;

    if (!g_first)
        fprintf(g_trace, ",\n");

    g_first = false;

    fprintf(
        g_trace,
        "{"
        "\"name\":\"%s\","
        "\"cat\":\"%s\","
        "\"ph\":\"%s\","
        "\"ts\":%lld,"
        "\"pid\":%d,"
        "\"tid\":%d"
        "}",
        name,
        category,
        phase,
        timestamp_us(),
        pid,
        tid);

    fflush(g_trace);
}