"""Requirements the replacement tracker inherits from br's defects (basicly-vkh0.6).

Plan §4 Phase 6: *carry Phase 0's defects forward as requirements*. Eight defects in
`br` have already been paid for in sessions spent diagnosing them, and the repo
rule is that a dependency's defect is **requirements input for our own
replacement** and the proof must become a committed gate — never a fix applied
outside this repo.

This module is that gate. One test per requirement, each exercising the harness's
*own* defence against the **defective input**, so it fails if the defence is
removed. The register this module is checked against is architecture §32.9 (R1-R9);
the ids here match its table. It was `work-tracker.md` §2.1 until 2026-08-18, and
moved because that document is scheduled for deletion (basicly-vkh0.42.8).

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
import re
import time
from pathlib import Path

import pytest

from basicly import merge, policy, tracker, tracker_paths, verify
from tests import flipped_tracker

REPO_ROOT = Path(__file__).parent.parent
COMMIT_MSG_HOOK = REPO_ROOT / ".basicly" / "core" / "hooks" / "tracker-commit-msg.py"
ARCHITECTURE_MD = REPO_ROOT / "docs" / "architecture" / "architecture.md"

_REGISTER_HEADING = "### 32.9 "
_REGISTER_ROW = re.compile(r"^\| (R\d+) \|", re.MULTILINE)


def _load_hook(path: Path, name: str):
    """Import a hook script by path: it is not part of the installable package."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _register_ids(text: str) -> set[str]:
    """The ids listed in the architecture register's table, empty when the section is gone.

    Read from the section body rather than the whole document, so an ``R<n>`` written in
    ordinary prose elsewhere cannot substitute for a row of the table under test.
    """
    start = text.find(_REGISTER_HEADING)
    if start < 0:
        return set()
    end = text.find("\n### ", start + 1)
    body = text[start:] if end < 0 else text[start:end]
    return set(_REGISTER_ROW.findall(body))


# --- R1: a timestamp is evidence, never a constraint --------------------------


def test_r1_no_write_is_rejected_on_a_clock_comparison(tmp_path: Path) -> None:
    """R1: a timestamp is evidence, never a constraint.

    The external tracker validated ``updated_at >= created_at`` and refused its own
    write when the host clock stepped backwards between two writes. Nothing in this repo
    set either timestamp, yet it failed landings and spent a rework attempt on
    basicly-m4zv.9. The replacement holds the requirement **by construction**: the store
    records a stamp and compares none, so there is no rejection to forgive.

    Asserted as behaviour rather than against a message register: the entry that used to
    forgive that message left with the process that produced it (basicly-vkh0.42.7), and
    a source scan would pass on a store that compared under another name.
    """
    repo = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(repo)
    # The clock takes POSIX seconds; the second write is a minute *earlier* than the
    # first, which is the step that used to refuse it.
    later, earlier = 1_786_000_000.0, 1_785_999_940.0

    for stamp in (later, earlier):
        kit.events.append(
            tracker.ledger_dir(repo),
            [kit.events.Draft("b-1", kit.events.KIND_COMMENT, {"text": f"at {stamp:.0f}"})],
            clock=lambda held=stamp: held,
        )

    assert [row["text"] for row in tracker.read_comments(repo, "b-1")] == [
        f"at {later:.0f}",
        f"at {earlier:.0f}",
    ]


def test_r1_the_signature_does_not_forgive_a_fixture_quoting_the_phrase() -> None:
    """The register must not become a way to launder a real failure.

    Matching is conjunctive per line: the defect phrase *plus* the store's own error
    class, which proves the failure came out of the ledger rather than out of a test's
    own fixture. A bare phrase must not match.
    """
    assert verify._defect_reason("assert 'another writer holds' in out") is None


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
    assert tracker.dependency_edge(dep) == ("basicly-a", "blocks")


def test_r2_a_row_that_is_not_an_edge_is_rejected_rather_than_guessed() -> None:
    """A missing id must not become an empty-string dependency on some node."""
    assert tracker.dependency_edge({"dependency_type": "blocks"}) is None
    assert tracker.dependency_edge("basicly-a") is None
    assert tracker.dependency_edge({"id": "", "type": "blocks"}) is None


def test_r2_blocking_dependencies_reads_the_echo_spelling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The landing order is the consumer that silently broke, so pin it end to end.

    Injected at ``tracker.read_record`` — the one reader every consumer shares — rather than
    at the spawn under it: since ``[tracker] mode`` became ``owned`` that reader answers
    from the ledger, so a fake on ``try_run_br`` reached nothing and the assertion below
    was comparing two empty sets.
    """
    record = {"id": "basicly-x", "dependencies": [{"depends_on_id": "basicly-a", "type": "blocks"}]}
    monkeypatch.setattr(tracker, "read_record", lambda *_a, **_k: record)
    assert merge.blocking_dependencies(tmp_path, "basicly-x") == frozenset({"basicly-a"})


# --- R3: validation rules are configurable, not compiled in -------------------


def test_r3_acceptance_criteria_are_required_for_a_work_type_lint_never_asks_about(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R3: the replacement's validation templates must be configurable per type.

    br's are built into the binary, and a ``chore`` is never asked for acceptance
    criteria — so lint staying silent does not mean they exist, it can mean the
    template never asked. The harness had to move the rule into its own gate
    (basicly-kjc5.36). This asserts the gate still catches the case br is quiet
    about.

    The ``chore`` carrying neither carrier is the whole input, because
    ``definition_of_ready`` no longer consults ``lint`` at all: basicly-wpc8.1 deleted
    that half and owns the rule in :func:`policy.required_sections`.
    """
    record = {
        "id": "basicly-x",
        "issue_type": "chore",
        "acceptance_criteria": None,
        "description": "",
    }
    monkeypatch.setattr(tracker, "read_record", lambda *_a, **_k: record)
    result = policy.definition_of_ready(tmp_path, "basicly-x")

    assert result.ready is False
    assert policy._ACCEPTANCE_CRITERIA_SECTION in result.missing


# --- R4: a multi-line field stays multi-line ----------------------------------


def test_r4_multi_line_acceptance_criteria_satisfy_the_gate_from_the_body(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
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
    # The structured field is empty precisely because it cannot hold this.
    record = {"id": "basicly-x", "acceptance_criteria": "", "description": body}
    # The criteria read goes through `tracker.read_record`, the one reader every consumer in
    # the package shares (basicly-tcmy.14), and it is the *only* read this gate makes.
    monkeypatch.setattr(tracker, "read_record", lambda *_a, **_k: record)
    result = policy.definition_of_ready(tmp_path, "basicly-x")

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
    hook = _load_hook(COMMIT_MSG_HOOK, "tracker_commit_msg_hook")
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
    seen = set()
    for log in sorted((REPO_ROOT / tracker_paths.LEDGER_DIR_NAME).glob("events-*.jsonl")):
        for line in log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            record = event.get("record") if isinstance(event, dict) else None
            if isinstance(record, str):
                seen.add(record)
    assert seen, "the ledger is the subject; it must not be empty"
    offenders = sorted(record for record in seen if record.count("-") > 1)
    assert offenders == [], f"slug-shaped ids break the commit gate: {offenders}"


# --- R6: a committed artifact carries no machine-specific path ----------------


def test_r6_the_committed_ledger_publishes_no_machine_specific_path(tmp_path: Path) -> None:
    """R6: nothing may write a host path into a committed artifact.

    The export published two users' home layouts to every clone (basicly-vkh0.5); the
    ledger carries the same risk, so the property moved with it.
    """
    repo = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(
                "b-1",
                kit.events.KIND_COMMENT,
                {"text": "see /home/someone/development/basicly/docs for context"},
            )
        ],
    )

    changed = tracker.scrub_ledger(repo)

    assert changed == 1
    committed = (tracker.ledger_dir(repo) / "events-0001.jsonl").read_text(encoding="utf-8")
    assert "/home/someone" not in committed


def test_r6_scrubbing_an_already_clean_ledger_changes_nothing(tmp_path: Path) -> None:
    """It runs on the commit path, so a clean ledger must not churn the file."""
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "b-1", title="no paths here")
    log = tracker.ledger_dir(repo) / "events-0001.jsonl"
    original = log.read_text(encoding="utf-8")

    assert tracker.scrub_ledger(repo) == 0
    assert log.read_text(encoding="utf-8") == original


# --- R7: concurrent readers and one writer must not corrupt shared state ------


def test_r7_a_reader_never_observes_a_torn_write_of_the_shared_ledger(tmp_path: Path) -> None:
    """R7: a reader colliding with a writer sees whole records or none, never half.

    A partially written line must be *discarded* rather than folded — a torn record read
    as real hands a lane work that does not exist. Asserted by injection, not by racing.
    """
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "b-1", title="whole")
    log = tracker.ledger_dir(repo) / "events-0001.jsonl"
    whole = log.read_text(encoding="utf-8")
    log.write_text(whole + whole.splitlines()[0][:40], encoding="utf-8")

    records = tracker.all_records(repo)

    assert [record["id"] for record in records] == ["b-1"]


def test_r7_a_missing_ledger_is_empty_without_waiting(tmp_path: Path) -> None:
    """R7: absence answers at once — every consumer of the bulk read is telemetry."""
    started = time.monotonic()

    assert tracker.all_records(tmp_path) == []
    assert time.monotonic() - started < 1.0


def test_r7_an_unreadable_ledger_is_not_reported_as_a_populated_one(tmp_path: Path) -> None:
    """R7: an unreadable ledger reads as empty, never as a record set to act on."""
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "b-1", title="whole")
    (tracker.ledger_dir(repo) / "events-0001.jsonl").write_text("{not json", encoding="utf-8")

    assert tracker.all_records(repo) == []


# --- R8: a contended write lock waits, and giving up is retryable -------------

# The ledger's own answer to a held lock, taken from `events.LedgerLock.__enter__`
# rather than composed: an invented fixture is what made the clock recogniser dead code
# through two "fixes" (basicly-aswc).
_R8_LOCK_TIMEOUT = (
    "E           basicly_tracker_kit_events.LockUnavailableError: another writer holds "
    "/repo/.basicly/ledger/.events.lock after 5.0s"
)


def test_r8_a_contended_write_lock_does_not_spend_the_lanes_rework_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R8: a lock a lane must *wait* for is not that lane's gate failing.

    The store fails a write outright when it cannot take the lock before the timeout,
    and the engine's lanes all reach one ledger through the redirect, so a gate contends
    with every sibling landing. Two gates failed that way in one session
    (basicly-m4zv.14) and each passed unchanged on the next attempt.

    Asserted at the landing's verdict rather than on the recogniser, because the
    recogniser is only worth having if it changes what the failure costs: an
    unreliable gate is bounded by ``MAX_UNRELIABLE_GATE_EVENTS`` and charged to no
    rework budget, while ``verify-failed`` spends one of two attempts.
    """
    failed = verify.VerifyReport("full", (verify.CheckResult("pytest", "fail", 1),))
    contended = verify.VerifyReport(
        "full", (verify.CheckResult("pytest", "fail", 1, output=_R8_LOCK_TIMEOUT),)
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: failed)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: contended)

    result = merge._verify_for_landing(tmp_path, "lane", tmp_path, "full", "basicly-x")

    assert result is not None
    assert result.status == merge.VERIFY_UNRELIABLE
    assert result.unreliable is True
    # The reason travels with the verdict: a reader must not have to guess which
    # dependency was forgiven, or on what grounds.
    assert "one lock" in result.detail


# --- R9: a publish never shrinks the artifact silently ------------------------


def test_r9_a_publish_that_would_shrink_the_export_is_refused_not_silent(
    tmp_path: Path,
) -> None:
    """R9: the replacement must not let a smaller store overwrite a larger artifact.

    A mutating `br` command auto-flushed a 426-record database over a 612-record
    committed export, deleting 187 records — 47 of them open — and reported success
    (basicly-b2n2). Nothing in the tracker layer noticed; three positive-control tests
    asserting a gate was not measuring an empty set were the only detection.

    Asserted on **content, not timestamps**, which is the refinement measured while
    filing it: `br sync --status` says "JSONL is newer" from mtime alone and fires on a
    healthy checkout where the import is a no-op, so a timestamp guard would cry wolf
    routinely and would not have distinguished this incident from clock ordering.

    Written against a local helper rather than the replacement's API, which does not
    exist yet: this pins the *rule* so the replacement inherits it as a gate rather
    than as prose. When the owned tracker lands, point this at its publish path.
    """

    def publish(existing: Path, records: list[str], *, intent: bool = False) -> None:
        """Stand-in for the replacement's publish: refuse a silent shrink."""
        prior = [line for line in existing.read_text(encoding="utf-8").splitlines() if line]
        if len(records) < len(prior) and not intent:
            raise ValueError(
                f"refusing to publish {len(records)} records over {len(prior)}: "
                "a shrink needs explicit intent"
            )
        existing.write_text("".join(f"{line}\n" for line in records), encoding="utf-8")

    export = tmp_path / "issues.jsonl"
    export.write_text("".join(f'{{"id":"b-{n}"}}\n' for n in range(612)), encoding="utf-8")
    smaller = [f'{{"id":"b-{n}"}}' for n in range(426)]

    with pytest.raises(ValueError, match=r"refusing to publish 426 records over 612"):
        publish(export, smaller)

    # The artifact is untouched by the refusal — a guard that half-writes is worse.
    assert len([line for line in export.read_text(encoding="utf-8").splitlines() if line]) == 612

    # Declared intent still shrinks, because a real deletion must remain expressible.
    publish(export, smaller, intent=True)
    assert len([line for line in export.read_text(encoding="utf-8").splitlines() if line]) == 426


# --- The register must stay complete -----------------------------------------


def test_every_requirement_in_the_design_register_has_a_test_here() -> None:
    """A prose register nobody tests is a wish list.

    Architecture §32.9 numbers the properties R1-R9; this asserts each id appears in
    a test name in this module, so adding a tenth defect to the register without
    a gate fails here rather than being noticed years later.
    """
    declared = _register_ids(ARCHITECTURE_MD.read_text(encoding="utf-8"))
    assert declared, f"no R<n> rows found under {_REGISTER_HEADING.strip()}"

    source = Path(__file__).read_text(encoding="utf-8")
    covered = {rid for rid in declared if f"def test_{rid.lower()}_" in source}
    assert covered == declared, f"requirements with no test: {sorted(declared - covered)}"


def test_the_register_read_returns_nothing_when_its_section_is_not_there() -> None:
    """The non-empty assert above is the fail-open guard, so its zero case is exercised.

    A reader that answers "no requirements" for a missing or renumbered section turns the
    gate above into a no-op reporting green, which is the shape the move off a
    deletion-scheduled document could have introduced. Scoping to the section is asserted
    too: an ``R<n>`` row written elsewhere in the document must not stand in for one.
    """
    assert _register_ids("") == set()
    assert _register_ids("## 1. Elsewhere\n\n| R1 | a row outside the register |\n") == set()
    assert _register_ids(f"{_REGISTER_HEADING}The register\n\n| R1 | a row |\n") == {"R1"}
