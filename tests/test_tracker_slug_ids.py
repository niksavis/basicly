from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

if TYPE_CHECKING:
    from pathlib import Path

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "tracker_slug_ids_cli")
claims = cli.commands.claims
hook = _load(REPO_ROOT / ".basicly/core/hooks/tracker-commit-msg.py", "tracker_slug_ids_hook")
SLUG = "dev-memory-audit-multi-target-2zx"
WHEN = "2026-10-01T00:00:00Z"


def _beads_line(record: str) -> str:
    line = {
        "id": record,
        "title": "probe",
        "status": "open",
        "priority": 2,
        "issue_type": "task",
        "created_at": WHEN,
        "updated_at": WHEN,
    }
    return json.dumps(line) + "\n"


def test_import_keeps_a_slug_id(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    export = tmp_path / "issues.jsonl"
    export.write_text(_beads_line(SLUG), encoding="utf-8")
    assert cli.main(["import", str(tmp_path / "ledger"), str(export)]) == cli.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["rejected"] == []
    assert cli.main(["show", str(tmp_path / "ledger"), SLUG]) == cli.EXIT_OK
    assert json.loads(capsys.readouterr().out)["record"] == SLUG


@pytest.mark.parametrize("surface", ["hook", "commit-check"])
def test_a_message_resolves_a_slug_id(surface: str) -> None:
    message = f"fix(tracker): keep the slug id ({SLUG})"
    if surface == "hook":
        assert hook.validate(message, {SLUG, "dev-abc"}) == (True, "")
    else:
        assert claims.named_ids(message, {SLUG: object()}) == [SLUG]


@pytest.mark.parametrize("surface", ["hook", "commit-check"])
def test_a_hyphenated_word_after_an_id_still_resolves(surface: str) -> None:
    message = "fix(tracker): see dev-abc-related notes"
    if surface == "hook":
        assert hook.validate(message, {"dev-abc"}) == (True, "")
    else:
        assert claims.named_ids(message, {"dev-abc": object()}) == ["dev-abc"]
