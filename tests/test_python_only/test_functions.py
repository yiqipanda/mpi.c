"""Smoke tests for the function implementations."""

from src.functions import Sum, deserialize_function


def test_sum_partition_evaluate_and_orchestrate() -> None:
    total = Sum(params=[1, 2, 3, 4])

    left, right = total.partition()

    assert left.params == [1, 2]
    assert right.params == [3, 4]
    assert left.eval() is True
    assert right.eval() is True

    assert total.orchestrate([left, right]) is True

    assert total.result == 10


def test_sum_accepts_empty_partitions() -> None:
    total = Sum(params=[])
    left, right = total.partition()

    assert left.eval() is True
    assert right.eval() is True
    assert total.orchestrate([left, right]) is True
    assert total.result == 0


def test_sum_serialization_round_trip() -> None:
    original = Sum(params=[1, 2, 3], result=6)

    restored = deserialize_function(original.serialize())

    assert isinstance(restored, Sum)
    assert restored.params == [1, 2, 3]
    assert restored.result == 6
