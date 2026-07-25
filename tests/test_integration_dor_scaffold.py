"""Definition-of-Ready body scaffolding against a real ``br`` tracker (basicly-kjc5.44).

The required-section set lives in :mod:`basicly.policy` as a stated map, because
``br``'s per-type templates are compiled into the binary and no read-only ``br``
command reports them. A stated map can drift from the tool it describes, and a
stub cannot disagree with ``br`` — so the map is pinned here, against whatever
``br`` is installed:

- :func:`test_scaffold_covers_every_section_br_lint_asks_for` fails if a ``br``
  release adds or renames a template section the scaffold would then omit. It is a
  superset check, so a section ``br`` *drops* stays in the scaffold silently —
  harmless (an extra heading blocks nothing) and not worth a stricter assertion
  that would also fail on the acceptance criteria the harness adds itself.
- :func:`test_a_filled_scaffold_passes_the_dor_gate` is the acceptance criterion
  end to end: create a bead from the scaffold, fill the sections, and the real
  gate reads READY with no further edits.

Only ``br`` is real here; there is no git history or loop to drive, so the
fixture is a bare ``br init`` workspace rather than
``tests/test_integration_loop.py``'s full consumer repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import br, policy
from basicly.config import WORK_TYPES

needs_br = pytest.mark.skipif(
    br.which() is None, reason="the beads tracker (br) is not installed on this machine"
)

pytestmark = needs_br


@pytest.fixture
def tracker(tmp_path: Path) -> Path:
    """A throwaway ``br`` workspace, so no probe bead touches the real tracker."""
    workspace = tmp_path / "tracker"
    workspace.mkdir()
    br.run_br(workspace, ["init", "--prefix", "probe"])
    return workspace


def _create(tracker: Path, work_type: str, body: str) -> str:
    out = br.run_br(
        tracker, ["create", f"probe {work_type}", "-t", work_type, "-d", body, "--json"]
    ).stdout
    return str(json.loads(out)["id"])


def _lint_missing(tracker: Path, issue_id: str) -> set[str]:
    """The sections ``br lint`` itself reports missing on *issue_id*."""
    results = json.loads(br.run_br(tracker, ["lint", issue_id, "--json"]).stdout).get("results", [])
    return set(results[0].get("missing", [])) if results else set()


def _fill(body: str) -> str:
    """Replace every scaffolded placeholder with content, as an agent would."""
    return "\n".join(
        "an answer the agent supplied" if "TODO" in line else line for line in body.splitlines()
    )


@pytest.mark.parametrize("work_type", WORK_TYPES)
def test_scaffold_covers_every_section_br_lint_asks_for(tracker: Path, work_type: str) -> None:
    """The stated map is a superset of the installed ``br``'s per-type template.

    Probed with an empty body so lint reports the type's full required set, then
    compared against what the scaffold would emit. A ``br`` upgrade that adds or
    renames a section fails here rather than at some future classify gate.
    """
    missing = _lint_missing(tracker, _create(tracker, work_type, "no sections here"))
    assert missing <= set(policy.required_sections(work_type))


@pytest.mark.parametrize("work_type", WORK_TYPES)
def test_a_filled_scaffold_passes_the_dor_gate(tracker: Path, work_type: str) -> None:
    """A bead created from the scaffold and filled in is READY with no further edits."""
    issue_id = _create(tracker, work_type, _fill(policy.compose_body(work_type)))
    assert policy.definition_of_ready(tracker, issue_id).ready is True


@pytest.mark.parametrize("work_type", WORK_TYPES)
def test_an_unfilled_scaffold_still_satisfies_the_gate_structurally(
    tracker: Path, work_type: str
) -> None:
    """Placeholders are for the reader, not the gate — the gate only sees headings.

    Pins why :data:`policy._SECTION_HINTS` exists: nothing downstream can tell an
    unfilled body from a filled one, so the placeholder has to say ``TODO`` out
    loud rather than leave a heading with nothing under it.
    """
    body = policy.compose_body(work_type)
    assert "TODO" in body
    assert policy.definition_of_ready(tracker, _create(tracker, work_type, body)).ready is True
