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

import json
import os
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

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
    )


def record(repo_root: Path, bead_id: str, run_record: RunRecord) -> None:
    """Append *run_record* under *bead_id*, writing the file atomically.

    Creates the self-ignored ``.basicly/usage/`` directory on first write. A
    corrupt, non-dict, or wrong-shaped file restarts that bead's history empty
    rather than raising — the record history is telemetry, not something that
    should ever fail a loop landing. The tmp file is per-process so concurrent
    dispatches writing the shared base file cannot corrupt each other's rename
    (a lost update under a true write-write race is acceptable for telemetry).
    """
    usage_dir = repo_root / USAGE_DIR
    usage_dir.mkdir(parents=True, exist_ok=True)
    gitignore = usage_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    records_file = repo_root / RUN_RECORDS_FILE
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
