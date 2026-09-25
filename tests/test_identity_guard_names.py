from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

HOOK = Path(__file__).parent.parent / ".basicly" / "core" / "hooks" / "identity-guard.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("identity_guard_names", HOOK)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["identity_guard_names"] = module
    spec.loader.exec_module(module)
    return module


guard = _load()
LEDGER = ".basicly/ledger/pending-main.jsonl"


def _field(name: str, value: str) -> str:
    return json.dumps({"kind": "field", "payload": {"name": name, "value": value}})


def test_the_holder_field_may_carry_the_holder_name() -> None:
    lines = [(LEDGER, _field("assignee", "Dana Doe"))]

    assert guard.name_findings(lines, "Dana Doe", "Dana Doe") == []


def test_the_name_anywhere_else_is_found() -> None:
    comment = json.dumps({"kind": "comment", "payload": {"text": "asked Dana Doe"}})
    lines = [(LEDGER, comment), ("docs/owners.md", "Owner: Dana Doe")]

    assert guard.name_findings(lines, "Dana Doe", "Dana Doe") == [LEDGER, "docs/owners.md"]


def test_a_chosen_pseudonym_keeps_the_real_name_out_of_the_holder_field() -> None:
    lines = [(LEDGER, _field("assignee", "Dana Doe"))]

    assert guard.name_findings(lines, "Dana Doe", "dd") == [LEDGER]
    assert guard.name_findings([(LEDGER, _field("assignee", "dd"))], "Dana Doe", "dd") == []


def test_an_escaped_name_is_found_and_a_short_name_is_ignored() -> None:
    lines = [(LEDGER, json.dumps({"kind": "comment", "payload": {"text": "Zoë Ångström"}}))]

    assert guard.name_findings(lines, "Zoë Ångström", "x") == [LEDGER]
    assert guard.name_findings([("a.md", "al is here")], "al", "al") == []


def _diff(removed: list[tuple[str, str]], added: list[tuple[str, str]]) -> str:
    out = []
    for path, text in removed:
        out += [f"--- a/{path}", f"+++ b/{path}", "@@ -1 +0,0 @@", f"-{text}"]
    for path, text in added:
        out += ["--- /dev/null", f"+++ b/{path}", "@@ -0,0 +1 @@", f"+{text}"]
    return "\n".join(out) + "\n"


def test_a_fold_that_moves_a_line_naming_the_user_adds_nothing() -> None:
    comment = json.dumps({"kind": "comment", "payload": {"text": "asked Dana Doe"}})
    diff = _diff([(LEDGER, comment)], [(".basicly/ledger/events-0001.jsonl", comment)])

    assert guard.name_findings(guard.net_added(diff), "Dana Doe", "Dana Doe") == []


def test_a_new_line_naming_the_user_beside_a_move_is_still_found() -> None:
    moved = json.dumps({"kind": "comment", "payload": {"text": "old"}})
    fresh = json.dumps({"kind": "comment", "payload": {"text": "asked Dana Doe"}})
    trunk = ".basicly/ledger/events-0001.jsonl"
    diff = _diff([(LEDGER, moved)], [(trunk, moved), (trunk, fresh)])

    assert guard.name_findings(guard.net_added(diff), "Dana Doe", "Dana Doe") == [trunk]


def test_a_snapshot_state_holding_only_the_holder_passes() -> None:
    snapshot = ".basicly/ledger/snapshot.jsonl"
    state = json.dumps({"record": "acme-1", "fields": {"assignee": "Dana Doe", "title": "t"}})
    leaked = json.dumps({
        "record": "acme-2",
        "fields": {"assignee": "Dana Doe", "title": "Dana Doe"},
    })

    assert guard.name_findings([(snapshot, state)], "Dana Doe", "Dana Doe") == []
    assert guard.name_findings([(snapshot, leaked)], "Dana Doe", "Dana Doe") == [snapshot]
