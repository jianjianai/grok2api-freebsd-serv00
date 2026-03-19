"""
Small orjson compatibility shim.

The project primarily relies on `dumps`, `loads`, and a few option flags.
This fallback keeps the same return types that the code expects.
"""

from __future__ import annotations

import json
from typing import Any

JSONDecodeError = json.JSONDecodeError

OPT_INDENT_2 = 1 << 0
OPT_SORT_KEYS = 1 << 1


def dumps(obj: Any, option: int = 0) -> bytes:
    kwargs = {
        "ensure_ascii": False,
        "separators": (",", ":"),
    }
    if option & OPT_SORT_KEYS:
        kwargs["sort_keys"] = True
    if option & OPT_INDENT_2:
        kwargs["indent"] = 2
        kwargs["separators"] = (",", ": ")
    return json.dumps(obj, **kwargs).encode("utf-8")


def loads(data: Any) -> Any:
    if isinstance(data, bytes):
        data = data.decode("utf-8")
    return json.loads(data)

