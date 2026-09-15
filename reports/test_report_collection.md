# Timed Concurrent Chaos Test Report -

## Result

The chaos suite collected 11 pytest cases from four test functions. The final
timed run passed all 11 cases in 71.59 seconds.

Command used:

```bash
python3 -m pytest tests/test_python_only/test_chaos.py -q --durations=0
```

Environment:

- Python 3.14.5
- pytest 9.1.1
- pytest-asyncio 1.4.0
- asyncio subprocesses using the current Python interpreter

Only these task programs are executed for all tests in test_chaos:

- `demo/program1_t1.py`: root main work, approximately 3 seconds
- `demo/program1_t2.py`: first leaf, approximately 2 seconds
- `demo/program1_t3.py`: second leaf, approximately 5 seconds
- `demo/program1_t1_or.py`: root orchestration, approximately 3 seconds

Direct runner-pool malfunction tests were removed. Pool behavior is observed
only when a worker returns a healthy or unhealthy runner during a chaos case.

## Focused Tests

| Test                                 | Injection and assertion                                                                                                                                                                                                                                                                     | Result | Duration |
| ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------ | -------: |
| `test_randomized_resets`           | Seeded selection chooses the root worker after all three subprocesses start. A reset makes that worker return`False`, leaves the root incomplete, permits both children to complete, and returns the runner to the available queue with the expected worker and pool log lines.           | Passed |    5.26s |
| `test_randomized_runner_unhealthy` | Seeded selection chooses the`t2` worker. Its active runner becomes unhealthy, is detected and moved stale, and the worker retries. All three tasks complete and the runner/detection/stale log lines are present.                                                                         | Passed |    8.60s |
| `test_procedure_check_task_fail`   | `program1_t2.py` receives `invalid`, causing its integer conversion to fail. The worker makes `MAX_RETRIES` attempts, returns `False`, leaves the task incomplete, and retains `ValueError` details in each failed runner's error stream. The final worker error line is printed. | Passed |    0.31s |

The task-script exception does not escape `Runner.run()`. The runner converts a
nonzero subprocess return code into `False`; therefore this test asserts the
return contract and captured stderr rather than using `pytest.raises`.

|  |  |  |  |  |
| - | -: | -: | -: | - |

The original child-wait schedule used 3.5 seconds for the unhealthy event. One
run showed this could still overlap the root's main-run monitoring because of
subprocess startup jitter. Moving the events to 4.0 and 4.4 seconds reliably
placed them between the estimated 3-second root completion and 5-second child
completion.

## Log Contracts

The suite captures stdout and verifies the relevant dynamic worker and runner
IDs rather than matching UUID-independent generic messages. Depending on the
scenario, it asserts these lines:

- Worker starts or retries an assigned program.
- Worker detects an unhealthy runner and identifies the retry phase.
- Worker returns its runner.
- Runner pool returns a healthy runner to the available queue.
- Runner pool moves an unhealthy runner to the stale queue.
- Worker reports that the assigned program could not complete after retries.

There is currently no explicit log line saying that a reset signal was
received. Reset handling is observable only through the `False` result, task
state, and runner-return logs.

## Behaviors To Address Or Observe Next

1. **Reset completion and availability**: reset handling clears the assignment
   but does not set the worker back to available. A defined terminal reset state
   is needed before testing worker reuse.
2. **Reset latency**: the reset branch waits for the running subprocess to
   finish instead of interrupting it. Tests should eventually require bounded
   cancellation latency so a reset is not delayed by a long-running task.
3. **Child failure propagation**: when a child resets or fails, the root waits
   forever in `update_intermediate_stream()`. The parent needs a failed/cancelled
   state or deadline so one child cannot deadlock the program.
4. **Health monitoring between phases**: runner health is not checked while the
   root waits for children. The child-wait case leaves that unhealthy runner
   neither detected nor placed in the stale queue.
5. **Runner ownership during changes**: `change_runner()` clears
   `current_runner` before deciding whether to return the previous runner. This
   can lose a runner when moving from main execution to orchestration and during
   repeated procedure failures.
6. **Failure diagnostics**: subprocess tracebacks remain in
   `Runner.error_stream`, while the final worker error log omits the return code,
   retry number, and stderr summary. Those fields would make concurrent failures
   attributable without inspecting objects after the run.
7. **Simultaneous-event precedence**: reset and unhealthy events in the same
   scheduler tick can take different valid branches. The system should define
   whether reset or health failure owns cancellation and runner disposition.
8. **Interrupt log formatting**: `Runner.interrupt()` prints the literal text
   `{self.id}` instead of the runner ID. A future regression test should require
   the real ID after production logging is corrected.
9. **Post-completion signals**: setting `reset_signal` on an idle completed
   worker has no immediate effect or log. Its lifecycle should define whether
   late signals are rejected, cleared, or recorded.
10. **Runner-pool-specific faults**: empty-queue timeout behavior, duplicate
    handoff, foreign-runner return, stale recovery, and stale-poller log volume
    remain deliberately untested here. They belong in a separate pool-focused
    suite if that scope is restored.
11. **Background exception visibility**: `Manager.run_program()` discards
    completed background tasks from its set without reading their exception.
    Future coverage should verify that unexpected worker exceptions are surfaced
    and attributed rather than silently disappearing.
12. **Test runtime and clock control**: this suite intentionally uses the real
    task sleeps and therefore takes about 72 seconds. Once task timing is
    injectable, a second deterministic clock-controlled layer could cover a
    larger timing matrix quickly while retaining a smaller real-subprocess smoke
    matrix.
