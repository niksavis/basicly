from __future__ import annotations

import json
from pathlib import Path

from basicly import needs_input


def _write(cwd: Path, payload: str) -> Path:
    path = cwd / needs_input.SENTINEL_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path


def test_take_returns_none_when_no_sentinel(tmp_path: Path) -> None:
    assert needs_input.take(tmp_path) is None


def test_take_reads_and_consumes_a_valid_sentinel(tmp_path: Path) -> None:
    path = _write(tmp_path, json.dumps({"fact": "prod db dialect", "detail": "no vendor marker"}))
    result = needs_input.take(tmp_path)
    assert result == needs_input.NeedsInput("prod db dialect", "no vendor marker")
    assert not path.exists()
    assert needs_input.take(tmp_path) is None


def test_take_strips_whitespace_and_defaults_detail(tmp_path: Path) -> None:
    _write(tmp_path, json.dumps({"fact": "  which port?  "}))
    assert needs_input.take(tmp_path) == needs_input.NeedsInput("which port?", "")


def test_take_consumes_but_ignores_malformed_json(tmp_path: Path) -> None:
    path = _write(tmp_path, "not json at all {")
    assert needs_input.take(tmp_path) is None
    assert not path.exists()


def test_take_ignores_empty_or_missing_fact(tmp_path: Path) -> None:
    _write(tmp_path, json.dumps({"fact": "   ", "detail": "blank"}))
    assert needs_input.take(tmp_path) is None
    _write(tmp_path, json.dumps({"detail": "no fact key"}))
    assert needs_input.take(tmp_path) is None


def test_take_ignores_non_dict_and_non_string_fact(tmp_path: Path) -> None:
    _write(tmp_path, json.dumps(["fact", "detail"]))
    assert needs_input.take(tmp_path) is None
    _write(tmp_path, json.dumps({"fact": 42}))
    assert needs_input.take(tmp_path) is None


def test_take_tolerates_non_string_detail(tmp_path: Path) -> None:
    _write(tmp_path, json.dumps({"fact": "x", "detail": {"nested": True}}))
    assert needs_input.take(tmp_path) == needs_input.NeedsInput("x", "")
