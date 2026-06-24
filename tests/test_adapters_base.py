from __future__ import annotations

import pytest

from yanshi.adapters.base import compact_json, parse_json_object
from yanshi.errors import AdapterError


def test_parse_json_object_rejects_invalid_json() -> None:
    with pytest.raises(AdapterError):
        parse_json_object("{")


def test_parse_json_object_rejects_non_object() -> None:
    with pytest.raises(AdapterError):
        parse_json_object("[]")


def test_compact_json_is_deterministic() -> None:
    assert compact_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'
