"""Tests for the corpus-drift gate (basicly-b9ef).

Driven against fixture exports written into `tmp_path`, never against this repo's own
tracker: a gate asserted on live bead text becomes a report on whatever the tracker holds
today, and any lane editing a bead turns the suite red. The one real-tree run asserts only
that the entry point runs and identifies itself, which is what a hardcoded ``REPO_ROOT``
can be held to.

The ratchet is pinned in both directions for the reason `module-size` states: a recorded
count that could rise is a licence, and one that could fall unbanked licenses regrowth
back to the higher number.

The wiring test is the one that matters most here — an instrument built and never
connected is this repo's named defect class, and `basicly.toml` is where this one becomes
a gate rather than a script.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

from basicly import corpus_drift

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_corpus_drift.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_corpus_drift")


def _export(repo_root: Path, records: list[dict]) -> None:
    """Write *records* as the committed tracker export the gate reads."""
    beads = repo_root / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    (beads / "issues.jsonl").write_text(
        "\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8"
    )


def _epic(description: str, status: str = "open") -> dict:
    return {"id": "epic", "status": status, "issue_type": "epic", "description": description}


def _child(child_id: str, status: str) -> dict:
    return {
        "id": child_id,
        "status": status,
        "dependencies": [{"depends_on_id": "epic", "type": "parent-child"}],
    }


STALE = "## Context\n\n- park re-admits the lane, a fail-open on a human control point\n"


def test_a_stale_bullet_is_reported_with_its_epic(tmp_path: Path) -> None:
    """What a human runs the check for: which bead, and which of its claims."""
    _export(tmp_path, [_epic(STALE), _child("epic.1", "closed")])
    found = gate.findings(tmp_path)
    assert len(found) == 1
    assert found[0].issue_id == "epic"
    assert found[0].bullet.startswith("park re-admits the lane")
    assert "epic: 1 problem bullet(s) name no child" in gate.report(found, gate.verdicts(found, {}))


def test_the_gate_exits_non_zero_only_while_a_bullet_is_unaccounted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exit code is the gate; the corrected form of the same bead clears it."""
    monkeypatch.setattr(gate, "REPO_ROOT", tmp_path)
    _export(tmp_path, [_epic(STALE), _child("epic.1", "closed")])
    assert gate.main(["--strict"]) == 1
    corrected = "## Context\n\n- CORRECTED 2026-08-08 (epic.1): the park claim was refuted\n"
    _export(tmp_path, [_epic(corrected), _child("epic.1", "closed")])
    assert gate.main(["--strict"]) == 0


def _finding(issue_id: str) -> corpus_drift.Finding:
    return corpus_drift.Finding(issue_id, "a claim nobody marked", ("epic.1",), ())


def test_a_bead_absent_from_the_baseline_may_not_carry_one_unaccounted_bullet() -> None:
    """The list is closed, which is what makes the recorded debt a debt and not a licence."""
    recorded = (_finding("epic"),) * 4
    verdict = gate.verdicts((*recorded, _finding("other")), {"epic": 4})
    assert [entry.issue_id for entry in verdict] == ["other"]
    assert "name no child" in verdict[0].detail


def test_a_recorded_bead_may_only_fall() -> None:
    """Growth fails, and so does an unbanked fall — the shape module-size already refuses."""
    grew = gate.verdicts((_finding("epic"), _finding("epic")), {"epic": 1})
    assert "up from the frozen 1" in grew[0].detail
    assert gate.verdicts((_finding("epic"),), {"epic": 1}) == []
    fell = gate.verdicts((), {"epic": 1})
    assert "down from the frozen 1" in fell[0].detail
    assert '"epic" = 0' in fell[0].remedy


def test_the_baseline_is_read_from_pyproject_and_refuses_a_missing_table(
    tmp_path: Path,
) -> None:
    """A gate that defaults to a permissive baseline passes everything, which is worse."""
    assert "basicly-u2hl" in gate.load_frozen(REPO_ROOT)
    (tmp_path / "pyproject.toml").write_text("[tool.other]\n", encoding="utf-8")
    with pytest.raises(gate.RatchetError, match="corpus_drift"):
        gate.load_frozen(tmp_path)


def test_a_closed_epic_and_an_unnamed_one_are_out_of_scope(tmp_path: Path) -> None:
    """A closed bead's statement is history, and a named id narrows to one bead."""
    _export(tmp_path, [_epic(STALE, status="closed"), _child("epic.1", "closed")])
    assert gate.findings(tmp_path) == ()
    _export(tmp_path, [_epic(STALE), _child("epic.1", "closed")])
    assert gate.findings(tmp_path, ("other",)) == ()
    assert len(gate.findings(tmp_path, ("epic",))) == 1


def test_the_gate_runs_over_this_repo() -> None:
    """The entry point works against the hardcoded repo root, whatever it finds there."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert result.returncode in (0, 1), result.stderr
    assert "[corpus-drift]" in result.stdout


def test_the_gate_is_wired_as_a_verify_check() -> None:
    """An instrument nothing runs is the defect class this repo keeps paying for."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = config["verify"]["checks"]
    wired = [check for check in checks if SCRIPT.name in " ".join(check["command"])]
    assert [check["name"] for check in wired] == ["corpus-drift"]
    assert wired[0]["modes"] == ["fast", "full"]
