"""Integration tests for the manager-free multi-process demo."""

from io import StringIO

from src.app import DemoSystem, execute_command, run
from src.engine import CommandParse


def test_demo_system_evaluates_explicit_partitions_in_separate_processes() -> None:
    system = DemoSystem()
    try:
        first_process, second_process = system.create_processes(2)
        total_id = system.create_function("Sum", [1, 2, 3, 4])
        left_id, right_id = system.partition(total_id)

        system.transfer(left_id, first_process)
        system.transfer(right_id, second_process)

        assert system.evaluate(left_id) == 3
        assert system.evaluate(right_id) == 7
        assert system.orchestrate(total_id) == 10

        output = StringIO()
        assert execute_command(system, "list processes", output) is True
        listing = output.getvalue()
        assert f"{first_process} running\n  object {left_id}: idle" in listing
        assert f"{second_process} running\n  object {right_id}: idle" in listing
    finally:
        system.close()


def test_invalid_command_warns_and_the_next_command_runs() -> None:
    commands = StringIO("not-a-command\nhelp\nexit\n")
    output = StringIO()
    warnings = StringIO()

    assert run(commands, output, warnings) == 0
    assert "warning: unknown command: not-a-command" in warnings.getvalue()
    assert "Commands:" in output.getvalue()


def test_command_parser_accepts_a_list_literal() -> None:
    payload = CommandParse.parse("create -fn Sum -params [1,2,3,4,5,6]")

    assert payload == {
        "ok": True,
        "directive": "create_function",
        "arguments": {
            "class_name": "Sum",
            "params": [1, 2, 3, 4, 5, 6],
        },
    }
