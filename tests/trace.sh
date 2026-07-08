#End to end testing for trace.cpp. This test is not run by default, but can be run manually with `make test-trace`.
#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tmpdir="$(mktemp -d "${TMPDIR:-/tmp}/trace-tests.XXXXXX")"

cleanup() {
    rm -rf "$tmpdir"
}
trap cleanup EXIT

cat >"$tmpdir/trace_test.cpp" <<'CPP'
#include "trace.h"

#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <regex>
#include <string>
#include <system_error>
#include <thread>

namespace fs = std::filesystem;

static std::string read_file(const fs::path &path)
{
    std::ifstream in(path);
    if (!in)
    {
        std::cerr << "failed to open " << path << " for reading\n";
        std::exit(1);
    }

    return std::string((std::istreambuf_iterator<char>(in)), std::istreambuf_iterator<char>());
}

static void expect(bool condition, const char *message)
{
    if (!condition)
    {
        std::cerr << "trace test failed: " << message << "\n";
        std::exit(1);
    }
}

static long long parse_timestamp(const std::smatch &match, std::size_t index)
{
    return std::stoll(match[index].str());
}

int main(int argc, char **argv)
{
    if (argc != 2)
    {
        std::cerr << "usage: trace_test <tmpdir>\n";
        return 1;
    }

    const fs::path workdir = argv[1];
    std::error_code ec;

    // trace_close() should be a safe no-op before initialization.
    trace_close();

    // trace_event() should also be a no-op before trace_init().
    const fs::path no_init_file = workdir / "no_init.json";
    fs::remove(no_init_file, ec);
    trace_event("ignored", "Runtime", "i", 1, 2);
    expect(!fs::exists(no_init_file), "trace_event created output before trace_init");

    // Case 1: one event should create a valid Chrome trace JSON file.
    const fs::path single_event_file = workdir / "single_event.json";
    trace_init(single_event_file.c_str());
    trace_event("single", "Runtime", "i", 7, 9);
    trace_close();
    trace_close(); // repeated close should remain harmless

    const std::string single_content = read_file(single_event_file);
    std::smatch single_match;
    const std::regex single_pattern(
        R"(^\[\n\{"name":"single","cat":"Runtime","ph":"i","ts":([0-9]+),"pid":7,"tid":9\}\n\]\n$)");
    expect(std::regex_match(single_content, single_match, single_pattern),
           "single-event trace did not match expected JSON layout");
    const long long single_ts = parse_timestamp(single_match, 1);
    expect(single_ts >= 0, "single-event timestamp was negative");

    // Case 2: multiple events should be comma-separated and timestamps should move forward.
    const fs::path multi_event_file = workdir / "multi_event.json";
    trace_init(multi_event_file.c_str());
    trace_event("first", "Runtime", "i", 3, 4);
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
    trace_event("second", "Runtime", "i", 3, 4);
    trace_close();

    const std::string multi_content = read_file(multi_event_file);
    std::smatch multi_match;
    const std::regex multi_pattern(
        R"(^\[\n\{"name":"first","cat":"Runtime","ph":"i","ts":([0-9]+),"pid":3,"tid":4\},\n\{"name":"second","cat":"Runtime","ph":"i","ts":([0-9]+),"pid":3,"tid":4\}\n\]\n$)");
    expect(std::regex_match(multi_content, multi_match, multi_pattern),
           "multi-event trace did not match expected comma-separated JSON layout");
    const long long first_ts = parse_timestamp(multi_match, 1);
    const long long second_ts = parse_timestamp(multi_match, 2);
    expect(first_ts >= 0, "first multi-event timestamp was negative");
    expect(second_ts >= first_ts, "second multi-event timestamp did not advance");

    // Case 3: re-initializing after close should truncate the file and start a fresh trace.
    trace_init(multi_event_file.c_str());
    trace_event("fresh", "Runtime", "i", 11, 12);
    trace_close();

    const std::string reopened_content = read_file(multi_event_file);
    std::smatch reopened_match;
    const std::regex reopened_pattern(
        R"(^\[\n\{"name":"fresh","cat":"Runtime","ph":"i","ts":([0-9]+),"pid":11,"tid":12\}\n\]\n$)");
    expect(std::regex_match(reopened_content, reopened_match, reopened_pattern),
           "trace_init did not truncate the old file contents on reopen");

    std::cout << "trace.cpp functional tests passed\n";
    return 0;
}
CPP

c++ -std=c++17 -Wall -Wextra -Werror -I"$repo_root/include" -I"$repo_root/src" \
    "$repo_root/src/trace.cpp" "$tmpdir/trace_test.cpp" \
    -pthread -o "$tmpdir/trace_test"

"$tmpdir/trace_test" "$tmpdir"
