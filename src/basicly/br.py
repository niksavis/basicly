"""The single seam to the ``br`` (beads) tracker CLI.

Every harness module used to carry its own private ``_run_br`` copy plus a
``shutil.which("br")`` probe — eight call sites to audit whenever br's CLI
or JSON output changes. This module is now the only place that spawns br:
one invocation contract, one absence message, and a one-time version probe
that warns when the installed br is older than the floor the harness was
built against.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable, Mapping
from pathlib import Path

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


def run_br(
    repo_root: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a br subcommand; raises when br is absent — the harness needs the tracker."""
    br_path = which()
    if not br_path:
        raise RuntimeError("br is not on PATH; the harness requires the beads tracker")
    _probe_version(br_path)
    proc = _spawn_tolerating_transient(br_path, repo_root, args)
    if check and proc.returncode != 0:
        raise RuntimeError(f"br {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def try_run_br(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a br subcommand; None when br is absent (soft call sites)."""
    br_path = which()
    if not br_path:
        return None
    _probe_version(br_path)
    return _spawn_tolerating_transient(br_path, repo_root, args)


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
