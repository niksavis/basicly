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
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import br, merge, policy, supervise, verify

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


# --- R7: concurrent readers and one writer must not corrupt shared state ------

# One reader process. It reads the shared tracker artifact through the harness's own
# reader in a tight loop until the writer says stop, and reports the smallest record
# count it ever saw.
#
# A separate *process*, not a thread, because that is the shape the defect has: five
# lane worktrees sharing one tracker through `.beads/redirect` are five OS processes,
# and a same-process test would be free to share a lock that the real readers cannot.
_READER = """
import sys
from pathlib import Path

from basicly import br

repo_root, ready, stop = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
ready.write_text("", encoding="utf-8")
worst = None
reads = 0
while not stop.exists():
    seen = len(br.export_records(repo_root))
    reads += 1
    if worst is None or seen < worst:
        worst = seen
print(f"{worst if worst is not None else -1} {reads}")
"""

# Large enough that the writer's rewrite is not instantaneous — a torn read has to be
# reachable or the gate cannot fail — and small enough to stay a unit test.
_R7_RECORDS = 3000
_R7_READERS = 4
_R7_ROUNDS = 40
# A crude backstop against an orphaned child, never the thing the test waits on: every
# wait below polls its condition (basicly's wait-for-the-condition rule).
_R7_TIMEOUT_S = 120.0

# br's observed response, quoted so the classification is pinned to the text br really
# emits rather than to one composed here (the basicly-aswc lesson).
_R7_STORAGE_ERROR = (
    '{"error": {"code": "DATABASE_ERROR", "message": "Database error: WAL file is '
    'corrupt: short read at frame 12: got 0, need 4120", "retryable": false}}'
)


def _r7_export_body(dirty: bool) -> str:
    """An export of :data:`_R7_RECORDS` records, in the state before or after a scrub.

    Both states have the same record *count*, which is what makes the count a clean
    tear detector: a reader that sees fewer records did not catch a different version
    of the file, it caught half of one.
    """
    leak = "/home/someone/development/basicly" if dirty else "somewhere"
    return (
        "\n".join(
            json.dumps(
                {"id": f"basicly-r7.{n}", "description": f"record {n} at {leak}", "pad": "x" * 160},
                separators=(",", ":"),
            )
            for n in range(_R7_RECORDS)
        )
        + "\n"
    )


def _r7_publish(export: Path, body: str) -> None:
    """Put *body* in place atomically, so only the code under test can tear a read.

    The rename waits a reader out for the same reason ``br._publish`` does, and this
    is the platform difference that makes it necessary rather than tidy: CPython
    opens a file for reading without ``FILE_SHARE_DELETE``, so renaming over a file
    another process is mid-read raises ``ERROR_SHARING_VIOLATION`` on Windows while
    succeeding silently on POSIX. This test keeps four readers deliberately mid-read,
    so on Windows the *fixture* is nearly always the one refused — it failed there
    with ``WinError 5`` while passing on both other runners.

    Retrying **here** does not soften anything the test asserts. The claim is about
    what a reader can observe, and the reader path still has no retry: this is setup
    putting a known-good file in place before the race, and it raises rather than
    returning False because a fixture that could not publish must fail loudly instead
    of quietly testing a stale body.
    """
    tmp = export.with_suffix(".fixture.tmp")
    tmp.write_text(body, encoding="utf-8")
    deadline = time.monotonic() + _R7_TIMEOUT_S
    delay = 0.005
    while True:
        try:
            tmp.replace(export)
        except OSError:
            assert time.monotonic() < deadline, "the fixture could not publish the export"
            time.sleep(delay)
            delay = min(delay * 2, 0.1)
        else:
            return


def _await(condition, what: str) -> None:
    """Poll *condition* until true, bounded by a monotonic deadline."""
    deadline = time.monotonic() + _R7_TIMEOUT_S
    while not condition():
        assert time.monotonic() < deadline, f"timed out waiting for {what}"
        time.sleep(0.005)


def test_r7_concurrent_readers_never_observe_a_torn_write_of_the_shared_export(
    tmp_path: Path,
) -> None:
    """R7: N readers and one writer against one tracker must not corrupt shared state.

    br fails this. Under the engine's own five-lane fan-out its storage layer tore its
    WAL and four of five lane dispatches died in the pre-flight read, each on a bead it
    had not been assigned (basicly-vkh0.10). We cannot fix br, so the register asserts
    the same property of the store *we* own — the committed JSONL export, which
    ``br.scrub_export`` rewrites on the commit path while every lane reads it through
    ``.beads/redirect``.

    Our own store had the same defect, in a quieter form: the scrub truncated the file
    before writing it, and ``export_records`` skips a line it cannot parse rather than
    raising — so a reader caught in that window got a *partial issue set with no error
    at all*. A silent wrong answer is worse than the DATABASE_ERROR it mirrors.

    No retry loop in the **reader** path, which is the point of the third criterion:
    ``export_records`` does not retry, so a passing run means the write was atomic and
    not that a reader got a second chance. The fixture's own publish does wait a reader
    out, for a Windows-only reason ``_r7_publish`` records; that is setup, not the
    assertion.
    """
    repo_root = tmp_path / "repo"
    export = repo_root / ".beads" / "issues.jsonl"
    export.parent.mkdir(parents=True)
    _r7_publish(export, _r7_export_body(dirty=True))

    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")}
    readers = []
    for n in range(_R7_READERS):
        ready, stop = tmp_path / f"ready.{n}", tmp_path / "stop"
        readers.append((
            # An argv list, never a shell string: a shell would mangle a Windows
            # path in `sys.executable` and fail only on that runner (basicly-5tjk).
            subprocess.Popen(  # nosec B603
                [sys.executable, "-c", _READER, str(repo_root), str(ready), str(stop)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
            ),
            ready,
        ))
    try:
        # Start writing only once every reader is actually reading: a writer that
        # finished first would make this pass by never racing anything.
        _await(lambda: all(ready.exists() for _, ready in readers), "the readers to start")
        for _ in range(_R7_ROUNDS):
            _r7_publish(export, _r7_export_body(dirty=True))
            assert br.scrub_export(repo_root) == _R7_RECORDS
    finally:
        (tmp_path / "stop").write_text("", encoding="utf-8")

    for proc, _ready in readers:
        stdout, stderr = proc.communicate(timeout=_R7_TIMEOUT_S)
        assert proc.returncode == 0, stderr
        worst, reads = (int(part) for part in stdout.split())
        assert reads > 0, "a reader that never read cannot have observed anything"
        assert worst == _R7_RECORDS, (
            f"a reader saw {worst} of {_R7_RECORDS} records: the shared export was "
            f"observable half-written"
        )

    # ...and the store is readable afterwards, in the state the last write left it.
    records = br.export_records(repo_root)
    assert len(records) == _R7_RECORDS
    assert "/home/someone" not in json.dumps(records)


def test_r7_a_dispatch_lost_to_the_store_is_retryable_not_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """R7: a storage-contention failure must reach the caller marked retryable.

    br marks its own ``DATABASE_ERROR`` ``retryable: false``, and the supervisor
    believed it: the failure was charged to the lane's dispatch rework budget, so
    ``basicly-tcmy.11`` reached the cap and was parked without an agent ever starting.
    Nothing about the lane had failed — nothing had run.

    Asserted at the routing boundary rather than on the flag, because the flag is only
    worth having if it changes which counter is charged.
    """
    charged: list[str] = []
    monkeypatch.setattr(
        supervise.policy,
        "record_rework",
        lambda _repo, _issue, gate: (charged.append(gate), 1)[1],
    )
    monkeypatch.setattr(
        supervise.policy, "load_policy", lambda _repo: SimpleNamespace(max_rework=3)
    )
    outcome = supervise.LaneOutcome(
        issue_id="basicly-tcmy.11",
        runner_name="claude",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="lane dispatch failed: br comments list failed: " + _R7_STORAGE_ERROR,
        transient=True,
    )

    routed = supervise._route_failed(tmp_path, outcome.issue_id, outcome)

    assert routed.route == "retry"
    assert charged == [supervise.TRACKER_GATE], (
        "the lane's dispatch budget must not pay for the store's contention"
    )


def test_r7_a_lane_failure_that_is_not_the_store_still_costs_a_dispatch_attempt(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The mirror case, so the new counter cannot become an escape from the old one.

    A lane whose agent genuinely failed must still spend its bounded dispatch
    attempts — otherwise the retryable classification is a way to loop forever.
    """
    charged: list[str] = []
    monkeypatch.setattr(
        supervise.policy,
        "record_rework",
        lambda _repo, _issue, gate: (charged.append(gate), 1)[1],
    )
    monkeypatch.setattr(
        supervise.policy, "load_policy", lambda _repo: SimpleNamespace(max_rework=3)
    )
    outcome = supervise.LaneOutcome(
        issue_id="basicly-x",
        runner_name="claude",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="runner exited 3",
    )

    assert supervise._route_failed(tmp_path, "basicly-x", outcome).route == "retry"
    assert charged == [supervise.DISPATCH_GATE]


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
