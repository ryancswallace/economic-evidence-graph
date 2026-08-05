"""Strict JSON serialization helpers shared by persistence and exports."""

from __future__ import annotations

import hashlib
import json
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, TypeAlias, cast

JsonValue: TypeAlias = bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"] | None


def to_json_value(value: object) -> JsonValue:
    """Convert supported immutable domain values into strict JSON values.

    Args:
        value: Domain value composed of dataclasses, enums, dates, mappings, and scalars.

    Returns:
        A value accepted by the standard JSON encoder.

    Raises:
        TypeError: If the value is outside the strict serialization contract.
    """
    if value is None or isinstance(value, str | bool | int | float):
        return value
    if isinstance(value, Enum):
        return to_json_value(value.value)
    if isinstance(value, datetime):
        return value.isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, tuple | list):
        return [to_json_value(item) for item in value]
    if isinstance(value, dict | MappingProxyType):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        instance = cast(Any, value)
        return {item.name: to_json_value(getattr(instance, item.name)) for item in fields(instance)}
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def canonical_json_bytes(value: object) -> bytes:
    """Serialize a supported value into canonical UTF-8 JSON bytes."""
    return json.dumps(
        to_json_value(value),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def record_sha256(value: object) -> str:
    """Return the canonical SHA-256 digest for a supported value."""
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()
