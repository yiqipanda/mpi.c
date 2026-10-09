"""Function and serializable classes available to message decoding."""

from functions import Function, Serializable, Sum
from log import Log
from log_buffer import LogBuffer
from matrix import MatrixMultiplication


FUNCTION_CLASSES: dict[str, type[Function]] = {
    Sum.__name__: Sum,
    MatrixMultiplication.__name__: MatrixMultiplication,
}

SERIALIZABLE_CLASSES: dict[str, type[Serializable]] = dict(FUNCTION_CLASSES)
SERIALIZABLE_CLASSES.update({"Log": Log, "LogBuffer": LogBuffer})
