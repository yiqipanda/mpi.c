import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import isfinite
from typing import Any

from functions import Function


@dataclass
class MatrixMultiplication(Function):
    """Multiply two matrices, partitioning the second matrix by columns."""

    @staticmethod
    def _matrix_shape(matrix: Any) -> tuple[int, int]:
        """Validate a numeric rectangular matrix without changing it."""
        if not isinstance(matrix, list) or not matrix:
            raise TypeError("A matrix must be a non-empty list of rows")
        if any(not isinstance(row, list) for row in matrix):
            raise TypeError("Matrix rows must be lists")
        columns = len(matrix[0])
        if columns == 0 or any(len(row) != columns for row in matrix):
            raise ValueError("Matrix rows must have the same non-zero length")
        if any(type(value) not in {int, float} for row in matrix for value in row):
            raise TypeError("Matrix entries must be integers or floats")
        if any(not isfinite(value) for row in matrix for value in row):
            raise ValueError("Matrix entries must be finite")
        return len(matrix), columns

    @staticmethod
    def _matrices(
        params: Any,
    ) -> tuple[list[list[int | float]], list[list[int | float]]]:
        """Validate and return the two operands without changing them."""
        if not isinstance(params, list) or len(params) != 2:
            raise ValueError("MatrixMultiplication requires params=[A, B]")
        left, right = params
        _, left_columns = MatrixMultiplication._matrix_shape(left)
        right_rows, _ = MatrixMultiplication._matrix_shape(right)
        if left_columns != right_rows:
            raise ValueError("A's column count must equal B's row count")
        return left, right

    @staticmethod
    def _dot_product(left: Sequence[int | float], right: Sequence[int | float]) -> int | float:
        """Compute one output entry from a row and a column."""
        return sum(a * b for a, b in zip(left, right))

    def eval(self) -> bool:
        if self.params is None:
            return False
        left, right = self._matrices(self.params)
        columns = list(zip(*right))
        self.result = [
            [self._dot_product(row, column) for column in columns]
            for row in left
        ]
        return True

    def partition(self) -> tuple["MatrixMultiplication", "MatrixMultiplication"]:
        left, right = self._matrices(self.params)
        columns = len(right[0])
        if columns < 2:
            raise ValueError("B must have at least two columns to partition")
        midpoint = columns // 2
        return (
            MatrixMultiplication(params=[
                [row.copy() for row in left],
                [row[:midpoint] for row in right],
            ]),
            MatrixMultiplication(params=[
                [row.copy() for row in left],
                [row[midpoint:] for row in right],
            ]),
        )

    def orchestrate(self, partitions: Sequence[Function]) -> bool:
        """Concatenate evaluated left and right partitions in that order."""
        if self.result is not None:
            return False
        if len(partitions) != 2:
            raise ValueError("MatrixMultiplication.orchestrate() requires two partitions")
        if any(not isinstance(part, MatrixMultiplication) for part in partitions):
            raise TypeError("Partitions must be MatrixMultiplication objects")
        if any(part.result is None for part in partitions):
            return False
        left, right = self._matrices(self.params)
        rows, columns = len(left), len(right[0])
        midpoint = columns // 2
        expected_shapes = ((rows, midpoint), (rows, columns - midpoint))
        shapes = tuple(self._matrix_shape(part.result) for part in partitions)
        if shapes != expected_shapes:
            raise ValueError("Partition result shapes do not match the column split")
        first, second = partitions
        self.result = [a + b for a, b in zip(first.result, second.result)]
        return True

    def serialize(self) -> dict[str, Any]:
        payload = {
            "class": type(self).__name__,
            "params": self.params,
            "result": self.result,
        }
        try:
            json.dumps(payload, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise TypeError("MatrixMultiplication state must be JSON-serializable") from exc
        return payload

    @classmethod
    def deserialize(cls, payload: Mapping[str, Any]) -> "MatrixMultiplication":
        if not isinstance(payload, Mapping):
            raise TypeError("Serialized MatrixMultiplication must be a mapping")
        if payload.get("class") != cls.__name__:
            raise ValueError("Serialized payload does not describe a MatrixMultiplication")
        if "params" not in payload:
            raise ValueError("Serialized MatrixMultiplication is missing params")
        return cls(params=payload["params"], result=payload.get("result"))
