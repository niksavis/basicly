from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / ".basicly" / "core" / "kit" / "orch"))

doctor = importlib.import_module("orch_lessons.doctor")


def test_doctor_rule_parity_fails_on_an_owner_rule_with_no_kit_entry(tmp_path: Path) -> None:
    ruleset = tmp_path / "ruleset.json"
    owner = [
        {"date": "2026-01-02", "phrase": "keep the gate on"},
        {"date": None, "phrase": "plain"},
    ]
    ruleset.write_text(json.dumps({"rules": [{"id": "a", "owner": owner}]}), encoding="utf-8")
    doc = tmp_path / "rules.md"
    doc.write_text(
        '- Owner rule, 2026-01-02: "keep the gate on".\n- Owner rule. A plain rule.\n'
        'Only the person changes an "Owner rule".\n',
        encoding="utf-8",
    )
    environ = {"ORCH_RULES_DOC": str(doc), "ORCH_RULESET": str(ruleset)}
    assert doctor.rule_parity_lines(environ) == (
        ["rule parity: 2 of 2 owner rules have a kit entry"],
        False,
    )
    doc.write_text(
        doc.read_text(encoding="utf-8") + "- Owner rule, 2026-01-03: new.\n", encoding="utf-8"
    )
    lines, failed = doctor.rule_parity_lines(environ)
    assert failed
    assert lines[0] == f"{doc}:4: owner rule (2026-01-03) has no entry in {ruleset}"
    assert doctor.rule_parity_lines({})[1] is False


@pytest.mark.parametrize(
    "line",
    [
        "Working rule, 2026-11-19 (the machine has 32 cores): a landing verify runs on cores 0-15.",
        "A rule change reaches a session when it rereads (lesson 2026-11-19 18:18, dev-j6oqo).",
    ],
)
def test_doctor_rule_parity_also_covers_working_rules_and_lessons(
    tmp_path: Path, line: str
) -> None:
    ruleset = tmp_path / "ruleset.json"
    ruleset.write_text(json.dumps({"rules": [{"id": "a", "owner": []}]}), encoding="utf-8")
    doc = tmp_path / "rules.md"
    doc.write_text(line + "\n", encoding="utf-8")
    environ = {"ORCH_RULES_DOC": str(doc), "ORCH_RULESET": str(ruleset)}

    lines, failed = doctor.rule_parity_lines(environ)

    assert failed
    assert lines[0] == f"{doc}:1: owner rule (2026-11-19) has no entry in {ruleset}"
