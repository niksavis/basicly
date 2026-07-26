"""Runner run-record: the per-dispatch outcome, keyed by bead id (basicly-z6dh).

The runner is the only place basicly holds who ran a node (agent), on what
model, for how long, and with what outcome; today the loop keeps only the exit
code and discards the rest (``runner.run`` -> ``loop._dispatch_runner``). This
module persists a structured, metadata-only record per dispatched run into the
self-ignored ``.basicly/usage/`` directory, using the same atomic
tmp-write-then-replace pattern as the ``tool-usage`` telemetry.

It is the correlation foundation the spike (basicly-zv48, Dimension 3) calls the
keystone: agent attribution (basicly-140a), model provenance (basicly-45ld), the
cross-repo fleet rollup (basicly-h0f0), and health/drift scoring (basicly-y886)
all consume this one record.

Redaction (coordinates with basicly-3p2i): only metadata is persisted — never
the prompt body and never the captured stdout/stderr. The command is stored with
the prompt argument elided (:data:`REDACTED_PROMPT`), so a run-record can never
carry a prompt or a secret embedded in one. Records accumulate as a list per
bead id, so a re-dispatched (reworked) node keeps its run history.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from . import br, session

USAGE_DIR = Path(".basicly/usage")
RUN_RECORDS_FILE = USAGE_DIR / "run-records.json"

# Substituted for the prompt argument in a persisted command, so a run-record is
# metadata-only and can never carry the prompt (or a secret embedded in it).
REDACTED_PROMPT = "<prompt-redacted>"

# Outcome labels for a dispatched run.
EXECUTED = "executed"  # ran to completion with exit 0
FAILED = "failed"  # ran to completion with a non-zero exit
HANDOFF = "handoff"  # no CLI invocation — handed to the driving agent/human


@dataclass(frozen=True)
class RunRecord:
    """One dispatched run's metadata, stored on disk under the bead it ran."""

    agent: str
    outcome: str
    returncode: int | None
    duration_s: float | None
    command: tuple[str, ...]  # redacted: the prompt argument is elided
    timestamp: str
    # Model provenance, the pinned model the dispatch ran (basicly-45ld); null
    # when the runner pins no model.
    model: str | None = None
    # Token telemetry (basicly-kjc5.1): total tokens and USD cost for the run,
    # from adapter-reported usage where the CLI emits it. estimated=True marks
    # a chars/4 transcript fallback (design 7.5) so calibration can down-weight
    # it; all three stay null for a handoff — nothing executed, nothing to meter.
    tokens: int | None = None
    cost: float | None = None
    estimated: bool | None = None
    # --- Reproducible dispatch inputs (D9, basicly-kjc5.28) ------------------
    # What the dispatch actually ran, so two attempts on one node are diffable:
    # when attempt 2 behaves differently, these say whether the *input* changed.
    # The prompt itself is never persisted — only its digest.
    adapter_version: str | None = None
    prompt_sha256: str | None = None
    phase: str | None = None
    # Sizing inputs frozen at dispatch, so a later calibration cannot silently
    # re-derive them against a changed tree (D8 drift, basicly-kjc5.30).
    scope_tokens: int | None = None
    forecast_tokens: int | None = None
    # Ids of the found-info records folded into the bundle. Bundle assembly
    # truncates to the newest N, so without this the prompt is unexplainable.
    folded_info: tuple[str, ...] = ()
    # Harness config this session overrode from the command line, as sorted
    # ``section.key=value`` strings (basicly-jr0l.8). A per-session ``--runner``
    # or ``--autonomy`` changes what a dispatch *is* without changing any
    # committed file, so leaving it unrecorded would put two genuinely different
    # dispatches behind identical records — the irreproducibility D9 forbids.
    # Empty for a run configured entirely by committed config.
    config_overrides: tuple[str, ...] = ()


def outcome_of(*, handoff: bool, returncode: int | None) -> str:
    """Label a dispatch: handoff, or executed/failed by its exit code."""
    if handoff:
        return HANDOFF
    return EXECUTED if returncode == 0 else FAILED


# Intrinsic record fields, one parameter each: the raw RunResult can't be passed
# in as one arg because it carries the un-redacted command, which must never
# enter this module.
def build_record(  # noqa: PLR0913
    *,
    agent: str,
    handoff: bool,
    returncode: int | None,
    duration_s: float | None,
    command: tuple[str, ...],
    model: str | None = None,
    tokens: int | None = None,
    cost: float | None = None,
    estimated: bool | None = None,
    adapter_version: str | None = None,
    prompt_sha256: str | None = None,
    phase: str | None = None,
    scope_tokens: int | None = None,
    forecast_tokens: int | None = None,
    folded_info: tuple[str, ...] = (),
) -> RunRecord:
    """Assemble a :class:`RunRecord`, deriving the outcome and stamping the time.

    *command* must already be redacted by the caller (the prompt elided) — this
    module never sees the raw prompt. *model* is the runner's pinned model
    (basicly-45ld), null when it pins none. *tokens*/*cost*/*estimated* carry
    the run's token telemetry (basicly-kjc5.1, ``runner.extract_usage``); all
    three null when nothing executed.
    """
    return RunRecord(
        agent=agent,
        outcome=outcome_of(handoff=handoff, returncode=returncode),
        returncode=returncode,
        duration_s=duration_s,
        command=tuple(command),
        timestamp=datetime.now(UTC).isoformat(),
        model=model,
        tokens=tokens,
        cost=cost,
        estimated=estimated,
        adapter_version=adapter_version,
        prompt_sha256=prompt_sha256,
        phase=phase,
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        folded_info=tuple(folded_info),
        # Read here rather than passed in: the overrides are process-global for the
        # session, so stamping them centrally means no dispatch site can forget to
        # and a later one gets it for free (basicly-jr0l.8).
        config_overrides=session.override_pairs(),
    )


# Serializes same-process writers: the supervisor's concurrent dispatch
# (basicly-kjc5.6) records lanes from pool threads that share one PID, so both
# the read-modify-write and the per-process tmp path need a process-local lock.
# Cross-process safety stays what it was: the atomic tmp-then-replace, with a
# lost update under a true cross-process race accepted for telemetry.
_RECORD_LOCK = threading.Lock()


def record(repo_root: Path, bead_id: str, run_record: RunRecord) -> None:
    """Append *run_record* under *bead_id*, writing the file atomically.

    Creates the self-ignored ``.basicly/usage/`` directory on first write. A
    corrupt, non-dict, or wrong-shaped file restarts that bead's history empty
    rather than raising — the record history is telemetry, not something that
    should ever fail a loop landing. Same-process writers (the supervisor's
    dispatch pool) are serialized by :data:`_RECORD_LOCK`; the per-process tmp
    file keeps concurrent *processes* from corrupting each other's rename.
    """
    usage_dir = repo_root / USAGE_DIR
    usage_dir.mkdir(parents=True, exist_ok=True)
    gitignore = usage_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    records_file = repo_root / RUN_RECORDS_FILE
    with _RECORD_LOCK:
        data = _read(records_file)
        history = data.get(bead_id)
        if not isinstance(history, list):  # missing, or an externally-tampered value
            history = []
            data[bead_id] = history
        history.append(asdict(run_record))

        tmp = records_file.with_suffix(f".{os.getpid()}.json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(records_file)


def load_run_records(repo_root: Path) -> dict[str, list[dict]] | None:
    """The raw record map keyed by bead id, or None when no file exists yet."""
    records_file = repo_root / RUN_RECORDS_FILE
    if not records_file.exists():
        return None
    return _read(records_file)


def latest_record(repo_root: Path, bead_id: str) -> RunRecord | None:
    """The most recent :class:`RunRecord` for *bead_id*, or None when there is none.

    Rebuilds the last persisted entry into a :class:`RunRecord`, keeping only
    known fields (an older on-disk record with extra/missing keys still loads via
    the dataclass defaults). Returns None for a missing file, an absent/empty bead
    history, or a malformed last entry — attribution (basicly-140a) reads this and
    must be best-effort, never fatal to a landing.
    """
    data = load_run_records(repo_root)
    if not data:
        return None
    history = data.get(bead_id)
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return None
    known = {f.name for f in fields(RunRecord)}
    kwargs = {k: v for k, v in history[-1].items() if k in known}
    try:
        return RunRecord(**kwargs)
    except TypeError:
        return None


def _read(records_file: Path) -> dict[str, list]:
    """The record map on disk; an empty map for a missing/corrupt/non-dict file."""
    try:
        data = json.loads(records_file.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


# --- Shared evidence ledger (D11, basicly-kjc5.28) ---------------------------

# Marker family carrying one dispatch's evidence on the bead it ran for. The
# fourth alongside [harness-policy], [harness-decision] and [harness-info], and
# for the same reason: br exports comments in issues.jsonl, so a marker travels
# with a clone while .basicly/usage/ does not. That makes this the only carrier
# a teammate's calibration can read (D11).
MARKER = "[harness-run]"


def marker_id(bead_id: str, prompt_sha256: str, phase: str, attempt: int = 1) -> str:
    """The content-derived id for one dispatch's marker.

    Derived rather than sequential so re-recording the same dispatch is
    idempotent instead of duplicated — the decision-queue pattern
    (``decisions.decision_id_for``). *attempt* distinguishes a genuine re-run
    whose prompt and phase happened to be identical, which is how rework shows
    up in the ledger rather than collapsing into one entry.
    """
    digest = hashlib.sha256(f"{phase}:{prompt_sha256}".encode()).hexdigest()[:10]
    suffix = digest if attempt == 1 else f"{digest}-{attempt}"
    return f"{bead_id}#run-{suffix}"


def _recorded_marker_ids(repo_root: Path, bead_id: str) -> set[str]:
    """Marker ids already recorded on *bead_id*; empty when br cannot answer."""
    proc = br.try_run_br(repo_root, ["comments", "list", bead_id, "--json"])
    if proc is None or proc.returncode != 0:
        return set()
    try:
        comments = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return set()
    if not isinstance(comments, list):
        return set()
    found: set[str] = set()
    for comment in comments:
        text = comment.get("text", "") if isinstance(comment, dict) else ""
        for line in text.splitlines():
            if line.startswith(f"{MARKER} id="):
                found.add(line[len(f"{MARKER} id=") :].split()[0])
    return found


def record_marker(repo_root: Path, bead_id: str, run_record: RunRecord) -> str | None:
    """Persist *run_record* as a ``[harness-run]`` marker on *bead_id*.

    Returns the marker id, or None when there is nothing to key on (no prompt
    digest) or br is unavailable. Best-effort like :func:`record`: the ledger is
    evidence, and a write failure must never fail a landing.

    A handoff is recorded too — that nothing executed is itself evidence, and
    omitting it would make the dispatch count understate the attempts.
    """
    if not run_record.prompt_sha256:
        return None
    phase = run_record.phase or "dispatch"
    existing = _recorded_marker_ids(repo_root, bead_id)
    attempt = 1
    while marker_id(bead_id, run_record.prompt_sha256, phase, attempt) in existing:
        attempt += 1
    ident = marker_id(bead_id, run_record.prompt_sha256, phase, attempt)
    payload = {k: v for k, v in asdict(run_record).items() if v not in (None, (), [])}
    body = f"{MARKER} id={ident} phase={phase}\n{json.dumps(payload, sort_keys=True)}"
    if br.try_run_br(repo_root, ["comments", "add", bead_id, body]) is None:
        return None
    return ident
