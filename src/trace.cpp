// Pull in the trace API declarations from the local header.
#include "trace.h"

// std::chrono gives us a simple way to measure elapsed time.
#include <chrono>
// std::mutex and std::lock_guard protect shared trace state across threads.
#include <mutex>

// File-local globals: static here means "private to this source file".
// nullptr is the C++ null pointer value, like None in Python.
static FILE *g_trace = nullptr;
static std::mutex g_mutex;
static bool g_first = true;

// auto lets the compiler infer the exact clock-time type for us.
static auto g_start = std::chrono::steady_clock::now();

// Return the elapsed time since trace_init() in microseconds.
static long long timestamp_us()
{
    // Bring chrono names into this function so we can write microseconds
    // instead of std::chrono::microseconds every time.
    using namespace std::chrono;

    // Example: 1.5 ms becomes 1500 us.
    return duration_cast<microseconds>(
        steady_clock::now() - g_start
    ).count();
}

// Open the trace file and start a JSON array for Chrome trace viewers.
void trace_init(const char *filename)
{
    // RAII lock: it locks now and unlocks automatically when the function exits.
    std::lock_guard<std::mutex> lock(g_mutex);

    // "w" creates or truncates the file.
    g_trace = fopen(filename, "w");

    if (!g_trace)
        return;

    // Chrome trace format starts with an array.
    fprintf(g_trace, "[\n");

    fflush(g_trace);

    // The next event should be written without a leading comma.
    g_first = true;
    
    // Reset the time origin so timestamps start at 0 for this run.
    g_start = std::chrono::steady_clock::now();
}

// Close the trace file after writing the final JSON array bracket.
void trace_close()
{
    // RAII lock: same pattern as trace_init(), no manual unlock needed.
    std::lock_guard<std::mutex> lock(g_mutex);

    if (!g_trace)
        return;

    // Finish the JSON array.
    fprintf(g_trace, "\n]\n");

    fclose(g_trace);

    g_trace = nullptr;
}

// Write one trace event as a JSON object.
void trace_event(
    const char *name,
    const char *category,
    const char *phase,
    int pid,
    int tid)
{
    // Keep event writes serialized so the JSON stays valid.
    std::lock_guard<std::mutex> lock(g_mutex);

    // If trace_init() was never called, ignore the event.
    if (!g_trace)
        return;

    // JSON objects in an array need commas between them.
    if (!g_first)
        fprintf(g_trace, ",\n");

    g_first = false;

    // Each call writes one Chrome trace event record.
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
