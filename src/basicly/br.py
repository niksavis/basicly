"""The single seam to the ``br`` (beads) tracker CLI.

Every harness module used to carry its own private ``_run_br`` copy plus a
``shutil.which("br")`` probe — eight call sites to audit whenever br's CLI
or JSON output changes. This module is now the only place that spawns br:
one invocation contract, one absence message, and a one-time version probe
that warns when the installed br is older than the floor the harness was
built against.
"""

from __future__ import annotations

import contextlib
import contextvars
import importlib.util
import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

from basicly import redact, tracker_usage

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
        proc = subprocess.run(  # nosec B603
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
        proc = subprocess.run(  # nosec B603
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
# Steps 3 and 4 of the cutover in `docs/design/work-tracker.md` §5. The kit under
# `.basicly/core/kit/tracker/` is the owned store; everything below is the engine
# side of the seam that writes to it and, once flipped, reads from it.
#
# **Why all of it fits in this module.** `basicly-tcmy.14` collapsed eleven
# hand-written unwraps of `br show --json` into :func:`read_record`, and every br
# invocation already goes through :func:`run_br`/:func:`try_run_br`. Those two facts
# are the whole reason the flip is a change to one file rather than to eight: the
# engine's *write* surface is one funnel and its *record read* surface is one
# function, so mirroring and flipping are both edits to a seam rather than to
# callers.

MODE_EXTERNAL = "external"
MODE_DUAL = "dual"
MODE_OWNED = "owned"

# The cutover ladder, in the order §5 walks it. `external` is today's behaviour;
# `dual` writes both stores with br still authoritative for reads; `owned` is the
# flip — reads come from the ledger and br is *still written*, because the other ten
# subcommands the engine spawns still answer out of it (see :func:`read_record`).
TRACKER_MODES = (MODE_EXTERNAL, MODE_DUAL, MODE_OWNED)
DEFAULT_TRACKER_MODE = MODE_EXTERNAL

# The kit's work-tracker store, relative to the repo that installed it.
KIT_TRACKER_DIR = Path(".basicly") / "core" / "kit" / "tracker"

# The ledger directory, taken off the usage ledger's own path rather than spelled a
# second time: both artifacts live in `.basicly/ledger/`, and
# `.scripts/kit_deployment.py` gates that directory's ignore rules against the same
# location. A literal here could drift from either without a gate noticing.
LEDGER_DIR = tracker_usage.LEDGER_FILE.parent

# The prefix a kit module is loaded under. Fixed, and checked against `sys.modules`
# before loading, for the reason `differential._load_migrate` gives: two loads of one
# file give two `Event` classes and an `isinstance` against the wrong one is false for
# the right reason. The kit's own sibling loaders follow the same convention
# (`basicly_tracker_kit_migrate`, `..._ids`, `..._differential`), so a module the kit
# loads for itself and one the engine loads here are the same object.
_KIT_MODULE_PREFIX = "basicly_tracker_kit_"

# The kit module :func:`kit` answers with when a caller names none — the differential,
# which carries `events` and `migrate` under it.
DEFAULT_KIT_MODULE = "differential"

# The kit module that owns the ranking (basicly-vkh0.20).
SCHEDULER_KIT_MODULE = "scheduler"

# How a mirrored fact says it got here (§9.6). Distinguishes an event the dual write
# recorded from one `migrate.py` extracted out of the export, and it is one of
# `migrate.RESERVED_KEYS`, so it is dropped again when a record is rendered back.
MIRROR_PROVENANCE = "dual-write"

# br's writes that carry no record fact, so there is nothing to mirror. Named rather
# than defaulted to "skip", because the default for an unrecognised write is a
# refusal — see :func:`_mirror_drafts`.
#
# `sync` moves the whole store between its database and its export and `init` creates
# the store; neither states anything about a record. The owned ledger needs no
# equivalent of either: it *is* the export (git is its transport, §4) and
# `events.append` creates its directory on first write.
_UNMIRRORED_WRITES = frozenset({"init", "sync"})

# `br update`'s flags, as the ledger fact each one records. Two mappings rather than
# one because `status` has its own event kind while everything else is a `field`.
#
# **Deliberately only what the engine spawns** (`br update -t` in `classify`,
# `br update --external-ref` in `loop`), plus the status flag. A flag absent here is
# not dropped — it raises :class:`TrackerDivergenceError`, because a mirrored write that
# silently omitted half of what br recorded is exactly the divergence this mode
# exists to prevent, and it would be invisible until the differential ran.
_UPDATE_FIELD_FLAGS = {
    "-t": "issue_type",
    "--type": "issue_type",
    "--external-ref": "external_ref",
}
_UPDATE_STATUS_FLAGS = frozenset({"-s", "--status"})

# `br create`'s flags, as the fields the created record carries.
_CREATE_FIELD_FLAGS = {
    "-t": "issue_type",
    "--type": "issue_type",
    "-p": "priority",
    "--priority": "priority",
    "-l": "labels",
    "--label": "labels",
    "-d": "description",
    "--description": "description",
    "--parent": "parent",
}

# ...and the shape each one has to be stored in, because a flag's value arrives as one
# argv string while `br show --json` returns it typed. Not cosmetic: `supervise` reads
# ``record["labels"]`` as a list and a stored ``"phase-6,ready"`` iterates as characters,
# so a lane's follow-up would inherit twelve one-letter labels after the flip. Anything
# absent here is text on both sides.
_CREATE_FIELD_TYPES: dict[str, Callable[[str], object]] = {
    "priority": int,
    "labels": lambda value: [part for part in value.split(",") if part],
}

# Flags whose value is the following token, per subcommand. Needed to find the
# positional a write is about: `br gate report` puts the issue id *last*, after four
# or five flag/value pairs, so "the last argument" is only right by accident and
# "every token that is not a flag" would collect `--note`'s free text as one.
_VALUE_FLAGS: dict[str, frozenset[str]] = {
    "create": frozenset(_CREATE_FIELD_FLAGS) | {"-a", "--assignee"},
    "update": frozenset(_UPDATE_FIELD_FLAGS) | _UPDATE_STATUS_FLAGS,
    "close": frozenset({"--reason"}),
    "dep add": frozenset({"-t", "--type"}),
    "gate report": frozenset({"--gate", "--provider", "--status", "--note", "--actor"}),
}

# `br gate report --status` spells a pass this way; anything else is a failure, which
# is `policy.GateStatus`'s own reading of the same field.
_GATE_PASS_STATUS = "pass"

# The status br gives a record it just created, when the `--json` echo does not say.
_CREATED_STATUS = "open"


class TrackerDivergenceError(RuntimeError):
    """The owned ledger did not record a write the external tracker accepted.

    A hard failure on the write path, never a warning (`basicly-vkh0.19`'s first
    acceptance criterion). The two stores are only worth running side by side while
    they hold the same facts: a mirrored write that failed and said so in a log line
    leaves the ledger quietly short of one event, and the *next* thing to notice is
    the shadow differential — after however many more writes landed on top.

    A subclass of ``RuntimeError`` so a caller that already handles a br failure
    handles this one, and so the message is what `run_br` callers already print.
    """


# One-slot holder for the mode reader. A list rather than a rebound module global:
# `global` is the shape a reader has to chase, and this dependency is inverted
# already (see :func:`set_mode_reader`), so it should be obvious rather than terse.
_mode_reader: list[Callable[[Path], str]] = []

# Kit modules by (resolved tracker-directory path, module name).
_kit_modules: dict[tuple[str, str], ModuleType] = {}


def set_mode_reader(reader: Callable[[Path], str] | None) -> None:
    """Install the function that answers which tracker mode a repo declares.

    **The dependency is inverted, and an import cycle is why.** The declaration lives
    in ``[tracker] mode`` and only :mod:`basicly.config` may read it — it owns the
    three-layer merge over ``basicly.toml``, the gitignored overlay and the session
    overrides, and the strict schema that refuses a key this engine cannot honour.
    This module cannot import it: ``config`` imports ``runner``, ``runner`` imports
    ``run_record``, and ``run_record`` imports this module, so ``br -> config`` closes
    a genuine cycle rather than merely inverting a lint tier. So ``config`` reaches
    down and installs its reader here, which is the same direction every other engine
    module takes to this one.

    With no reader installed the mode is :data:`DEFAULT_TRACKER_MODE`, which is the
    behaviour this module had before the cutover existed — nothing is mirrored and
    nothing is flipped. Every process that reaches the tracker imports ``config``
    (``basicly.cli`` does, and it is the only entry point), and
    ``tests/test_br_seam.py`` asserts the installation rather than assuming it.

    Passing ``None`` uninstalls, which is what a test that wants the pre-cutover
    behaviour back should do.
    """
    _mode_reader.clear()
    if reader is not None:
        _mode_reader.append(reader)


def tracker_mode(repo_root: Path) -> str:
    """The cutover mode *repo_root* declares, or :data:`DEFAULT_TRACKER_MODE`."""
    if not _mode_reader:
        return DEFAULT_TRACKER_MODE
    return _mode_reader[0](Path(repo_root))


def ledger_dir(repo_root: Path) -> Path:
    """The owned ledger's directory for *repo_root*.

    **One ledger per repo, never one per worktree**, which is why this goes through
    :func:`tracker_usage.ledger_root` rather than joining onto *repo_root*. A loop
    worktree shares the base checkout's tracker through br's ``redirect`` file; a
    ledger that did not follow the same rule would take a lane's writes into the
    worktree's own copy and lose every one of them at teardown, which is exactly what
    happened to the usage spool (basicly-vkh0.8).
    """
    return tracker_usage.ledger_root(Path(repo_root)) / LEDGER_DIR


def kit(repo_root: Path, module_name: str = DEFAULT_KIT_MODULE) -> Any:
    """The installed kit's *module_name*; by default ``differential``.

    The differential rather than the event log directly, for the reason it loads
    ``migrate`` rather than ``events``: it is the module that owns every vocabulary
    the engine has to write in the store's own terms — the ``edge`` kind, the ``gate``
    kind and its payload keys — so reaching it through this one attribute chain
    (``kit(root).events``, ``kit(root).migrate``) keeps a second spelling of any of
    them impossible.

    A kit module that is not reachable that way is named instead — the scheduler
    (basicly-vkh0.20) is the first, because it sits *beside* the differential rather
    than under it. It loads its own sibling under the same fixed ``sys.modules`` name
    this function uses, which is what keeps one `RecordView` class in the process
    however the two are reached.

    Raises:
        TrackerDivergenceError: the module is not installed, or will not load. A hard
            failure rather than a degrade: a mode above ``external`` has already promised
            that both stores hold the same facts.
    """
    directory = Path(repo_root) / KIT_TRACKER_DIR
    source = directory / f"{module_name}.py"
    # Asked of the filesystem before either cache, and that ordering is the finding:
    # reusing an already-loaded kit is right (one `Event` class per process), but if the
    # reuse came first then a repo with no kit installed would be answered out of some
    # other repo's, and the mode would look enabled while writing nowhere.
    if not source.is_file():
        raise TrackerDivergenceError(f"the tracker kit is not installed at {directory}")
    key = (str(directory.resolve()), module_name)
    if (cached := _kit_modules.get(key)) is not None:
        return cached
    loaded_as = _KIT_MODULE_PREFIX + module_name
    module = sys.modules.get(loaded_as)
    if module is None:
        spec = importlib.util.spec_from_file_location(loaded_as, source)
        if spec is None or spec.loader is None:
            raise TrackerDivergenceError(f"the tracker kit is not installed at {directory}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[loaded_as] = module
        try:
            spec.loader.exec_module(module)
        except (OSError, ImportError) as exc:
            del sys.modules[loaded_as]
            raise TrackerDivergenceError(
                f"the tracker kit at {directory} did not load: {exc}"
            ) from exc
    _kit_modules[key] = module
    return module


def _positionals(args: Sequence[str], value_flags: Collection[str]) -> list[str]:
    """The positional words in *args*, with each value-taking flag's value consumed.

    ``--flag=value`` carries its own value, so only the space-separated form skips the
    next token. Anything after a flag this subcommand does not take a value for stays
    a positional, which is what makes an unexpected argument visible to the caller
    below rather than silently absorbed.
    """
    found: list[str] = []
    skip = False
    for arg in args:
        if skip:
            skip = False
            continue
        if arg.startswith("-"):
            skip = "=" not in arg and arg in value_flags
            continue
        found.append(arg)
    return found


def _flag_pairs(args: Sequence[str], value_flags: Collection[str]) -> list[tuple[str, str]]:
    """Each ``(flag, value)`` in *args*, in the order given, both spellings accepted."""
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(args):
        arg = args[index]
        if arg.startswith("-"):
            name, sep, inline = arg.partition("=")
            if sep:
                pairs.append((name, inline))
            elif name in value_flags and index + 1 < len(args):
                pairs.append((name, args[index + 1]))
                index += 1
            else:
                pairs.append((name, ""))
        index += 1
    return pairs


def _payload(kit_module: Any, **fields: object) -> dict[str, object]:
    """A mirrored event's payload, carrying how the fact got here."""
    payload: dict[str, object] = {kit_module.migrate.PROVENANCE_KEY: MIRROR_PROVENANCE}
    payload.update(fields)
    return payload


def _update_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br update``.

    Every flag has to be translatable. An unrecognised one raises rather than being
    dropped, because the alternative is a ledger that is missing precisely the field
    somebody just added a flag for.
    """
    events = kit_module.events
    positional = _positionals(args, _VALUE_FLAGS["update"])
    if len(positional) != 2:
        raise TrackerDivergenceError(f"br update names no single issue: {' '.join(args)}")
    record = positional[1]
    drafts: list[object] = []
    for flag, value in _flag_pairs(args, _VALUE_FLAGS["update"]):
        if flag in _UPDATE_STATUS_FLAGS:
            drafts.append(
                events.Draft(record, events.KIND_STATUS, _payload(kit_module, status=value))
            )
        elif (name := _UPDATE_FIELD_FLAGS.get(flag)) is not None:
            drafts.append(
                events.Draft(
                    record,
                    events.KIND_FIELD,
                    _payload(kit_module, name=name, value=value),
                )
            )
        else:
            raise TrackerDivergenceError(
                f"br update {flag} has no owned-ledger equivalent, so mirroring it would "
                f"drop the field br just wrote; add it to br._UPDATE_FIELD_FLAGS"
            )
    return drafts


def _create_drafts(kit_module: Any, args: Sequence[str], stdout: str) -> list[object]:
    """Drafts for one ``br create``, whose record id only the reply carries.

    ``--parent`` becomes a ``parent-child`` edge on the new record rather than a field:
    that is where both stores hold it (`differential.Edge`), and it is what makes the
    parent read as decomposed.
    """
    events = kit_module.events
    try:
        reply = json.loads(stdout)
    except ValueError as exc:
        raise TrackerDivergenceError(
            f"br create replied with no JSON record, so the id it minted cannot be mirrored: {exc}"
        ) from exc
    record = reply.get("id") if isinstance(reply, dict) else None
    if not isinstance(record, str) or not record:
        raise TrackerDivergenceError("br create replied with no issue id to mirror")
    positional = _positionals(args, _VALUE_FLAGS["create"])
    fields: dict[str, object] = {"title": positional[1]} if len(positional) > 1 else {}
    parent = ""
    for flag, value in _flag_pairs(args, _VALUE_FLAGS["create"]):
        name = _CREATE_FIELD_FLAGS.get(flag)
        if name == "parent":
            parent = value
        elif name is not None:
            fields[name] = _CREATE_FIELD_TYPES.get(name, str)(value)
    status = reply.get("status")
    drafts: list[object] = [
        events.Draft(record, events.KIND_CREATED, _payload(kit_module, **fields)),
        events.Draft(
            record,
            events.KIND_STATUS,
            _payload(
                kit_module,
                status=status if isinstance(status, str) and status else _CREATED_STATUS,
            ),
        ),
    ]
    if parent:
        # The kit's own name for the edge, not a fourth spelling of the string: this is
        # exactly the value `differential.children_of` inverts the population on, so a
        # literal here would make a mirrored parent invisible to the ready query.
        drafts.append(
            _edge_draft(kit_module, record, parent, kit_module.DEFAULT_VOCABULARY.parent_child_type)
        )
    return drafts


def _edge_draft(kit_module: Any, record: str, target: str, edge_type: str) -> object:
    """One dependency edge, recorded on the dependent — where both stores hold it."""
    migrate = kit_module.migrate
    payload = _payload(kit_module)
    payload[migrate.EDGE_FROM] = record
    payload[migrate.EDGE_TO] = target
    payload[migrate.EDGE_TYPE] = edge_type
    return kit_module.events.Draft(record, migrate.KIND_EDGE, payload)


def _gate_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br gate report``.

    **The writer `differential.KIND_GATE` was defined for.** The export carries no gate
    field at all, so `migrate.py` had nothing to import and the third of the three
    queries the shadow differential compares had no owned-side rows to compare — it
    reported ``inconclusive`` on every population. This is what fills it.
    """
    kind = kit_module.KIND_GATE
    positional = _positionals(args, _VALUE_FLAGS["gate report"])
    if len(positional) != 3:
        raise TrackerDivergenceError(f"br gate report names no single issue: {' '.join(args)}")
    values = dict(_flag_pairs(args, _VALUE_FLAGS["gate report"]))
    gate = values.get("--gate", "")
    provider = values.get("--provider", "")
    if not gate or not provider:
        raise TrackerDivergenceError(f"br gate report names no gate and provider: {' '.join(args)}")
    payload = _payload(kit_module)
    payload[kit_module.GATE_NAME_KEY] = gate
    payload[kit_module.GATE_PROVIDER_KEY] = provider
    payload[kit_module.GATE_PASSED_KEY] = values.get("--status", "") == _GATE_PASS_STATUS
    return [kit_module.events.Draft(positional[2], kind, payload)]


def _close_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br close``: the status move, and only that.

    The ``--reason`` is not mirrored as a comment. br records it as a field of the
    close rather than as a comment row, so writing one would put a comment on the
    owned side that the reference side does not hold — a difference invented by the
    mirror rather than found by it.
    """
    events = kit_module.events
    positional = _positionals(args, _VALUE_FLAGS["close"])
    if len(positional) != 2:
        raise TrackerDivergenceError(f"br close names no single issue: {' '.join(args)}")
    return [events.Draft(positional[1], events.KIND_STATUS, _payload(kit_module, status="closed"))]


def _comment_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br comments add`` — 45% of this repo's tracker traffic.

    Read by position rather than through :func:`_positionals`, and that is the whole
    point: the body is arbitrary free text, so a body beginning with ``-`` would be
    taken for a flag and silently dropped — losing exactly the checkpoint or rework
    marker the engine's whole policy layer is carried in.
    """
    events = kit_module.events
    if len(args) != 4:
        raise TrackerDivergenceError(
            f"br comments add takes one issue and one body; got {len(args)} arguments"
        )
    payload = _payload(kit_module, text=args[3])
    return [events.Draft(args[2], events.KIND_COMMENT, payload)]


def _dep_drafts(kit_module: Any, args: Sequence[str], _stdout: str) -> list[object]:
    """Drafts for one ``br dep add``, recorded on the dependent."""
    positional = _positionals(args, _VALUE_FLAGS["dep add"])
    if len(positional) != 4:
        raise TrackerDivergenceError(f"br dep add names no single edge: {' '.join(args)}")
    values = dict(_flag_pairs(args, _VALUE_FLAGS["dep add"]))
    edge_type = values.get("-t") or values.get("--type") or ""
    if not edge_type:
        raise TrackerDivergenceError(f"br dep add names no edge type: {' '.join(args)}")
    return [_edge_draft(kit_module, positional[2], positional[3], edge_type)]


# The record-write surface, as the translation each one takes. A dispatch table rather
# than a chain of comparisons so the mirrored set is *readable as a set* — it is the
# thing a reviewer has to check against the measured surface, and a branch buried in a
# function body is not.
_MIRRORED_WRITES: dict[str, Callable[[Any, Sequence[str], str], list[object]]] = {
    "close": _close_drafts,
    "comments add": _comment_drafts,
    "create": _create_drafts,
    "dep add": _dep_drafts,
    "gate report": _gate_drafts,
    "update": _update_drafts,
}


def _mirror_drafts(kit_module: Any, args: Sequence[str], stdout: str) -> list[object]:
    """The owned-ledger drafts recording the same fact *args* just wrote to br.

    Empty for a read and for the two writes that state nothing about a record. Every
    other write is translated, and one this function does not know **raises**: the
    surface was frozen by measurement (`basicly.tracker_usage`), so a write outside it
    is a new dependency on br that nobody decided to take, and the mirror is the only
    place that can still see it before the two stores drift.

    Raises:
        TrackerDivergenceError: *args* is a write with no owned-ledger translation.
    """
    surface, _ = tracker_usage.split_invocation(list(args))
    if tracker_usage.classify_access(surface) == "read" or surface in _UNMIRRORED_WRITES:
        return []
    translate = _MIRRORED_WRITES.get(surface)
    if translate is None:
        raise TrackerDivergenceError(
            f"br {surface} has no owned-ledger translation, so the dual write cannot keep "
            f"the two stores in step; add one to br._MIRRORED_WRITES, or list it in "
            f"br._UNMIRRORED_WRITES if it states nothing about a record"
        )
    return translate(kit_module, args, stdout)


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
    """
    if tracker_mode(repo_root) == MODE_EXTERNAL or proc.returncode != 0:
        return
    kit_module = kit(repo_root)
    try:
        drafts = _mirror_drafts(kit_module, args, proc.stdout or "")
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
    `comments list`, `gate list`, `blocked`, `list`, `lint` and `dep cycles` are each
    read at their own call site with their own payload shape — they are not behind a
    seam, so flipping them would mean rewriting callers, which is the thing this bead
    is required not to do. That is why the external tracker is still written in
    :data:`MODE_OWNED` rather than merely tolerated. `scheduler` was on that list until
    basicly-vkh0.20 gave it a seam of its own (:func:`read_ranking`), which is the shape
    the remaining nine would each need.
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
