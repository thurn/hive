"""Validate untrusted JSON into values with no dynamic typing inside Hive."""

from __future__ import annotations

import json
from collections.abc import Mapping

from hive.errors import ErrorCode, HiveError


def parse(text: str) -> object:
    try:
        value: object = json.loads(text)
    except (ValueError, RecursionError) as error:
        raise HiveError(ErrorCode.INVALID_INPUT, f"Invalid JSON: {error}") from error
    return value


def record(value: object, context: str = "record") -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise HiveError(ErrorCode.INVALID_RECORD, f"{context} must be an object")
    result: dict[str, object] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise HiveError(ErrorCode.INVALID_RECORD, f"{context} has a non-string key")
        result[key] = item
    return result


def sequence(value: object, context: str) -> list[object]:
    if not isinstance(value, list):
        raise HiveError(ErrorCode.INVALID_RECORD, f"{context} must be an array")
    return list(value)


def string(value: object, context: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise HiveError(ErrorCode.INVALID_RECORD, f"{context} must be a string")
    return value


def integer(value: object, context: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise HiveError(ErrorCode.INVALID_RECORD, f"{context} must be >= {minimum}")
    return value
