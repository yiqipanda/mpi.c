# Matrix evaluation event sequences

[Open the combined SVG](cost_event_sequences.svg). It places the local run and the two-worker partitioned run in one sequence diagram, using the same colors and arrow style as the diagrams in `diagrams/`.

Regenerate it from the current syslog:

```sh
python3 tools/generate_cost_event_sequences.py
```

The generator reads the newest `logs/syslog-*.log` file by default, or the legacy [`logs/syslog.log`](../logs/syslog.log) if there are no timestamped runs. Pass `--log PATH` to select a particular run. The analysis file referenced in the request is located at [`documentation/cost.md`](../documentation/cost.md) in this checkout. It describes an earlier 4 × 4, three-job run, while the syslog used for the existing SVG records a 15 × 15 local calculation followed by a two-worker partitioned run. All numbers in the existing SVG come from that single run.

The **23.347882 s** local headline is the window from the final worker registration to the first logged event after local evaluation. It includes any unlogged work in that interval; syslog has no exact local evaluation markers. The **12.441634 s** partitioned headline is the logged interval from the first `createFunction` send to the final `getFunctionResult` reply. It excludes object creation, partitioning, and final orchestration. The two headlines have different boundaries and are not an exact speedup ratio.

Worker evaluation times are their logged `perf_counter()` measurements. Send-to-reply durations are wall-clock differences between syslog events and include transport, scheduling, serialization, and handling. Vertical spacing represents event order, not elapsed time.
