"""The single seam to the ``br`` (beads) tracker CLI.

Every harness module used to carry its own private ``_run_br`` copy plus a
``shutil.which("br")`` probe — eight call sites to audit whenever br's CLI
or JSON output changes. This module is now the only place that spawns br:
one invocation contract, one absence message, and a one-time version probe
that warns when the installed br is older than the floor the harness was
built against.

The owned store this seam is being migrated onto sits below it, in two modules
that spawn nothing: :mod:`basicly.owned_store` answers where the ledger is and
which kit reads it, and :mod:`basicly.mirror` says what one accepted br write
becomes as ledger events. The boundary is *the process* against *the store* —
what stays here is every decision that depends on br having been run.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path
from typing import Any

from basicly import mirror, owned_store, redact, tracker_usage

# The oldest br this harness is exercised against (see `br --version`).
# The probe warns below this floor; it never blocks — br's core commands
# are stable and a hard failure would strand every loop.
MIN_VERSION = (0, 2)

# The exact br the harness is *tested* against: what `.scripts/install_br.py`
# pins by digest and what CI puts on PATH. It lives here, not in that script,
# because the script is not importable (it is `.scripts/`, not a package) while
# both sides can import this module — one constant, no drift between the version
# CI installs and the version the engine expects.
#
# MIN_VERSION alone could not catch what happened on 2026-07-28 (basicly-o7z5):
# it compares major.minor only, so 0.2.19 and 0.2.16 are indistinguishable to
# it, and it is a floor with no ceiling. A machine silently upgraded to 0.2.19,
# whose `gate report` rejects the harness's call, and the only symptom was four
# integration tests failing on that machine while CI — still on the pin — stayed
# green. Upgrading *past* the pin is not a fix either: br's current `main`
# targets schema 17 and its reviewed migration accepts only 13->17 and 14->17,
# so a 0.2.19 database (schema 16 here) has no supported path forward
# (beads_rust#398). The pin is the supported state in both directions.
PINNED_VERSION = "0.2.16"

_probed_paths: set[str] = set()


def which() -> str | None:
    """Path to the br executable, or None when not installed."""
    return shutil.which("br")


def _probe_version(br_path: str) -> None:
    """Warn once per process when the installed br is not the version we test."""
    if br_path in _probed_paths:
        return
    _probed_paths.add(br_path)
    try:
        # `br_path` is `shutil.which("br")`'s answer and the argv is a literal, so
        # nothing here is caller-supplied.
        proc = subprocess.run(  # noqa: S603 — resolved path, literal argv
            [br_path, "--version"], capture_output=True, text=True, check=False, timeout=10
        )
    except OSError, subprocess.TimeoutExpired:
        return
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", proc.stdout or "")
    if match is None:
        return
    found = match.group(0)
    version = (int(match.group(1)), int(match.group(2)))
    if version < MIN_VERSION:
        floor = ".".join(str(part) for part in MIN_VERSION)
        print(
            f"Warning: br {found} is older than the harness floor "
            f"({floor}); upgrade br if tracker commands misbehave.",
            file=sys.stderr,
        )
    elif found != PINNED_VERSION:
        # Any difference from the pin, in either direction. Naming the fix in
        # the warning is the point: the failure this catches shows up as
        # unrelated-looking test failures, hours away from the upgrade.
        print(
            f"Warning: br {found} on PATH ({br_path}) is not the pinned "
            f"{PINNED_VERSION} the harness is tested against; tracker behaviour "
            f"may differ. Reinstall the pin with "
            f"`python .scripts/install_br.py --bin-dir <dir-on-PATH>`.",
            file=sys.stderr,
        )


def _spawn(
    br_path: str, repo_root: Path, args: list[str], *, attempt: int = 1
) -> subprocess.CompletedProcess[str]:
    """Spawn br and record the invocation into the usage ledger (basicly-vkh0.1).

    The one place the engine's tracker calls are measured. It sits here rather
    than at each caller for the same reason this module exists at all: a new call
    site is instrumented by construction, so the surface list Phase 6 freezes
    cannot silently miss a subcommand somebody added later. Only flag *names*
    reach the ledger — never values, which would put issue titles and home
    directory paths into a committed file.
    """
    with tracker_usage.timed(
        repo_root, "br", args, site=tracker_usage.SITE_ENGINE, attempt=attempt
    ) as timer:
        # `br_path` is resolved by `which()` and `args` is built by this module's own
        # callers from typed values, never from a shell string (see `_spawn`'s docstring).
        proc = subprocess.run(  # noqa: S603 — resolved path, engine-built argv
            [br_path, *args], cwd=repo_root, capture_output=True, text=True, check=False
        )
        timer.ok = proc.returncode == 0
    return proc


# br rejects an update whose `updated_at` precedes its `created_at`. On a host
# whose clock can step backwards between two consecutive br calls, a
# create-then-update pair therefore fails for a reason that has nothing to do
# with the request (basicly-jr0l.41): it blocked a landing twice consecutively,
# with a different victim test each run, which reads as suite flakiness rather
# than as the dependency defect it is.
#
# Bounded by a *deadline*, not by a ladder of sleeps (basicly-jr0l.42). The wait a
# skew needs is however long the host clock takes to advance past the record's
# `created_at`, and br's error names no timestamps, so that duration cannot be
# derived — a fixed 0.35s ladder simply could not span a larger step, which is why
# the defect recurred after the first fix. A deadline covers any step shorter than
# itself without pretending to know which.
#
# The deadline is read from a *monotonic* clock, which matters more than usual
# here: the wall clock is the thing misbehaving, so it cannot be used to bound a
# wait on itself. tracker_usage.timed already documents the same hazard.
#
# Deliberately not a general retry — any other non-zero exit returns untouched so
# a real error still fails fast. Each attempt carries its attempt number into the
# usage ledger, so how many attempts a real skew needs becomes readable and the
# deadline below can stop being a guess. Requirements input for the replacement
# (basicly-vkh0.6), which must never branch on a wall-clock comparison at all —
# the rule D3 already states for the event log.
# Recognised from the *message* plus a timestamp field it was reported against, not
# from one joined phrase (basicly-aswc). The phrase this used to look for —
# "updated_at: cannot be before created_at" — never appears in br's output: br prints
# a Rust struct, `ValidationError { field: "updated_at", message: "cannot be before
# created_at" }`, so the substring was absent, :func:`_is_clock_skew` always answered
# False, and everything above was dead code on the only error it exists for. That is
# why the defect recurred after both basicly-jr0l.41 and basicly-jr0l.42 "fixed" it,
# and why jr0l.42's attempt-count instrumentation would have read zero attempts.
# Matching the two halves independently survives a re-spelling of the wrapper.
_CLOCK_SKEW_MESSAGE = "cannot be before created_at"
# br reports the same message against `closed_at` as well, in the same response: a
# `br close` on a backwards-stepped clock fails on both fields at once.
_CLOCK_SKEW_FIELDS = ("updated_at", "closed_at")
_TRANSIENT_DEADLINE_S = 5.0
_TRANSIENT_FIRST_WAIT_S = 0.05
_TRANSIENT_MAX_WAIT_S = 1.0

# The second transient br failure the harness has been billed for (basicly-vkh0.10).
# Under the engine's own five-lane fan-out — five worktrees sharing one tracker
# through `.beads/redirect` — four of five lane dispatches died in the pre-flight
# tracker read, each on a bead it had not been assigned. The response is quoted in
# :func:`is_transient_storage_error`'s docstring, where it is evidence rather than
# something a linter has to read as dead code.
#
# It is transient, and demonstrably so: the database survived intact, the WAL was
# truncated to a bare header, and five concurrent reads immediately afterwards passed
# 5/5. br's own `retryable: false` is therefore wrong about its own error, and that
# single wrong field is what cost the run — the supervisor treated a storage hiccup as
# a terminal lane failure, so one lane exhausted the dispatch rework cap without ever
# starting an agent and was parked.
#
# Keyed on br's own error *code*, observed in that response, rather than on a
# phrase composed from the sqlite message: the code is the field br fills in for every
# storage-layer failure, while the message text varies with which page tore. Composing
# a fixture instead of observing one is what made the clock-skew recogniser dead code
# through two "fixes" (basicly-aswc), so the register does not repeat it.
#
# Deliberately a *category*, not a whitelist of sqlite strings. A DATABASE_ERROR that
# is genuinely permanent still fails — the deadline below bounds the wait and the
# unrescued failure is returned to the caller untouched, exactly as a persistent clock
# skew is. Requirements input for the replacement (R7 in docs/design/work-tracker.md),
# which must not corrupt shared state under concurrency at all, and must mark a
# contention failure retryable when it does report one.
_STORAGE_ERROR_CODE = "DATABASE_ERROR"
_STORAGE_ERROR_PREFIX = "Database error:"


def is_transient_storage_error(text: str) -> bool:
    """True when *text* carries br's storage-layer failure, which is retryable.

    The response this is keyed on, observed on the 2026-08-02 five-lane pass::

        {"error": {"code": "DATABASE_ERROR", "message":
         "Database error: WAL file is corrupt: short read at frame 12: got 0,
          need 4120",
         "retryable": false}}

    Note the last field: br classifies its own storage contention as terminal, and
    it is wrong — the same store answered five concurrent reads correctly moments
    later. This function is the harness overriding that verdict.

    Takes text rather than a process because the harness's own wrapper has usually
    already turned the failure into a ``RuntimeError`` by the time a caller needs to
    decide whether to back off — :func:`run_br` formats br's output into the message,
    so ``str(exc)`` is the same evidence the process carried. **The text must be a
    failure's output**; :func:`_is_storage_contention` is the guard that guarantees
    it, and the reason that guard is not optional is below.
    """
    return _STORAGE_ERROR_CODE in text or _STORAGE_ERROR_PREFIX in text


def _is_storage_contention(proc: subprocess.CompletedProcess[str]) -> bool:
    """True when br refused this call because its storage layer was contended.

    Two guards, and neither is defensive padding — both were found by running the
    recogniser against the live tracker.

    **A zero exit is never contention.** The bead that filed this requirement quotes
    the whole error envelope in its own description, so ``br show basicly-vkh0.10
    --json`` *succeeds* and returns the phrase as record content. Without the exit
    check the recogniser answers True on that success, and the retry loop is then the
    only thing standing between us and retrying every read of that bead — a single
    ordering the next caller is free to get wrong. The register's own rule (R1) is
    that a recogniser must not become a way to launder a non-failure, so it
    discriminates here rather than relying on where it is called from.

    **stderr wins over stdout**, rather than the two being concatenated. On a failure
    that printed records before it died, the payload is on stdout and the diagnosis is
    on stderr; reading them joined lets record text outvote the actual error. This is
    also exactly what :func:`run_br` puts in the ``RuntimeError`` it raises, so the
    process and the exception are classified off the same bytes.
    """
    return proc.returncode != 0 and is_transient_storage_error(proc.stderr or proc.stdout or "")


def _is_clock_skew(proc: subprocess.CompletedProcess[str]) -> bool:
    """True when br refused this write because the host clock stepped backwards.

    Deliberately narrow: the timestamp-ordering message *and* a timestamp field it was
    reported against must both be present, so an unrelated validation failure still
    fails fast instead of being retried until the deadline. The message alone would
    not do — it has to be attributed to a timestamp field rather than to some later
    field that reuses the wording.
    """
    output = f"{proc.stderr or ''}{proc.stdout or ''}"
    if _CLOCK_SKEW_MESSAGE not in output:
        return False
    return any(field in output for field in _CLOCK_SKEW_FIELDS)


def _is_transient(proc: subprocess.CompletedProcess[str]) -> bool:
    """True when this failure is about the host or the store, not about the request.

    Two recognised causes, both defects we have already been billed for: a backwards
    clock step (R1) and storage contention under the engine's own fan-out (R7).
    Anything else is a real error and must fail fast — this is an escape hatch for
    named defects, never a retry policy for every br error.
    """
    return _is_clock_skew(proc) or _is_storage_contention(proc)


def _spawn_tolerating_transient(
    br_path: str,
    repo_root: Path,
    args: list[str],
    *,
    sleep: Callable[[float], None] | None = None,
    monotonic: Callable[[], float] | None = None,
) -> subprocess.CompletedProcess[str]:
    """:func:`_spawn`, retrying only the transient br failures :func:`_is_transient` names.

    Waits between attempts rather than spinning: retrying instantly re-reads the
    same skewed clock, and hits the same contended store. *sleep* and *monotonic*
    are injectable so a test can prove both the retry and the deadline without
    spending wall-clock time, and both resolve at call time rather than as bound
    defaults — a default of ``time.sleep`` freezes the reference at import and
    silently ignores both the parameter and a patched ``time.sleep``.

    One deadline covers both causes for the same reason it was chosen for the first:
    neither error names how long its condition will last, so the wait cannot be
    derived from the message. Whatever is still failing when the deadline passes is
    returned untouched, so a caller always sees the real failure rather than a hang.
    """
    wait = sleep if sleep is not None else time.sleep
    clock = monotonic if monotonic is not None else time.monotonic
    deadline = clock() + _TRANSIENT_DEADLINE_S
    delay = _TRANSIENT_FIRST_WAIT_S
    attempt = 1
    while True:
        proc = _spawn(br_path, repo_root, args, attempt=attempt)
        if proc.returncode == 0 or not _is_transient(proc):
            return proc
        if clock() >= deadline:
            return proc
        wait(delay)
        delay = min(delay * 2, _TRANSIENT_MAX_WAIT_S)
        attempt += 1


# --- Read-only sections (gates-and-rework-design.md §2) ----------------------
#
# A pre-flight gate reads the world, returns a verdict, and writes nothing. The
# rule earns enforcement here, at the funnel, rather than at each gate's call
# site: both of the incidents behind it were *tracker* writes — a hand-recorded
# verify gate that shipped a bead with its code stranded unmerged, and an approved
# ship checkpoint that wedged phase derivation with no un-approve path — and br
# comments and gate results cannot be deleted, so a write that should have been a
# refusal is unrecoverable. A gate that cannot reach this function cannot leave
# the tracker in a state no command can undo.
#
# The guard covers the tracker only, which is the boundary the rule needs and no
# more. The engine's other writes during a check — the verify run artifact and the
# usage ledger under `.basicly/usage/` — are self-ignored, rewritten by every run,
# and undone by deleting the file; neither can strand a bead.
#
# :func:`basicly.policy.preflight_gate` is the typed entry point. This is the
# mechanism it installs, and it lives here because :mod:`basicly.policy` sits
# above this module in the import contract while every other write path
# (:mod:`basicly.verify`, :mod:`basicly.rubrics`, :mod:`basicly.decisions`) is a
# sibling of it or higher — the funnel is the one place all of them pass through.


class TrackerWriteRefusedError(Exception):
    """A tracker write was attempted inside a read-only section.

    Deliberately **not** a :class:`RuntimeError`, unlike every other failure these
    funnels raise. Two dozen call sites across the engine wrap a br call in
    ``except RuntimeError, OSError, ValueError`` and answer None or a typed
    absence — :func:`read_record` is one — so a refusal in that family would be
    swallowed into "the tracker had nothing to say", which is the fail-open
    direction for the one guard whose whole purpose is that a write cannot slip
    through. A violation here is a gate breaking its own declared type, not a
    tracker that misbehaved, and it must reach the top.
    """


# Scoped to the calling thread by construction: a supervised pass runs its lanes
# in a ThreadPoolExecutor, and a process-global flag would let one lane's
# read-only section refuse another lane's legitimate write. The honest bound is
# the other direction — a section that hands its work to a *new* thread does not
# guard that thread, because a fresh context starts empty.
_read_only: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "br_read_only", default=None
)


@contextlib.contextmanager
def read_only(reason: str) -> Iterator[None]:
    """Refuse every tracker write attempted in this thread until the block exits.

    *reason* is quoted in the refusal, so the traceback names the gate that must
    not have written rather than only the write it attempted. Restores the previous
    state on the way out, exception or not, so a refused write does not leave the
    tracker read-only for the rest of the process.
    """
    token = _read_only.set(reason)
    try:
        yield
    finally:
        _read_only.reset(token)


def _refuse_write_in_read_only(args: Sequence[str]) -> None:
    """Raise when *args* is not a read and a read-only section is active.

    Fail-closed on an unclassified surface. ``tracker_usage.READ_SUBCOMMANDS`` is
    deliberately not exhaustive, so "not known to be a read" is the only safe test
    a guard against unrecoverable writes can make: a refusal is loud and fixed by
    classifying the surface, while a leaked write is silent and permanent.

    The two cases get different messages on purpose. A known write is the caller's
    bug and there is nothing to reclassify; only an *unclassified* surface should
    ever send a reader to :mod:`basicly.tracker_usage`, because telling them to
    classify ``comments add`` as a read is how a guard gets disabled by its own
    error text.
    """
    reason = _read_only.get()
    if reason is None:
        return
    surface, _ = tracker_usage.split_invocation(list(args))
    access = tracker_usage.classify_access(surface)
    if access == "read":
        return
    named = f"br {surface}" if surface else " ".join(args)
    fault = (
        f"{named} is not classified, and unknown is not read: classify it in "
        "tracker_usage if it only reads"
        if access == "unclassified"
        else f"{named} writes"
    )
    raise TrackerWriteRefusedError(f"{reason} must write nothing, but {fault}")


def run_br(
    repo_root: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a br subcommand; raises when br is absent — the harness needs the tracker.

    Raises:
        TrackerWriteRefusedError: *args* writes and a :func:`read_only` section is active.
    """
    _refuse_write_in_read_only(args)
    br_path = which()
    if not br_path:
        raise RuntimeError("br is not on PATH; the harness requires the beads tracker")
    _probe_version(br_path)
    proc = _spawn_tolerating_transient(br_path, repo_root, args)
    if check and proc.returncode != 0:
        raise RuntimeError(f"br {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    _mirror_write(repo_root, args, proc)
    return proc


def try_run_br(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a br subcommand; None when br is absent (soft call sites).

    Refuses a write inside a :func:`read_only` section by raising, exactly as
    :func:`run_br` does. Soft here means "tolerates a missing tracker", never
    "tolerates writing when a gate promised not to".

    Raises:
        TrackerWriteRefusedError: *args* writes and a :func:`read_only` section is active.
    """
    _refuse_write_in_read_only(args)
    br_path = which()
    if not br_path:
        return None
    _probe_version(br_path)
    proc = _spawn_tolerating_transient(br_path, repo_root, args)
    _mirror_write(repo_root, args, proc)
    return proc


# --- The owned tracker: dual write, then the flip (basicly-vkh0.19) ----------
#
# Steps 3 and 4 of the cutover in `docs/design/work-tracker.md` §5. *Where* the
# owned store is and *what* a write becomes in it are :mod:`basicly.owned_store`
# and :mod:`basicly.mirror`; what stays here is *when* either applies, because
# this module is the one place the engine spawns br and therefore the only place
# that sees a write land on the external tracker.
#
# The store's vocabulary is re-bound below so ``br.<name>`` keeps naming it. That
# is not tidiness: :mod:`basicly.config` installs its mode reader through this
# module (the inversion :func:`owned_store.set_mode_reader` documents), and the
# engine's callers read the mode constants off the same seam they spawn through.
# Each is an alias rather than a wrapper — one object, so a test that patches
# ``br.kit`` patches the loader this module calls.
MODE_EXTERNAL = owned_store.MODE_EXTERNAL
MODE_DUAL = owned_store.MODE_DUAL
MODE_OWNED = owned_store.MODE_OWNED
TRACKER_MODES = owned_store.TRACKER_MODES
DEFAULT_TRACKER_MODE = owned_store.DEFAULT_TRACKER_MODE
KIT_TRACKER_DIR = owned_store.KIT_TRACKER_DIR
LEDGER_DIR = owned_store.LEDGER_DIR
SCHEDULER_KIT_MODULE = owned_store.SCHEDULER_KIT_MODULE
TrackerDivergenceError = owned_store.TrackerDivergenceError
set_mode_reader = owned_store.set_mode_reader
tracker_mode = owned_store.tracker_mode
ledger_dir = owned_store.ledger_dir
kit = owned_store.kit


def _mirror_write(
    repo_root: Path, args: Sequence[str], proc: subprocess.CompletedProcess[str]
) -> None:
    """Record on the owned ledger the write br just accepted.

    A no-op in :data:`MODE_EXTERNAL`, and a no-op for a call br refused — a rejected
    write changed neither store, so mirroring it is what would create the divergence.

    Every failure below is raised, never logged, and that is the acceptance criterion
    rather than a preference: the whole value of running two stores side by side is
    that they hold the same facts, and the *only* moment at which a missing mirror is
    cheap to fix is before the next write lands on top of it.

    :func:`basicly.mirror.drafts` owns what each write becomes, including which writes
    have no translation and therefore fail the command.
    """
    if tracker_mode(repo_root) == MODE_EXTERNAL or proc.returncode != 0:
        return
    kit_module = kit(repo_root)
    try:
        drafts = mirror.drafts(kit_module, args, proc.stdout or "")
        if drafts:
            kit_module.events.append(
                ledger_dir(repo_root), drafts, redact=redact.redact_machine_paths
            )
    except TrackerDivergenceError:
        raise
    except (kit_module.events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"br {' '.join(args)} landed on the external tracker and not on the owned ledger: {exc}"
        ) from exc


def owned_record(repo_root: Path, issue_id: str) -> dict | None:
    """*issue_id* as the owned ledger holds it, in ``br show --json``'s shape.

    The flipped half of :func:`read_record`, and it keeps that function's contract
    rather than inventing one: None for every way the read comes back without a
    record, never an exception.

    **A tombstoned record reads as absent**, which is not a detail. The owned store
    expresses a deletion by keeping the record and flagging it (`events.py`), and the
    live tracker expresses the same deletion by not returning the record at all. A
    reader that saw the tombstoned record would hand out work on a bead somebody
    deleted — the defect `differential.is_ready` names — so the two stores are made to
    spell absence the same way here, at the seam, once.

    Two passes over one event list, and the second is not redundancy: the fold is the
    authority for status, fields and comments, while `events.py` has no handler for the
    ``edge`` and ``gate`` kinds at all, so `differential.views_from_events` is the one
    reader of those. Duplicating either here is the drift the kit's own loaders exist
    to prevent.
    """
    try:
        kit_module = kit(repo_root)
        found = kit_module.read_ledger(ledger_dir(repo_root))
        # Not named `folded`: `.scripts/wired_or_deleted.py` counts an identifier
        # anywhere outside `tests/` as a read of a same-named record field, so a local
        # by that name silently retires the suppression on
        # `basicly.supervise.DispatchBundle.folded` and turns that gate red here.
        ledger_fold = kit_module.events.fold(found)
        state = ledger_fold.records.get(issue_id)
        if state is None or state.tombstoned:
            return None
        view = kit_module.views_from_events(found).get(issue_id)
    except TrackerDivergenceError, OSError, ValueError:
        return None
    reserved = kit_module.migrate.RESERVED_KEYS
    record: dict = {key: value for key, value in state.fields.items() if key not in reserved}
    record["id"] = issue_id
    record["status"] = state.status or ""
    record["comments"] = [{"text": text} for text in state.comments]
    record["dependencies"] = [
        {"id": edge.target, "dependency_type": edge.type}
        for edge in (view.dependencies if view is not None else ())
    ]
    return record


# --- Harness markers, carried natively (basicly-s5li) ------------------------
#
# Step 5 of the cutover in `docs/design/work-tracker.md` §5, and the step that
# actually removes br from the engine. `comments` is the largest remaining
# dependency — 26 of the engine's 55 `_run_br` call sites and 45% of all recorded
# br traffic — and measured over the live tracker on 2026-08-07, **89% of it
# (1646 of 1834 comments) is `[harness-*]` markers**: checkpoint approvals,
# grants, gate records, rework counters, needs-input, the human-wait clock,
# dispatch records and spend rollups, all using a beads comment purely as
# transport. That is what the plan's standing constraint anticipated when it said
# to land evidence as markers "a format we own, which migrates with us".
#
# So this is not a data migration — the step-1 import already wrote 1831 comment
# events into the ledger. It is a seam change, and it takes the shape the two
# rungs above it took: four functions here, and the callers keep their contracts.
#
# **The 188 human comments are deliberately out of scope.** A human writing prose
# on a bead runs br directly, and the engine never spawns that. Removing the
# engine's dependency does not require removing the human's, and conflating them
# is how this grows into the general-purpose tracker §2 declines to build.
#
# **What a caller must not conclude from `owned`:** the marker families this seam
# carries stop reaching the external tracker, so a `br comments list` run by hand
# no longer shows them and the shadow differential's comment query diverges by
# construction. That is the point of no return §5 step 4 names, and it is why the
# differential (step 2) is run on `dual`, before this. The two surfaces still
# spawning `comments` at their own call site — `decompose`'s sizing markers and
# `supervise`'s found-info records — are each internally consistent, writing and
# reading the same store; retiring them is basicly-wpc8's.

# How a marker the engine wrote itself says it got here. Distinguishes a native
# write from one the dual write mirrored (:data:`mirror.MIRROR_PROVENANCE`) and one
# `migrate.py` extracted out of the export, and it is one of
# `migrate.RESERVED_KEYS`, so it is dropped again when a record is rendered back.
OWNED_PROVENANCE = "engine"

# The two keys a comment row carries, in br's spelling. The owned ledger holds the
# body under the same ``text`` key (`events.KIND_COMMENT`) and the stamp as the
# event's ``ts``, so the rendering below is a rename of one field rather than a
# second shape for a caller to learn.
COMMENT_TEXT_KEY = "text"
COMMENT_STAMP_KEY = "created_at"


def _comments_add_argv(issue_id: str, body: str) -> list[str]:
    """The br invocation one marker write is, whichever store ends up taking it.

    Built even on the owned path, because it is what :func:`_refuse_write_in_read_only`
    classifies: a gate that promised to write nothing must be refused for the *fact* it
    is about to record, not for which store happens to be authoritative this week.
    """
    return ["comments", "add", issue_id, body]


def _append_owned_comment(repo_root: Path, issue_id: str, body: str) -> None:
    """Record *body* on *issue_id* as a ledger ``comment`` event, and nothing else.

    The owned half of :func:`add_comment`. Every failure becomes a
    :class:`TrackerDivergenceError` — a ``RuntimeError``, so a caller that already
    handles a br failure handles this one unchanged, which is what makes the flip
    invisible at the call site. The read-only refusal is the caller's; see
    :func:`add_comment`.

    Raises:
        TrackerDivergenceError: the kit is not installed, or the append failed.
    """
    kit_module = kit(repo_root)
    payload = {
        kit_module.migrate.PROVENANCE_KEY: OWNED_PROVENANCE,
        COMMENT_TEXT_KEY: body,
    }
    draft = kit_module.events.Draft(issue_id, kit_module.events.KIND_COMMENT, payload)
    try:
        kit_module.events.append(ledger_dir(repo_root), [draft], redact=redact.redact_machine_paths)
    except (kit_module.events.LedgerError, OSError, ValueError) as exc:
        raise TrackerDivergenceError(
            f"the marker on {issue_id} did not reach the owned ledger: {exc}"
        ) from exc


def _owned_comment_rows(repo_root: Path) -> dict[str, list[dict]]:
    """Every record's comments, keyed by record, each row in ``br comments list``'s shape.

    Canonical order — ``(record, seq, id)`` — rather than file order, so the rows come
    back oldest-first however the log was concatenated. Both readers depend on that:
    `decisions` documents its per-bead read as oldest-first, and `policy`'s wait clock
    takes the *first* stamp it sees for a request.

    **A tombstoned record answers empty**, the same rule and for the same reason as
    :func:`owned_record`: the two stores spell a deletion differently, and a reader that
    served a deleted bead's markers would count rework on work somebody removed.
    """
    kit_module = kit(repo_root)
    found = kit_module.read_ledger(ledger_dir(repo_root))
    ledger_fold = kit_module.events.fold(found)
    rows: dict[str, list[dict]] = {}
    for event in kit_module.events.canonical_order(found):
        if event.kind != kit_module.events.KIND_COMMENT:
            continue
        state = ledger_fold.records.get(event.record)
        if state is not None and state.tombstoned:
            continue
        text = event.payload.get(COMMENT_TEXT_KEY)
        if not isinstance(text, str):
            continue
        rows.setdefault(event.record, []).append({
            COMMENT_TEXT_KEY: text,
            COMMENT_STAMP_KEY: event.ts,
        })
    return rows


def _br_comment_rows(stdout: str, issue_id: str) -> list[dict]:
    """``br comments list --json``'s reply as rows, raising when it is not usable.

    Raises rather than answering empty, which is the opposite of what two of the three
    callers used to do on their own. It is the safe direction here and the choice is
    made once: every marker family this reads is a *counter* or a *refusal* — rework
    attempts against a cap, an unanswered needs-input, an open checkpoint — so an
    unreadable tracker that answers "no markers" reads as "nothing is blocking" and the
    loop advances past exactly the gate the marker existed to hold. :func:`try_read_comments`
    is the soft contract, for the evidence readers where an empty answer is honest.

    Raises:
        RuntimeError: the reply was not a JSON array of rows.
    """
    try:
        payload = json.loads(stdout)
    except ValueError as exc:
        raise RuntimeError(f"br comments list {issue_id} returned no usable JSON: {exc}") from exc
    if not isinstance(payload, list):
        raise RuntimeError(
            f"br comments list {issue_id} returned {type(payload).__name__}, not an array"
        )
    return [row for row in payload if isinstance(row, dict)]


def add_comment(repo_root: Path, issue_id: str, body: str) -> None:
    """Record one harness marker on *issue_id*, in whichever store is authoritative.

    The marker write seam. In :data:`MODE_OWNED` the fact lands in the ledger and br is
    **not spawned**; on the rungs below it goes to br, and :func:`_mirror_write` keeps
    the ledger in step exactly as before.

    Raises:
        TrackerWriteRefusedError: a :func:`read_only` section is active. Refused **here**,
            at the seam, rather than only inside :func:`run_br`: a recorded comment cannot
            be deleted from either store, and on the owned path there is no br call to
            inherit the guard from. Checking it once at the entry point means the refusal
            is a property of the fact being recorded rather than of which store is
            authoritative this week — which is also what keeps it enforced on a rung where
            the funnel below is stubbed out. Re-checked by :func:`run_br` on the external
            path, and a second identical refusal costs nothing.
        RuntimeError: the write did not land. :class:`TrackerDivergenceError` on the owned
            path, br's own failure below it; both are ``RuntimeError``, which is what the
            callers already catch.
    """
    _refuse_write_in_read_only(_comments_add_argv(issue_id, body))
    if tracker_mode(repo_root) == MODE_OWNED:
        _append_owned_comment(repo_root, issue_id, body)
        return
    run_br(repo_root, _comments_add_argv(issue_id, body))


def try_add_comment(repo_root: Path, issue_id: str, body: str) -> bool:
    """:func:`add_comment` for a caller that tolerates the write not landing.

    False when nothing was recorded. Soft here means "tolerates a store that cannot
    answer", never "tolerates writing when a gate promised not to" — the read-only
    refusal is raised before either store is reached and is deliberately outside the
    caught set, which is the same split :func:`try_run_br` makes.
    """
    _refuse_write_in_read_only(_comments_add_argv(issue_id, body))
    if tracker_mode(repo_root) != MODE_OWNED:
        proc = try_run_br(repo_root, _comments_add_argv(issue_id, body))
        return proc is not None and proc.returncode == 0
    try:
        _append_owned_comment(repo_root, issue_id, body)
    except TrackerDivergenceError:
        return False
    return True


def read_comments(repo_root: Path, issue_id: str) -> list[dict]:
    """*issue_id*'s comments, oldest-first, each row carrying ``text`` and ``created_at``.

    The marker read seam, and the hard half of its contract: a store that cannot answer
    raises rather than reporting an empty history. See :func:`_br_comment_rows` for why
    that direction rather than the other.

    One shape from both stores, for the reason :func:`owned_record` renders into
    ``br show``'s: the caller then parses one thing, so the flip is a change of source and
    not of contract. The stamp is the tracker's own in both — br's ``created_at``, the
    ledger's event ``ts`` — which is what keeps `policy`'s wait clock measuring an
    interval that outlives the process that opened it.

    Raises:
        RuntimeError: br is absent or failed, its reply was not usable, or the kit will
            not load.
    """
    if tracker_mode(repo_root) == MODE_OWNED:
        return _owned_comment_rows(repo_root).get(issue_id, [])
    proc = run_br(repo_root, ["comments", "list", issue_id, "--json"])
    return _br_comment_rows(proc.stdout, issue_id)


def try_read_comments(repo_root: Path, issue_id: str) -> list[dict]:
    """:func:`read_comments` for an evidence reader; ``[]`` when the store cannot answer.

    The soft contract, and it is soft on purpose only where an empty answer is honest:
    its callers deduplicate a dispatch or a spend rollup, so "no markers recorded" and
    "the tracker did not answer" both correctly mean *write one now*. A counter or a
    refusal must not read this — it must use :func:`read_comments` and fail loudly.
    """
    if tracker_mode(repo_root) != MODE_OWNED:
        proc = try_run_br(repo_root, ["comments", "list", issue_id, "--json"])
        if proc is None or proc.returncode != 0:
            return []
        try:
            return _br_comment_rows(proc.stdout, issue_id)
        except RuntimeError:
            return []
    try:
        return _owned_comment_rows(repo_root).get(issue_id, [])
    except TrackerDivergenceError, OSError, ValueError:
        return []


def all_comment_texts(repo_root: Path) -> dict[str, list[str]]:
    """Every record's comment bodies, keyed by record id — the whole-tracker marker read.

    The travelling read (D11): what a fresh clone can answer with no tracker binary and
    no local state, because both stores commit their own artifact — br the JSONL export,
    the owned ledger its event logs. That is what makes a teammate's dispatch history and
    the cost-per-landed-package rollup readable at all.

    In :data:`MODE_OWNED` it folds the ledger; otherwise it reads the committed export, in
    file order. Best-effort in both directions, matching :func:`export_records`: every
    consumer here is evidence or telemetry, never a gate.
    """
    if tracker_mode(repo_root) == MODE_OWNED:
        try:
            rows = _owned_comment_rows(repo_root)
        except TrackerDivergenceError, OSError, ValueError:
            return {}
        return {
            record: [str(row[COMMENT_TEXT_KEY]) for row in found] for record, found in rows.items()
        }
    texts: dict[str, list[str]] = {}
    for record in export_records(repo_root):
        found = export_comment_texts(record)
        if found:
            texts[str(record["id"])] = found
    return texts


# --- Export scrubbing (basicly-vkh0.5) --------------------------------------

# br stamps every record it writes with the absolute canonical path of the
# workspace that produced it. The export is committed and this repo is
# distributed, so the field publishes each contributor's home directory layout
# to every consumer clone — a breach of the hard constraint that no
# machine-specific path or username is ever committed, and a portability defect
# in its own right (a path that means something on one machine is a wrong answer
# on another). br documents the field as optional: "older databases and
# hand-edited JSONL records without this field are valid" (`br schema issue`),
# and nothing in the harness reads it.
#
# Stripping it here rather than asking br not to emit it is deliberate: br has no
# config knob for the field, and an upstream defect is requirements input for our
# own replacement, never something we patch outside this repo. The requirement is
# recorded for the replacement in docs/design/work-tracker.md — a record is
# path-free, and provenance is the repo identity rather than a filesystem
# location.
MACHINE_PATH_FIELD = "source_repo_path"

# A path can also reach the export as ordinary text — pasted into a description
# or a comment. That half cannot be fixed by asking br to drop a field, and it
# cannot be edited away either: `br comments` offers only `add` and `list`, so a
# path already recorded in a comment has no removal path through the documented
# CLI (another requirement carried to basicly-vkh0.6 — the replacement owes a
# redaction path for recorded text).
#
# The export is therefore the layer where this is fixed. The local database is
# git-ignored and keeps full fidelity; only the published artifact is redacted,
# so nobody working in the repo loses the original.


# How long a publish waits for a reader to let go of the export before giving up.
# Only Windows ever spends this: `os.replace` needs delete access to the
# destination, and CPython opens a file for reading with `FILE_SHARE_READ |
# FILE_SHARE_WRITE` and *not* `FILE_SHARE_DELETE` — so renaming over a file another
# process is mid-read raises ERROR_SHARING_VIOLATION there while succeeding silently
# on POSIX. Under the fan-out this requirement is about, some reader is nearly always
# mid-read, which would have made the atomic write a Windows-only failure. Bounded on
# a monotonic clock for the reason R1 gives: the wall clock is not trustworthy here.
_PUBLISH_DEADLINE_S = 5.0
_PUBLISH_FIRST_WAIT_S = 0.005
_PUBLISH_MAX_WAIT_S = 0.1
# The reader's half of the same Windows sharing window, and shorter than the writer's
# on purpose: a denied read has a correct answer waiting microseconds away, so a long
# budget here would only delay a genuinely unreadable file on the telemetry read path.
_READ_DEADLINE_S = 1.0


def _publish(tmp: Path, export: Path) -> bool:
    """Rename *tmp* over *export*; False when a reader never let go in time.

    False rather than an exception because :func:`scrub_export` runs on the commit
    path and must never be the reason tracker state fails to land. An unpublished
    scrub leaves the export exactly as it was — still carrying whatever the scrub
    would have removed — and the companion ``tracker-path-scan`` hook is the gate
    that then refuses the commit. Failing to repair is safe; a half-written export
    is not.
    """
    deadline = time.monotonic() + _PUBLISH_DEADLINE_S
    delay = _PUBLISH_FIRST_WAIT_S
    while True:
        try:
            tmp.replace(export)
        except OSError:
            if time.monotonic() >= deadline:
                tmp.unlink(missing_ok=True)
                return False
            time.sleep(delay)
            delay = min(delay * 2, _PUBLISH_MAX_WAIT_S)
        else:
            return True


def _dump_record(record: dict[str, object]) -> str:
    """Serialize *record* the way br writes the export.

    Compact separators with UTF-8 left unescaped: every untouched record
    round-trips byte-identically under these, so a scrub's diff is exactly the
    fields it changed and nothing else.
    """
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def dependency_edge(dep: object) -> tuple[str, str] | None:
    """One dependency row as ``(dep_id, dep_type)``, or None when it is not a row.

    **br spells a dependency two ways for the same edge**: ``br show --json``
    emits ``id``/``dependency_type`` while the ``create``/``dep add`` echo emits
    ``depends_on_id``/``type``. Reading only one spelling silently yields *no
    dependencies at all* rather than an error, which is how it degraded every
    landing order to the caller's (basicly-kjc5.10).

    One reader for both spellings, so a new call site cannot re-acquire the bug by
    picking a spelling. Carried as a requirement on the replacement, which must
    emit exactly one spelling (`docs/design/work-tracker.md` R2, basicly-vkh0.6).
    """
    if not isinstance(dep, dict):
        return None
    dep_id = dep.get("depends_on_id") or dep.get("id")
    dep_type = dep.get("dependency_type") or dep.get("type")
    if not isinstance(dep_id, str) or not dep_id:
        return None
    return dep_id, dep_type if isinstance(dep_type, str) else ""


def read_record(repo_root: Path, issue_id: str) -> dict | None:
    """*issue_id*'s ``br show`` record, or None when the tracker has no usable one.

    The one read seam. `br.py` was already the only place that *spawns* br, but not the
    only place that *reads* it: the unwrap below was written out at eleven call sites
    across eight modules, and they disagreed about failure four ways — two raised, two
    returned None, four returned a local empty, one carried a typed absence — plus one
    (`loop._child_states`) that guarded the shape not at all and would raise
    ``AttributeError`` on a payload that was not an object (basicly-tcmy.14).

    **One contract, and it is deliberately the soft one:** None for every way the read
    can come back without a record — br absent from PATH, a spawn that fails, a
    non-zero exit, output that is not JSON, an empty array, or a payload that is not an
    object. It never raises for absence. A caller that must have the record calls
    :func:`require_record`, so the hard contract is one wrapper over this rather than a
    second reader.

    Swallowing the spawn's own errors here costs nothing that the split does not give
    back: every caller that needs an exception gets one from
    :func:`require_record`, with a message that names the id rather than the layer that
    failed. `decompose._read_bead` already caught exactly this set to produce a typed
    absence, which is the behaviour being generalised rather than invented.

    Why one seam matters more than the duplication: `basicly-vkh0.19` replaces br with
    an in-process log, and the replacement **chooses** what "not found" looks like. An
    empty list is the natural in-process answer, and against the old eleven that choice
    split six sites (``IndexError``) from five (their documented absence) — a behaviour
    change introduced by the change that is supposed to be transparent. With one reader
    the choice is made once, here.

    br spells a single record two ways — a bare object, or a one-element array — so the
    unwrap is part of the contract rather than a caller's concern, the same reason
    :func:`dependency_edge` reads both spellings of an edge.

    **The flip lands here and nowhere else** (`basicly-vkh0.19`). In
    :data:`MODE_OWNED` the record comes from :func:`owned_record` instead of from a
    spawn, and the contract above is unchanged — the same six absences answer None,
    and :func:`require_record` still raises one message naming the bead. That the
    choice of what "not found" means was already made once, here, is the whole reason
    the flip is not eleven decisions.

    What is deliberately *not* flipped: the other subcommands the engine spawns.
    `gate list`, `blocked`, `list`, `lint` and `dep cycles` are each read at their own
    call site with their own payload shape — they are not behind a seam, so flipping them
    would mean rewriting callers, which is the thing this bead is required not to do.
    That is why the external tracker is still written in :data:`MODE_OWNED` rather than
    merely tolerated. `scheduler` was on that list until basicly-vkh0.20 gave it a seam of
    its own (:func:`read_ranking`), and `comments` until basicly-s5li gave it
    :func:`read_comments`/:func:`add_comment` — which is the shape the rest would each
    need. Two `comments list` spawns remain outside that seam (`decompose`'s sizing
    markers, `supervise`'s found-info records) and are basicly-wpc8's.
    """
    if tracker_mode(repo_root) == MODE_OWNED:
        return owned_record(repo_root, issue_id)
    try:
        proc = try_run_br(repo_root, ["show", issue_id, "--json"])
    except RuntimeError, OSError:
        return None
    if proc is None or proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        return None
    record = data[0] if isinstance(data, list) and data else data
    return record if isinstance(record, dict) else None


def require_record(repo_root: Path, issue_id: str) -> dict:
    """*issue_id*'s ``br show`` record, raising when the tracker has no usable one.

    The hard half of :func:`read_record`'s contract, for a caller whose work cannot
    proceed without the record. One message for every absence, so a caller no longer has
    to know whether it is looking at a missing bead, a missing binary or a malformed
    payload to explain what went wrong.

    Raises:
        RuntimeError: :func:`read_record` found no usable record.
    """
    record = read_record(repo_root, issue_id)
    if record is None:
        raise RuntimeError(f"br show {issue_id} returned no issue record")
    return record


def owned_ranking(repo_root: Path, limit: int | None = None) -> dict:
    """The owned scheduler's answer for *repo_root*, in ``br scheduler --json``'s shape.

    The flipped half of :func:`read_ranking` (basicly-vkh0.20). Rendered into br's payload
    shape for the same reason :func:`owned_record` is rendered into ``br show``'s: the
    caller then has one parser rather than one per store, so the flip is a change of source
    and not of contract.

    Two fields of that shape mean something different on this side, and both are stated
    rather than papered over. ``fallback_rank`` equals the rank, because the owned ordering
    has no evidence-weighted pass above it that a fallback could differ from — br's two
    diverge exactly when its scoring evidence moved a node, and here the score *is* the
    ordering. And ``schema`` reads ``basicly.scheduler.v1`` rather than ``br.scheduler.v1``,
    which is what lets a marker recorded before the flip be told from one recorded after it.

    Unlike :func:`owned_record` this **raises** rather than degrading to an empty answer. An
    absent record is an ordinary fact a caller handles; an empty ranking is
    indistinguishable from "no work is ready", so a kit that would not load would stall the
    loop silently instead of failing.

    Raises:
        TrackerDivergenceError: the kit is not installed or will not load.
    """
    scheduler = kit(repo_root, SCHEDULER_KIT_MODULE)
    answer = scheduler.ranking(ledger_dir(repo_root), limit=limit)
    return {
        "schema": answer.schema,
        "fallback_policy": {"sort": answer.sort},
        "recommendations": [
            {
                "rank": entry.rank,
                "fallback_rank": entry.rank,
                "score": entry.score,
                "issue": {"id": entry.record, "title": entry.title},
            }
            for entry in answer.records
        ],
    }


def read_ranking(repo_root: Path, limit: int | None = None) -> dict:
    """The ranked ready set for *repo_root*, as the scheduler payload its caller parses.

    The ranking read's one seam, and the second thing the cutover flips
    (basicly-vkh0.20). It exists for the reason ``tests/test_br_seam.py`` guards: a caller
    that branched on :func:`tracker_mode` itself, or reached into the ledger, would scatter
    the cutover across the modules `basicly-tcmy.14` spent its whole budget collapsing.

    The payload is br's own — a ``schema``, a ``fallback_policy`` and a list of
    ``recommendations`` — from whichever store answers, so `basicly.loop_state` parses one
    shape. In :data:`MODE_OWNED` that shape is rendered by :func:`owned_ranking`; otherwise
    it is br's, parsed here and not at the caller.

    Raises:
        RuntimeError: br could not be run, or its reply was not a JSON object. Unchanged in
            direction from when this spawn lived at the call site: an unrankable ready set
            is a stop, never an empty list, because an empty list reads as "nothing to do"
            and the loop would idle instead of reporting.
    """
    if tracker_mode(repo_root) == MODE_OWNED:
        return owned_ranking(repo_root, limit)
    args = ["scheduler", "--json"]
    if limit is not None:
        args += ["--limit", str(limit)]
    proc = run_br(repo_root, args)
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"br scheduler returned no usable JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"br scheduler returned {type(payload).__name__}, not an object")
    return payload


# --- The shadow differential's reference side (basicly-vkh0.18) --------------
#
# Step 2 of the cutover in `docs/design/work-tracker.md` §5, and the step the two
# rungs above it are licensed by. The kit's `differential` module owns the
# comparison and the audit; what it cannot own is the reference side, because
# reading the live tracker means spawning br and the kit may not (§4). This is
# that side — a `views` callable, on the engine's side of the seam.
#
# **It reads the live tracker and never the export**, which is §5.1's rule rather
# than a preference: `import` upstream is upsert-only and cannot express a
# deletion, so comparing two derivatives of one lossy snapshot agrees with itself
# and proves nothing. The kit refuses a source that declares a snapshot and
# perturbs the ledger to catch one that secretly derives from it; the point of
# what follows is to be a source neither check can catch, by actually being live.

# Ids per `br show` spawn. Not for speed — the whole population in a single spawn
# is measured at 0.91s for 639 records (§5's step 2) — but for portability: a
# Windows command line is capped at 32767 characters, and a tracker large enough
# to cross it would fail the read outright rather than answer for fewer records.
LIVE_SHOW_BATCH = 100

# What the refusal names if a surface this read spawns is ever reclassified as a
# write. All three (`list`, `show`, `gate list`) are classified `read` today, so
# the guard cannot fire now — which is the reason to install it now: a shadow run
# that mutated the store it is auditing would be reporting on its own writes.
_SHADOW_READ_ONLY = "the shadow differential"


def _live_ids(repo_root: Path) -> list[str]:
    """Every record id the live tracker holds, closed and deferred ones included.

    ``-a`` and ``--limit 0`` are both load-bearing. `br list` reports open records
    only and caps its result set, and on this repo's tracker that is 100 records of
    644 (measured 2026-08-07): a reference that inherited the default would leave 544
    reported as unanswered — or, worse, look clean on the subset it was handed. That
    is `basicly-vkh0`'s own recorded lesson about a filter hiding a population.
    """
    proc = run_br(repo_root, ["list", "-a", "--json", "--limit", "0"])
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"br list returned no usable JSON: {exc}") from exc
    issues = payload.get("issues") if isinstance(payload, dict) else payload
    if not isinstance(issues, list):
        raise RuntimeError("br list returned no issue array to read the population from")
    return [
        record["id"]
        for record in issues
        if isinstance(record, dict) and isinstance(record.get("id"), str)
    ]


def _live_records(repo_root: Path, ids: Sequence[str]) -> list[dict]:
    """``br show`` for every id in *ids*, in :data:`LIVE_SHOW_BATCH`-sized spawns.

    Not routed through :func:`read_record`: that seam answers for one id and, in
    :data:`MODE_OWNED`, answers out of the owned ledger — which is the one store this
    side may not read. The reference has to be the external tracker whatever rung the
    repo is on, so it spawns br directly.
    """
    found: list[dict] = []
    for start in range(0, len(ids), LIVE_SHOW_BATCH):
        batch = list(ids[start : start + LIVE_SHOW_BATCH])
        proc = run_br(repo_root, ["show", *batch, "--json"])
        try:
            payload = json.loads(proc.stdout)
        except ValueError as exc:
            raise RuntimeError(f"br show returned no usable JSON: {exc}") from exc
        records = payload if isinstance(payload, list) else [payload]
        found += [record for record in records if isinstance(record, dict)]
    return found


def _live_gate_rows(repo_root: Path, issue_id: str, kit_module: Any) -> list[Any]:
    """One record's recorded gate results, as ``br gate list --robot`` reports them.

    One spawn per record, because br answers this query for one id at a time — and it
    is the query that makes the whole read worth its cost. A `br gate report` row is
    visible here and **absent from the JSONL export** (measured 2026-08-06, and the kit
    carries the measurement as ``EXPORT_CANNOT_EXPRESS``), so a snapshot-backed
    reference is silent on exactly the third of §5's three queries where the live
    tracker is the only witness.

    `policy.gate_status` parses the same three fields for the engine's own reading.
    What is deliberately *not* duplicated is the classification — which gates are
    required, whose provider counts, what disagreement means — which the kit's
    `gate_verdict` runs once over both sides of the comparison.
    """
    proc = run_br(repo_root, ["gate", "list", issue_id, "--robot"])
    try:
        payload = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"br gate list {issue_id} returned no usable JSON: {exc}") from exc
    results = payload.get("results") if isinstance(payload, dict) else None
    return [
        kit_module.GateRow(
            str(row.get("gate", "")), str(row.get("provider", "")), bool(row.get("passed"))
        )
        for row in (results if isinstance(results, list) else [])
        if isinstance(row, dict)
    ]


def _live_views(repo_root: Path) -> dict[str, Any]:
    """The live tracker's whole population as the kit's ``RecordView`` per record.

    Narrow on purpose, and the kit's class rather than a shape of our own: everything
    br holds that no query reads — titles, descriptions, timestamps,
    :data:`MACHINE_PATH_FIELD` — is left out here, so an incidental difference between
    the two stores cannot be reported as a disagreement about a verdict.

    The whole read runs inside :func:`read_only`. Shadow mode is defined as the owned
    tracker answering *read-only*, and a run that wrote to the store it is auditing
    would be reporting on its own writes.
    """
    kit_module = kit(repo_root)
    with read_only(_SHADOW_READ_ONLY):
        ids = _live_ids(repo_root)
        views: dict[str, Any] = {}
        for record in _live_records(repo_root, ids):
            issue = record.get("id")
            if not isinstance(issue, str) or not issue:
                continue
            edges = [dependency_edge(dep) for dep in record.get("dependencies") or []]
            views[issue] = kit_module.RecordView(
                record=issue,
                status=str(record.get("status") or ""),
                external_ref=str(record.get("external_ref") or ""),
                comments=tuple(export_comment_texts(record)),
                dependencies=tuple(
                    kit_module.Edge(target=edge[0], type=edge[1]) for edge in edges if edge
                ),
                gates=tuple(_live_gate_rows(repo_root, issue, kit_module)),
            )
    return views


def _live_reference(repo_root: Path) -> Any:
    """A ``ReferenceSource`` that answers out of the live tracker, and nothing else.

    ``snapshot`` is left at None because no snapshot was read: the digest check has
    nothing to match and the export refusal has nothing to fire on, which is what a
    genuinely live source looks like to the audit.
    """
    kit_module = kit(repo_root)

    def views(_ledger_events: object) -> dict[str, Any]:
        # The argument is ignored, and that is the contract rather than an oversight.
        # `audit_reference` calls this a second time with one synthetic event appended
        # to the owned ledger, and a source whose answers move with it is a derivative
        # and is refused. Nothing is cached between the two calls either: a memoised
        # answer would clear the probe by being the *same* answer rather than by being
        # an independent one, which is the one hole the kit says its audit cannot
        # close. The cost is one extra live read per run, and it is the price of the
        # probe meaning anything.
        return _live_views(repo_root)

    return kit_module.ReferenceSource(views=views)


def shadow_differential(repo_root: Path, vocabulary: Mapping[str, Any] | None = None) -> Any:
    """Run §5's step 2 for *repo_root*: the owned ledger against the live tracker.

    Returns the kit's ``DifferentialReport``, whose ``clean`` and ``conclusive`` are
    separate questions and have to be read as two: a comparison over a population where
    every record gives one query the same answer has discriminated nothing.

    Measured on this repo, 2026-08-07 — and it corrects the kit's own docstring, which
    predicted the gate query would be the constant one here. It is not: the live tracker
    carries a passing ``verify`` row on 331 of 643 compared records, so the query
    discriminates and the run is **conclusive**. What it is not is clean, on those same
    331 — no export carries a gate field, so `migrate.py` had nothing to import and the
    owned side reads ``missing`` against br's ``passed``. Only the dual write can close
    that, which is the direction §5 already gives; the finding is that step 2 reports it
    as a disagreement rather than as an absence of evidence.

    *vocabulary* overrides the kit's ``Vocabulary`` defaults field by field, and it is a
    plain mapping rather than the kit's own class so the caller needs nothing out of the
    kit: `basicly.cli` supplies the engine's *configured* names — it is the layer allowed
    to read `basicly.config`, which this module is not (see :func:`set_mode_reader`) —
    while every module outside this one stays clear of the owned store. An unknown key
    raises rather than being ignored, because a name the kit does not read is a caller
    believing it configured something.

    Raises:
        TrackerDivergenceError: the kit is not installed or will not load.
        RuntimeError: br is absent, a read failed, or its reply was not usable JSON. A
            hard failure and never a partial reference: a comparison run against the
            records that happened to answer is the shape that reports clean by saying
            less.
    """
    kit_module = kit(repo_root)
    names = kit_module.Vocabulary(**dict(vocabulary or {}))
    return kit_module.run_differential(ledger_dir(repo_root), _live_reference(repo_root), names)


def _redact_paths(value: object) -> object:
    """Recursively redact machine-specific paths in every string inside *value*.

    Comments and dependency rows are nested under the record, so a leak is not
    confined to a top-level field.
    """
    if isinstance(value, str):
        return redact.redact_machine_paths(value)
    if isinstance(value, dict):
        return {key: _redact_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_paths(item) for item in value]
    return value


def scrub_export(repo_root: Path) -> int:
    """Strip machine-specific paths from the tracker export; return records changed.

    Two shapes, one pass: br's :data:`MACHINE_PATH_FIELD` is removed outright, and
    any path left in text is redacted to a placeholder.

    A no-op (returning 0, leaving the file untouched) when the export is absent or
    already clean, and a line that will not parse is passed through verbatim —
    this runs on the commit path, so it must never be the reason tracker state
    fails to land. The companion ``tracker-path-scan`` hook is the gate that makes
    a leak visible; this is only the repair.

    The write is atomic (R7, basicly-vkh0.10). This is a writer to the *shared*
    tracker artifact: it runs on the commit path while up to five lane worktrees
    read the same file through ``.beads/redirect``. A plain ``write_text``
    truncates before it writes, so a reader landing in that window gets a short
    file — and :func:`export_records` skips a line it cannot parse rather than
    raising, so the torn read comes back as a *partial issue set with no error at
    all*. That is our own store reproducing the defect this requirement was filed
    for; write-then-rename is the portable answer the design already names
    (work-tracker §9.3), and it publishes the new content in one step so a
    concurrent reader sees either the whole old export or the whole new one.

    Not :func:`projection.atomic_write_text`, for two reasons that are both about
    this file specifically. Its temp name is a fixed suffix on the destination, so
    two writers racing on one export would share one temp path and each could
    publish the other's half-written bytes — the very concurrency this is fixing.
    And the name it picks is not covered by ``.beads/.gitignore``, so a crash
    mid-write would strand untracked dirt in the one directory the harness's
    landing check treats as special. The pid-scoped ``.tmp`` name below is the
    pattern :mod:`basicly.run_record` and :mod:`basicly.policy` already use, and
    ``.beads/.gitignore`` already ignores ``*.tmp``.

    Returns 0 — an unrepaired export, left exactly as it was — when the rename could
    not be published; see :func:`_publish` for the Windows sharing rule that makes
    that reachable.
    """
    export = repo_root / ".beads" / "issues.jsonl"
    try:
        raw = export.read_text(encoding="utf-8")
    except OSError:
        return 0
    scrubbed: list[str] = []
    changed = 0
    for line in raw.splitlines():
        if not line.strip():
            scrubbed.append(line)
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            scrubbed.append(line)
            continue
        if not isinstance(record, dict):
            scrubbed.append(line)
            continue
        record.pop(MACHINE_PATH_FIELD, None)
        rendered = _dump_record({key: _redact_paths(item) for key, item in record.items()})
        scrubbed.append(rendered)
        if rendered != line:
            changed += 1
    if not changed:
        return 0
    trailer = "\n" if raw.endswith("\n") else ""
    tmp = export.with_suffix(f".{os.getpid()}.jsonl.tmp")
    tmp.write_text("\n".join(scrubbed) + trailer, encoding="utf-8")
    return changed if _publish(tmp, export) else 0


# --- Reading the committed export (basicly-kjc5.50) --------------------------


def beads_dir(repo_root: Path) -> Path:
    """The active beads directory, following br's git-ignored ``redirect`` file.

    A harness worktree shares the base checkout's tracker through ``redirect``,
    so the redirected directory — not the worktree's own checked-out copy — is
    where br flushes and where the freshest export lives.
    """
    beads = Path(repo_root) / ".beads"
    redirect = beads / "redirect"
    if redirect.is_file():
        try:
            target = Path(redirect.read_text(encoding="utf-8").strip())
        except OSError:
            return beads
        if target.is_dir():
            return target
    return beads


def export_records(repo_root: Path) -> list[dict]:
    """Every issue record in the committed JSONL export, in file order.

    The export is the *shared* tracker: git is its transport, so it is what a
    fresh clone has and the one source that answers for beads this machine never
    ran (D11). It carries each record's comments, which makes the harness marker
    families readable without a single br invocation — the bulk read `br list`
    cannot serve (it caps results and drops closed records).

    Empty when there is no export; an unparsable line is skipped rather than fatal,
    matching :func:`scrub_export` — every consumer here is evidence or telemetry,
    never a gate.

    **A file that exists but cannot be opened right now is retried, not reported as
    empty** (basicly-vkh0.10). Absence and denial are different facts and returning
    `[]` for both is the defect this whole requirement is about: a caller cannot tell
    "this repo has no export" from "some other process held it for a millisecond", so
    a transient denial read as a real answer means zero beads exist. Only Windows
    reaches this — `_publish`'s rename is atomic, so the destination never vanishes,
    but CPython opens for reading without `FILE_SHARE_DELETE`, so a reader that
    collides with a publish gets ERROR_SHARING_VIOLATION where POSIX succeeds. CI
    caught it as a reader observing 0 of 3000 records while the atomic write was
    working exactly as designed. Bounded on a monotonic clock, and still returning
    `[]` at the deadline rather than raising, because this function is on the read
    path of telemetry that must never be the reason a landing fails.
    """
    export = beads_dir(repo_root) / "issues.jsonl"
    deadline = time.monotonic() + _READ_DEADLINE_S
    delay = _PUBLISH_FIRST_WAIT_S
    while True:
        try:
            raw = export.read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        except OSError:
            if time.monotonic() >= deadline:
                return []
            time.sleep(delay)
            delay = min(delay * 2, _PUBLISH_MAX_WAIT_S)
        else:
            break
    records: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records.append(record)
    return records


def export_comment_texts(record: Mapping[str, object]) -> list[str]:
    """The comment bodies on one exported record, in export order."""
    comments = record.get("comments")
    if not isinstance(comments, list):
        return []
    return [
        str(comment["text"])
        for comment in comments
        if isinstance(comment, Mapping) and isinstance(comment.get("text"), str)
    ]
