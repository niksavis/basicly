from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from tests.test_kit_vault_canonical import PRIVATE, RECORD, _redact, _reviewed, canonical, events
from tests.test_kit_vault_migrate import _plan, vault_migrate
from tests.test_kit_vault_share import _machine, bare

__all__ = ["bare"]
template_kit = vault_migrate.vault_template


def _custom_source(folder: Path, heading: str) -> dict:
    _reviewed(folder)
    raw = {"prefix": "demo", "mode": "extend", "sections": [heading]}
    (folder / "template.json").write_text(json.dumps(raw))
    template = vault_migrate.templates.load(folder)
    field = canonical.process.shaping.field_of(heading)
    events.append(
        folder,
        [events.Draft(RECORD, "field", {"name": field, "value": "Use the standard library."})],
        actor=PRIVATE,
        clock=lambda: 4000.0,
    )
    found = events.read_events(folder)[0]
    checks = [
        {
            "criterion": "When a record is read, show shall return its title.",
            "command": ["python3", "check_title.py"],
            "expected": "The title appears.",
        }
    ]
    draft = canonical.process.review_draft(
        found, RECORD, {"checks": checks}, PRIVATE, template=template
    )
    events.append(folder, [draft], actor=PRIVATE, clock=lambda: 5000.0)
    found = events.read_events(folder)[0]
    completed = [
        {
            "criterion": checks[0]["criterion"],
            "command": checks[0]["command"],
            "result": "The title appeared.",
            "exit_code": 0,
        }
    ]
    draft = canonical.process.confirmation_draft(
        found, RECORD, {"checks": completed}, PRIVATE, template=template
    )
    events.append(folder, [draft], actor=PRIVATE, clock=lambda: 6000.0)
    return raw


@pytest.mark.parametrize("heading", ["## Constraints", f"## {PRIVATE} constraints"])
def test_closed_custom_template_evidence_survives_import_and_independent_export(
    tmp_path: Path,
    bare: Path,
    heading: str,
) -> None:
    source = tmp_path / "source"
    raw = _custom_source(source, heading)
    plan = _plan(tmp_path, bare, redactor=_redact)
    vault_migrate.ledger_to_vault(source, plan)
    clone, _ = _machine(tmp_path, bare, "independent-reader")
    view = vault_migrate.vault_view.present(
        clone, vault_migrate.vault_view.Want("demo"), clock=time.time
    )
    expected = template_kit.prepare(raw, _redact)
    assert vault_migrate.templates.read(view) == expected
    found = events.read_events(view)[0]
    state = events.fold(found).records[RECORD]
    assert state.status == "closed"
    field = canonical.process.shaping.field_of(_redact(heading))
    assert state.fields[field] == "Use the standard library."
    report = canonical.process.confirmation_report(
        found, RECORD, template=vault_migrate.templates.load(view)
    )
    assert report["review_current"] and report["confirmation_current"]
    assert not report["owed"]
    target = tmp_path / "independent-export"
    vault_migrate.from_vault(clone, "demo", target, clock=time.time)
    exported = events.read_events(target)[0]
    assert vault_migrate.templates.read(target) == expected
    report = canonical.process.confirmation_report(
        exported, RECORD, template=vault_migrate.templates.load(target)
    )
    assert report["review_current"] and report["confirmation_current"]
    assert canonical.fsck.check(target).exit_code == 0


@pytest.mark.parametrize("mutation", ["missing", "altered", "invalid"])
def test_required_template_metadata_fails_closed_when_missing_or_altered(mutation: str) -> None:
    raw = {"prefix": "demo", "mode": "extend", "sections": ["## Constraints"]}
    line = {
        "record": RECORD,
        template_kit.FIELD: raw,
        template_kit.IDENTITY: template_kit.identity(raw),
    }
    assert template_kit.carried([line], "demo") == raw
    if mutation == "missing":
        del line[template_kit.FIELD]
    elif mutation == "altered":
        line[template_kit.FIELD] = {**raw, "sections": []}
    else:
        invalid = {**raw, "sections": "not a list"}
        line[template_kit.FIELD] = invalid
        line[template_kit.IDENTITY] = template_kit.identity(invalid)
    with pytest.raises(template_kit.VaultTemplateError):
        template_kit.carried([line], "demo")


def test_template_name_redaction_refuses_to_overwrite_an_existing_field(tmp_path: Path) -> None:
    source = tmp_path / "source"
    raw = _custom_source(source, f"## {PRIVATE} constraints")
    events.append(
        source,
        [
            events.Draft(
                RECORD, "field", {"name": "contributor_constraints", "value": "Different content."}
            )
        ],
        actor=PRIVATE,
        clock=lambda: 7000.0,
    )
    with pytest.raises(vault_migrate.canonical.CanonicalError, match="overwrites a field"):
        vault_migrate.prepare(events.read_events(source)[0], raw, _redact, redact_holders=True)
