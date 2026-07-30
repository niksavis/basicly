"""Requirements the replacement tracker inherits from br's defects (basicly-vkh0.6).

Plan §4 Phase 6: *carry Phase 0's defects forward as requirements*. Six defects in
`br` have already been paid for in sessions spent diagnosing them, and the repo
rule is that a dependency's defect is **requirements input for our own
replacement** and the proof must become a committed gate — never a fix applied
outside this repo.

This module is that gate. One test per requirement, each exercising the harness's
*own* defence against the **defective input**, so it fails if the defence is
removed. The register in prose, with what each defect cost, is
`docs/design/work-tracker.md` §2.1 (R1-R6); the ids here match it.

Two things this module deliberately is not:

- **Not a test of br.** Asserting br still misbehaves would pin us to a bug and
  break on the version that fixes it. Every assertion here is against our code.
- **Not a place for `pytest.skip`.** A requirement that silently skips is a
  requirement nobody is holding, which is the failure mode the register exists to
  prevent.

When the replacement lands, this module runs against it unchanged. That is the
point: it is the executable half of the scope contract.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import br, merge, policy, verify

REPO_ROOT = Path(__file__).parent.parent
COMMIT_MSG_HOOK = REPO_ROOT / ".basicly" / "core" / "hooks" / "beads-commit-msg.py"


def _load_hook(path: Path, name: str):
    """Import a hook script by path: it is not part of the installable package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- R1: a timestamp is evidence, never a constraint --------------------------


def test_r1_a_backwards_clock_write_rejection_is_not_charged_as_our_failure() -> None:
    """R1: the replacement must never reject a write on a clock comparison.

    br validates ``updated_at >= created_at`` and refuses its own write when the
    host clock steps backwards between two writes. Nothing in this repo sets either
    timestamp, yet it failed landings and spent a rework attempt on
    basicly-m4zv.9 — the re-run test cannot see it, because a clock step persists
    for a window and so reproduces.

    Pinned here in both of br's message shapes, because the first version of the
    register held only the singular one and a landing reproduced the plural.
    """
    singular = (
        "RuntimeError: br update basicly-x failed: Validation failed: "
        "updated_at: updated_at cannot be before created_at"
    )
    plural = (
        "RuntimeError: br update basicly-x failed: Validation errors: "
        "[ValidationError { field: updated_at, message: updated_at cannot be "
        "before created_at }]"
    )
    for output in (singular, plural):
        assert verify._defect_reason(output) is not None, output


def test_r1_the_signature_does_not_forgive_a_fixture_quoting_the_phrase() -> None:
    """The register must not become a way to launder a real failure.

    Matching is conjunctive per line: the defect phrase *plus* our own br wrapper
    text, which proves the failure came out of a br subprocess rather than out of a
    test's own fixture. A bare phrase must not match.
    """
    assert verify._defect_reason("assert 'updated_at cannot be before created_at' in out") is None


# --- R2: one spelling per field ----------------------------------------------


@pytest.mark.parametrize(
    "dep",
    [
        # br show --json
        {"id": "basicly-a", "dependency_type": "blocks"},
        # the create / dep add echo, for the same edge
        {"depends_on_id": "basicly-a", "type": "blocks"},
    ],
)
def test_r2_a_dependency_edge_reads_in_either_of_brs_two_spellings(dep: dict) -> None:
    """R2: the replacement must emit exactly one spelling per field.

    br renders one edge two ways. Reading only one spelling yields *no
    dependencies at all* rather than an error, so it fails silently — which is how
    it degraded every landing order to the caller's (basicly-kjc5.10).
    """
    assert br.dependency_edge(dep) == ("basicly-a", "blocks")


def test_r2_a_row_that_is_not_an_edge_is_rejected_rather_than_guessed() -> None:
    """A missing id must not become an empty-string dependency on some node."""
    assert br.dependency_edge({"dependency_type": "blocks"}) is None
    assert br.dependency_edge("basicly-a") is None
    assert br.dependency_edge({"id": "", "type": "blocks"}) is None


def test_r2_blocking_dependencies_reads_the_echo_spelling(monkeypatch: pytest.MonkeyPatch) -> None:
    """The landing order is the consumer that silently broke, so pin it end to end."""
    payload = json.dumps([
        {"id": "basicly-x", "dependencies": [{"depends_on_id": "basicly-a", "type": "blocks"}]}
    ])
    monkeypatch.setattr(
        merge.br, "try_run_br", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=payload)
    )
    assert merge.blocking_dependencies(Path(), "basicly-x") == frozenset({"basicly-a"})


# --- R3: validation rules are configurable, not compiled in -------------------


def test_r3_acceptance_criteria_are_required_for_a_work_type_lint_never_asks_about(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R3: the replacement's validation templates must be configurable per type.

    br's are built into the binary, and a ``chore`` is never asked for acceptance
    criteria — so lint staying silent does not mean they exist, it can mean the
    template never asked. The harness had to move the rule into its own gate
    (basicly-kjc5.36). This asserts the gate still catches the case br is quiet
    about.
    """

    def fake_br(_repo, args, **_kw):
        if args[:1] == ["lint"]:
            # A chore: br reports nothing missing, because its template asks for nothing.
            return SimpleNamespace(returncode=0, stdout=json.dumps({"results": [{"missing": []}]}))
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([
                {"id": "basicly-x", "acceptance_criteria": None, "description": ""}
            ]),
        )

    monkeypatch.setattr(policy, "_run_br", fake_br)
    result = policy.definition_of_ready(Path(), "basicly-x")

    assert result.ready is False
    assert policy._ACCEPTANCE_CRITERIA_SECTION in result.missing


# --- R4: a multi-line field stays multi-line ----------------------------------


def test_r4_multi_line_acceptance_criteria_satisfy_the_gate_from_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R4: the replacement's acceptance-criteria field must accept multiple lines.

    br's ``--acceptance-criteria`` takes a single line only, and exists only on
    ``update`` — so filing a bead is always two calls, and any structured criterion
    has to be flattened. The harness's workaround is to carry the criteria as a
    ``## Acceptance Criteria`` section in the description body, where newlines
    survive, and to accept *either* carrier. This pins the body carrier: without
    it, multi-line criteria have nowhere to live.
    """
    body = "## Acceptance Criteria\n\n- given a thing\n- when it happens\n- then a result\n"

    def fake_br(_repo, args, **_kw):
        if args[:1] == ["lint"]:
            return SimpleNamespace(
                returncode=0,
                stdout=json.dumps({
                    "results": [{"missing": [policy._ACCEPTANCE_CRITERIA_SECTION]}]
                }),
            )
        # The structured field is empty precisely because it cannot hold this.
        return SimpleNamespace(
            returncode=0,
            stdout=json.dumps([
                {"id": "basicly-x", "acceptance_criteria": "", "description": body}
            ]),
        )

    monkeypatch.setattr(policy, "_run_br", fake_br)
    result = policy.definition_of_ready(Path(), "basicly-x")

    assert result.ready is True
    assert result.missing == ()


# --- R5: an id is opaque and never re-parsed ----------------------------------


def test_r5_a_slug_shaped_id_is_truncated_by_the_prefix_anchored_gate() -> None:
    """R5: the replacement must not mint an id whose text needs parsing.

    ``br create --slug`` produces ids like ``basicly-fix-the-thing``. The gate
    matches ids the way br's own commit scanner does — prefix-anchored — so such an
    id reads as ``basicly-fix`` and the rest is lost (basicly-jms0). The fix was
    deliberately *not* to teach the hook about slugs: a format whose own tool
    cannot round-trip it is the defect, and the standing rule became "never
    ``--slug``".

    Asserting the truncation, rather than pretending it is handled, is what records
    the constraint: an id must be opaque and never re-parsed, so the replacement
    may not put meaning in an id's separators.
    """
    hook = _load_hook(COMMIT_MSG_HOOK, "beads_commit_msg_hook")
    known = {"basicly-fix-the-thing", "basicly-m4zv.10"}

    assert hook._candidate_ids("fix(x): do it (basicly-fix-the-thing)", known) == {"basicly-fix"}
    # The shapes we do mint round-trip exactly.
    assert hook._candidate_ids("fix(x): do it (basicly-m4zv.10)", known) == {"basicly-m4zv.10"}
    # An ordinary hyphenated word is not an id, or every commit subject would be one.
    assert hook._candidate_ids("fix(x): a well-known problem", known) == set()


def test_r5_the_ids_this_repo_mints_carry_no_internal_hyphen() -> None:
    """The register's other half: never create the shape in the first place.

    Read from the live tracker rather than asserted about a generator, because the
    ids that matter are the ones already in the ledger.
    """
    export = REPO_ROOT / ".beads" / "issues.jsonl"
    offenders = []
    for line in export.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        ident = record.get("id") if isinstance(record, dict) else None
        if isinstance(ident, str) and ident.count("-") > 1:
            offenders.append(ident)
    assert offenders == [], f"slug-shaped ids break the commit gate: {offenders}"


# --- R6: a committed artifact carries no machine-specific path ----------------


def test_r6_the_export_publishes_no_machine_specific_path(tmp_path: Path) -> None:
    """R6: the replacement must never write a host path into a committed artifact.

    br's export carried ``source_repo_path`` on 328 of 332 records, publishing two
    users' home-directory layouts to every consumer clone (basicly-vkh0.5). Both
    shapes are covered: the named field is removed outright, and a path left in
    free text is redacted.
    """
    beads = tmp_path / ".beads"
    beads.mkdir()
    export = beads / "issues.jsonl"
    export.write_text(
        json.dumps({
            "id": "basicly-x",
            br.MACHINE_PATH_FIELD: "/home/someone/development/basicly",
            "description": "see /home/someone/development/basicly/docs for context",
        })
        + "\n",
        encoding="utf-8",
    )

    changed = br.scrub_export(tmp_path)

    assert changed == 1
    scrubbed = json.loads(export.read_text(encoding="utf-8").strip())
    assert br.MACHINE_PATH_FIELD not in scrubbed
    assert "/home/someone" not in json.dumps(scrubbed)


def test_r6_scrubbing_an_already_clean_export_changes_nothing(tmp_path: Path) -> None:
    """It runs on the commit path, so a clean export must not churn the file."""
    beads = tmp_path / ".beads"
    beads.mkdir()
    export = beads / "issues.jsonl"
    # br's own compact rendering: a fixture with default separators would be
    # rewritten by the re-dump and report a change that is only whitespace.
    original = (
        json.dumps({"id": "basicly-x", "description": "no paths here"}, separators=(",", ":"))
        + "\n"
    )
    export.write_text(original, encoding="utf-8")

    assert br.scrub_export(tmp_path) == 0
    assert export.read_text(encoding="utf-8") == original


# --- The register must stay complete -----------------------------------------


def test_every_requirement_in_the_design_register_has_a_test_here() -> None:
    """A prose register nobody tests is a wish list.

    The design doc numbers the requirements R1-R6; this asserts each id appears in
    a test name in this module, so adding a seventh defect to the register without
    a gate fails here rather than being noticed years later.
    """
    design = (REPO_ROOT / "docs" / "design" / "work-tracker.md").read_text(encoding="utf-8")
    declared = {f"R{n}" for n in range(1, 10) if f"**{f'R{n}'}." in design}
    assert declared, "no R<n> requirements found in the design register"

    source = Path(__file__).read_text(encoding="utf-8")
    covered = {rid for rid in declared if f"def test_{rid.lower()}_" in source}
    assert covered == declared, f"requirements with no test: {sorted(declared - covered)}"
