"""Opaque keyset cursors: base64url of the sort-key values joined by '|'."""

import base64
import binascii
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException


def _invalid() -> HTTPException:
    return HTTPException(422, {"error": "INVALID_CURSOR"})


def encode_cursor(*parts: Any) -> str:
    raw = "|".join(
        p.isoformat() if isinstance(p, (date, datetime)) else str(p) for p in parts
    )
    return base64.urlsafe_b64encode(raw.encode()).decode()


def decode_cursor(cursor: str, *parsers: Callable[[str], Any]) -> tuple:
    """Decode with one parser per part (e.g. int, datetime.fromisoformat); 422 on any error."""
    try:
        parts = base64.urlsafe_b64decode(cursor.encode()).decode().split("|")
        if len(parts) != len(parsers):
            raise ValueError("wrong arity")
        return tuple(parse(part) for parse, part in zip(parsers, parts, strict=True))
    except (binascii.Error, ValueError, UnicodeError, TypeError):
        raise _invalid() from None


def non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise ValueError("negative")
    return value
