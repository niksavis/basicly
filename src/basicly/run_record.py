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
import statistics
import threading
from collections.abc import Iterable, Mapping
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
    # How that pin was decided (basicly-kjc5.59). ``model`` above is the *what*;
    # these are the *why*, and they cannot be re-derived later — the map, the
    # config and the catalog all move on. ``model_tier`` is the declared tier,
    # ``model_source`` which input decided it (an explicit pin, the agent's tier,
    # the family default), and ``tier_honoured`` is False only when a tier was
    # asked for and the family could not pin one at all, so the dispatch ran on
    # the session's own model — recorded rather than reported as satisfied.
    model_tier: str | None = None
    model_source: str | None = None
    tier_honoured: bool | None = None
    # What the adapter said it actually ran, and any divergence from the pin.
    # Empty means **unobserved**, not "matched": codex reports no model at all, so
    # a mismatch there is unknowable and must not be invented. More than one
    # entry is normal — a copilot dispatch can switch model mid-run and its store
    # then names every model it used.
    observed_models: tuple[str, ...] = ()
    model_mismatch: str | None = None
    # Token telemetry (basicly-kjc5.1): total tokens and USD cost for the run,
    # from adapter-reported usage where the CLI emits it. estimated=True marks
    # a chars/4 transcript fallback (design 7.5) so calibration can down-weight
    # it; all three stay null for a handoff — nothing executed, nothing to meter.
    tokens: int | None = None
    cost: float | None = None
    estimated: bool | None = None
    # Per-kind token split, null for an adapter that reports no split
    # (basicly-2rn9). Provider-neutral on purpose: copilot fills them from its
    # session store's ``session.shutdown`` model metrics, and codex's own split
    # lands on these same fields. ``tokens`` above stays the single summed
    # total — policy's grant ceiling, sizing calibration and the cost rollups all
    # read that key, so redefining it would silently change every one of them.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    # AI credits spent, **not** USD. ``cost`` above is USD (claude's
    # total_cost_usd); copilot bills in AIU, which it reports as nano-AIU. Two
    # currencies, two fields: summing them would be a silent accounting defect,
    # and a rollup that reads ``cost`` stays in one unit.
    credits: float | None = None
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
    # The class the forecast was computed for, and where the forecast came from
    # (``decompose.FROZEN_FORECAST`` / ``DISPATCH_FORECAST``). Recorded rather than
    # re-derived because calibration reads a sample long after the fact and a closed
    # or compacted bead may no longer answer for its own class (basicly-jr0l.34).
    # The source separates a forecast registered before the work from one computed
    # at dispatch; averaging the two would read as prediction skill the estimator
    # does not have.
    task_class: str | None = None
    forecast_source: str | None = None
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
    # --- Dispatch ordering (D9, basicly-vkh0.3) -------------------------------
    # Why this lane went when it did. `loop_state.ready_ranked` passed br's rank
    # through untouched as the primary ordering key and nothing recorded it, so a
    # pass's ordering could not be reconstructed afterwards — a live D9 violation
    # in a system whose architecture claims the rule is enforced.
    #
    # `dispatch_rank` is the lane's 1-based position in the order the pass
    # actually dispatched, and it is the field that makes the ordering
    # reconstructible. The three `scheduler_*` fields are br's evidence and are
    # null whenever br did not rank the lane — which is the common case, not an
    # edge case: a provisioned lane is claimed, and `br scheduler` recommends only
    # unclaimed work, so the supervisor orders most lanes by the adoption
    # fallback. Recording both means a null reads as "br had no opinion" rather
    # than as "nobody recorded it".
    #
    # `scheduler_policy` is br's schema version (`br.scheduler.v1`), without which
    # a score is an uninterpretable integer.
    dispatch_rank: int | None = None
    scheduler_rank: int | None = None
    scheduler_fallback_rank: int | None = None
    scheduler_score: int | None = None
    scheduler_policy: str | None = None


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
    model_tier: str | None = None,
    model_source: str | None = None,
    tier_honoured: bool | None = None,
    observed_models: tuple[str, ...] = (),
    model_mismatch: str | None = None,
    tokens: int | None = None,
    cost: float | None = None,
    estimated: bool | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    credits: float | None = None,
    adapter_version: str | None = None,
    prompt_sha256: str | None = None,
    phase: str | None = None,
    scope_tokens: int | None = None,
    forecast_tokens: int | None = None,
    task_class: str | None = None,
    forecast_source: str | None = None,
    folded_info: tuple[str, ...] = (),
    dispatch_rank: int | None = None,
    scheduler_rank: int | None = None,
    scheduler_fallback_rank: int | None = None,
    scheduler_score: int | None = None,
    scheduler_policy: str | None = None,
) -> RunRecord:
    """Assemble a :class:`RunRecord`, deriving the outcome and stamping the time.

    *command* must already be redacted by the caller (the prompt elided) — this
    module never sees the raw prompt. *model* is the runner's pinned model
    (basicly-45ld), null when it pins none. *tokens*/*cost*/*estimated* carry
    the run's token telemetry (basicly-kjc5.1, ``runner.extract_usage``); all
    three null when nothing executed. The split counts and *credits* are the
    same telemetry at finer grain (basicly-2rn9), null for an adapter that
    reports no split and for a spend billed in USD rather than AI credits.
    """
    return RunRecord(
        agent=agent,
        outcome=outcome_of(handoff=handoff, returncode=returncode),
        returncode=returncode,
        duration_s=duration_s,
        command=tuple(command),
        timestamp=datetime.now(UTC).isoformat(),
        model=model,
        model_tier=model_tier,
        model_source=model_source,
        tier_honoured=tier_honoured,
        observed_models=tuple(observed_models),
        model_mismatch=model_mismatch,
        tokens=tokens,
        cost=cost,
        estimated=estimated,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        credits=credits,
        adapter_version=adapter_version,
        prompt_sha256=prompt_sha256,
        phase=phase,
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        task_class=task_class,
        forecast_source=forecast_source,
        folded_info=tuple(folded_info),
        dispatch_rank=dispatch_rank,
        scheduler_rank=scheduler_rank,
        scheduler_fallback_rank=scheduler_fallback_rank,
        scheduler_score=scheduler_score,
        scheduler_policy=scheduler_policy,
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


def _recorded_marker_ids(repo_root: Path, bead_id: str, marker: str = MARKER) -> set[str]:
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
            if line.startswith(f"{marker} id="):
                found.add(line[len(f"{marker} id=") :].split()[0])
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


def marker_payloads(texts: Iterable[str], marker: str = MARKER) -> list[dict]:
    """The JSON payloads carried by *marker* comments among *texts*, in order.

    The reader half of the marker format: a header line naming the marker and its
    id, then one JSON object. A comment that is not this marker, or whose payload
    will not parse, is skipped — the ledger is read best-effort everywhere.
    """
    payloads: list[dict] = []
    for text in texts:
        head, _, body = text.strip().partition("\n")
        if not (head == marker or head.startswith(f"{marker} ")):
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def tracker_history(repo_root: Path) -> dict[str, list[dict]]:
    """Per-dispatch records per bead id, read from the committed tracker export.

    The travelling twin of :func:`load_run_records`: ``.basicly/usage/`` is
    self-ignored and never leaves the machine that wrote it, while every dispatch
    also writes a ``[harness-run]`` marker and comments are exported. So this is
    the same telemetry as seen by a fresh clone — no br invocation, no local
    usage file (D10/D11).
    """
    history: dict[str, list[dict]] = {}
    for record in br.export_records(repo_root):
        payloads = marker_payloads(br.export_comment_texts(record))
        if payloads:
            history[str(record["id"])] = payloads
    return history


def dispatch_history(repo_root: Path) -> dict[str, list[dict]]:
    """Every known dispatch per bead: the tracker's markers unioned with local records.

    Two sources, one sample set. The tracker carries what travels (including
    dispatches other machines ran); the local records carry what this machine has
    recorded but not yet flushed and committed. A dispatch present in both is one
    sample, deduplicated on its timestamp — counting it twice would double-weight
    it in a calibration median and overstate a package's cost.
    """
    history: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    for source in (tracker_history(repo_root), load_run_records(repo_root) or {}):
        for bead_id, entries in source.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                stamp = entry.get("timestamp")
                if isinstance(stamp, str) and stamp:
                    key = (bead_id, stamp)
                    if key in seen:
                        continue
                    seen.add(key)
                history.setdefault(bead_id, []).append(entry)
    return history


# --- Ship-time cost rollup (basicly-kjc5.50) ---------------------------------

# The package-level ledger: one marker per shipped bead carrying the forecast that
# was made and the actual it produced. Its reason for existing is not that the
# per-dispatch markers are lossy — comments are exported and every record here is
# compaction level 0 — but that a *package* cost is the unit forecasting and
# cost-per-landed-package are expressed in, and deriving it means re-walking every
# dispatch of a bead that may since have been compacted. Written by the engine at
# ship, never hand-edited, and nothing branches on it: evidence, not state (D11).
COST_MARKER = "[harness-cost]"


@dataclass(frozen=True)
class CostRollup:
    """What a package actually cost, summed over every dispatch it took.

    Includes failed attempts and handoffs by construction: a rollup over the
    successful dispatch alone would understate exactly those packages whose cheap
    final attempt followed an expensive one. A metric stays None when no dispatch
    reported it, so "nothing was metered" never reads as a measured zero.
    """

    dispatches: int
    tokens: int | None = None
    cost: float | None = None
    wall_clock_s: float | None = None
    # Null when the rework markers could not be read, so a tracker hiccup cannot
    # publish a confident zero.
    rework: int | None = None
    # True when any summed sample was a chars/4 transcript estimate rather than
    # adapter-reported usage, so a consumer can down-weight the total.
    estimated: bool = False


@dataclass(frozen=True)
class CostForecast:
    """The forecast a package was sized with, kept beside its actual.

    The pair is the point: forecast error per class is the learning signal, and a
    median of raw actuals cannot correct a biased estimator. Money is never
    forecast — it is only ever the cost captured per run at the price of the day —
    and wall-clock stays None until the duration predictor (basicly-kjc5.48)
    supplies one.
    """

    tokens: int | None = None
    cost: float | None = None
    wall_clock_s: float | None = None


def _numbers(entries: Iterable[Mapping[str, object]], key: str) -> list[float]:
    """The numeric values recorded under *key*, skipping absent and non-numeric ones."""
    values: list[float] = []
    for entry in entries:
        value = entry.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        values.append(float(value))
    return values


def cost_rollup(
    history: Iterable[Mapping[str, object]], *, rework: int | None = None
) -> CostRollup:
    """Sum *history* — every dispatch on one bead — into a :class:`CostRollup`."""
    entries = [entry for entry in history if isinstance(entry, Mapping)]
    tokens = _numbers(entries, "tokens")
    cost = _numbers(entries, "cost")
    duration = _numbers(entries, "duration_s")
    return CostRollup(
        dispatches=len(entries),
        tokens=round(sum(tokens)) if tokens else None,
        cost=sum(cost) if cost else None,
        wall_clock_s=sum(duration) if duration else None,
        rework=rework,
        estimated=any(entry.get("estimated") is True for entry in entries),
    )


def cost_marker_id(bead_id: str) -> str:
    """The id of *bead_id*'s cost marker — one per package, so re-recording is a no-op."""
    return f"{bead_id}#cost"


# One keyword per recorded field, same as build_record: the payload is the point.
def record_cost_marker(  # noqa: PLR0913
    repo_root: Path,
    bead_id: str,
    *,
    actual: CostRollup,
    forecast: CostForecast,
    task_class: str | None = None,
    scope_tokens: int | None = None,
) -> str | None:
    """Write *bead_id*'s ``[harness-cost]`` rollup; None when nothing was written.

    Nothing is written when br is unavailable, when the write fails, or when the
    package already carries a rollup — the marker is the package's ledger entry
    and a second one would double-count it in cost-per-landed-package.

    Nulls are kept in the payload rather than dropped: a consumer computing
    forecast error has to tell "no forecast was made" apart from a missing field,
    and the ledger is read by programs, not only by people.
    """
    if cost_marker_id(bead_id) in _recorded_marker_ids(repo_root, bead_id, COST_MARKER):
        return None
    ident = cost_marker_id(bead_id)
    payload = {
        "bead": bead_id,
        "task_class": task_class,
        "scope_tokens": scope_tokens,
        "forecast": asdict(forecast),
        "actual": asdict(actual),
    }
    body = f"{COST_MARKER} id={ident}\n{json.dumps(payload, sort_keys=True)}"
    proc = br.try_run_br(repo_root, ["comments", "add", bead_id, body])
    if proc is None or proc.returncode != 0:
        return None
    return ident


@dataclass(frozen=True)
class LandedCost:
    """Cost aggregated over every landed package the tracker records (basicly-7bur)."""

    packages: int
    tokens: int | None = None
    cost: float | None = None
    wall_clock_s: float | None = None

    def per_package(self, metric: str) -> float | None:
        """The mean of *metric* per landed package; None when it was never metered."""
        total = getattr(self, metric)
        if total is None or not self.packages:
            return None
        return total / self.packages


def landed_package_cost(repo_root: Path) -> LandedCost:
    """Aggregate every ``[harness-cost]`` rollup in the committed tracker export.

    The cost-per-landed-package unit, computable from the tracker alone: the
    marker is written only at ship, so one marker is one landed package. A fresh
    clone with no ``.basicly/usage/`` answers this as fully as the machine that
    ran the work.
    """
    packages = 0
    actuals: list[dict] = []
    for record in br.export_records(repo_root):
        rollups = marker_payloads(br.export_comment_texts(record), COST_MARKER)
        if not rollups:
            continue
        # One marker is one landed package even when its payload is malformed —
        # dropping the package from the denominator would flatter the average.
        packages += 1
        actual = rollups[0].get("actual")
        if isinstance(actual, dict):
            actuals.append(actual)
    tokens = _numbers(actuals, "tokens")
    cost = _numbers(actuals, "cost")
    wall_clock = _numbers(actuals, "wall_clock_s")
    return LandedCost(
        packages=packages,
        tokens=round(sum(tokens)) if tokens else None,
        cost=sum(cost) if cost else None,
        wall_clock_s=sum(wall_clock) if wall_clock else None,
    )


# --- Forecast error, per dispatch record (basicly-jr0l.34) --------------------
#
# The learning signal jr0l.21's calibration is built on, and until this landed it
# did not exist: `forecast_tokens` was a declared field with no writer (measured
# non-null on zero of 149 records), while the actual tokens landed on the same
# record from a different code path. Nothing paired the estimate with the outcome,
# so a forecast could be wrong by two orders of magnitude and no report could say
# so. A dispatch now carries both halves, and this reads them back.
#
# The rule that makes the report trustworthy is that it **refuses to compute an
# error for a record missing either half**. A record with a forecast and no actual
# is a handoff or a killed run; one with an actual and no forecast is a helper
# dispatch (the rubric judge, the decider) that was never sized. Either would be a
# fabricated error if it were counted as zero, so both are reported as unpaired
# instead — a report that says "0 pairs, 137 unmetered" is honest, and one that
# quietly showed no rows would look like a passing calibration.
#
# **The two halves do not measure the same quantity, and a reader must be told so.**
# The forecast is a *working set* — the context a package needs — while the actual
# is *total spend*, and an agentic loop re-sends its context every turn, so spend is
# roughly working set times turn count. A ratio of 200x therefore does not mean the
# working-set estimator is wrong by 200x; it is mostly the turn multiplier, which
# nothing models yet (basicly-jr0l.21). This module deliberately calls the quantity
# a *ratio* rather than an error rate, and the CLI says what it is, because reading
# it as estimator error is how a session concludes the sizing governor is broken and
# rewrites the wrong thing.


@dataclass(frozen=True)
class ForecastError:
    """One dispatch whose forecast and actual are both known, so its ratio is real."""

    bead: str
    timestamp: str
    forecast_tokens: int
    actual_tokens: int
    task_class: str | None = None
    model: str | None = None
    forecast_source: str | None = None
    # True when the actual was a chars/4 transcript estimate rather than
    # adapter-reported usage, so a consumer can down-weight the sample (design 7.5).
    estimated: bool = False
    # The money and time the same dispatch spent, carried on the pair rather than
    # looked up again (basicly-jr0l.21): a spend forecast needs a price and a rate
    # per model, and the pair is the only sample set that already knows which model
    # and which class produced them. Null when the adapter metered neither.
    actual_cost: float | None = None
    actual_wall_clock_s: float | None = None

    @property
    def ratio(self) -> float:
        """Actual spend over forecast working set. A forecast of zero never reaches here.

        Not an estimator error rate: see the section comment above. The turn count an
        agentic loop re-sends its context for lives in this number too.
        """
        return self.actual_tokens / self.forecast_tokens

    @property
    def error_tokens(self) -> int:
        """Signed miss in tokens: positive when the dispatch spent more than forecast."""
        return self.actual_tokens - self.forecast_tokens


@dataclass(frozen=True)
class ForecastErrorReport:
    """Every computable forecast error, plus what was skipped and why."""

    errors: tuple[ForecastError, ...] = ()
    # Records deliberately not turned into an error, counted so an empty report
    # explains itself rather than reading as "no error".
    forecast_only: int = 0
    actual_only: int = 0
    unmetered: int = 0

    @property
    def paired(self) -> int:
        """How many records yielded an error."""
        return len(self.errors)

    @property
    def median_ratio(self) -> float | None:
        """Median actual/forecast across the pairs; None with no pairs.

        A median rather than a mean: the measured misses span 160x to 420x, and one
        such sample would drag a mean somewhere no individual dispatch has ever been.
        """
        if not self.errors:
            return None
        return statistics.median(error.ratio for error in self.errors)

    def by_task_class(self) -> dict[str, tuple[ForecastError, ...]]:
        """The pairs grouped by task class, dropping records with no class recorded."""
        grouped: dict[str, list[ForecastError]] = {}
        for error in self.errors:
            if error.task_class:
                grouped.setdefault(error.task_class, []).append(error)
        return {name: tuple(items) for name, items in sorted(grouped.items())}


def _positive_int(entry: Mapping[str, object], key: str) -> int | None:
    """*entry*'s value at *key* when it is a usable positive count, else None.

    A zero forecast is rejected along with a missing one: it cannot be divided by,
    and a "forecast" of zero tokens is a recording defect rather than a prediction.
    """
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def forecast_errors(repo_root: Path) -> ForecastErrorReport:
    """Pair every dispatch's forecast with its actual, over the whole known history.

    Reads :func:`dispatch_history`, so it sees the committed tracker markers as well
    as this machine's local records — a fresh clone can compute the same error a
    teammate measured, which is the property that makes the calibration shared
    rather than per-machine (D11).
    """
    errors: list[ForecastError] = []
    forecast_only = actual_only = unmetered = 0
    for bead_id, history in sorted(dispatch_history(repo_root).items()):
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            forecast = _positive_int(entry, "forecast_tokens")
            actual = _positive_int(entry, "tokens")
            if forecast is None or actual is None:
                if forecast is not None:
                    forecast_only += 1
                elif actual is not None:
                    actual_only += 1
                else:
                    unmetered += 1
                continue
            errors.append(
                ForecastError(
                    bead=bead_id,
                    timestamp=str(entry.get("timestamp", "")),
                    forecast_tokens=forecast,
                    actual_tokens=actual,
                    task_class=_text(entry, "task_class"),
                    model=_text(entry, "model"),
                    forecast_source=_text(entry, "forecast_source"),
                    estimated=entry.get("estimated") is True,
                    actual_cost=_positive_float(entry, "cost"),
                    actual_wall_clock_s=_positive_float(entry, "duration_s"),
                )
            )
    return ForecastErrorReport(
        errors=tuple(sorted(errors, key=lambda error: (error.timestamp, error.bead))),
        forecast_only=forecast_only,
        actual_only=actual_only,
        unmetered=unmetered,
    )


def _text(entry: Mapping[str, object], key: str) -> str | None:
    """*entry*'s value at *key* when it is a non-empty string, else None."""
    value = entry.get(key)
    return value if isinstance(value, str) and value else None


def _positive_float(entry: Mapping[str, object], key: str) -> float | None:
    """*entry*'s value at *key* as a positive float, else None.

    A zero is dropped along with a missing value: a metered cost or duration of
    exactly zero is a recording artefact (a run that never started), and averaging
    it into a per-token price would quietly halve the forecast.
    """
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


# --- Spend forecast, calibrated per model (basicly-jr0l.21) -------------------
#
# What the D8 governor forecasts is a *working set* — the context a lane needs —
# and that is not what a run costs. Measured on the basicly-u6jq.1 proof run, the
# working-set number under-shot actual context by 2.8-4.8x, and because an agentic
# loop re-sends its context every turn, total spend is context times turn count
# with nothing in the engine modelling the turn count at all. Spend landed 160-420x
# the forecast. Every `forecast` field on the shipped cost rollups is null.
#
# So this turns a working-set estimate into predicted spend with three ratios:
# tokens per working-set token (the whole turn multiplier, empirically), USD per
# million tokens, and seconds per million tokens. Money and time hang off the token
# prediction rather than off the working set, so a single multiplier carries the
# loop's behaviour and the other two stay what they are — a price and a rate.
#
# **Keyed per (model, task class), never in aggregate.** The same work costs
# different amounts on different models and models are replaced constantly, so a
# cross-model average is noise. A record whose model was never recorded cannot join
# a key: `model` was null on all 122 historical records, and folding those in would
# reintroduce exactly the aggregate this is built to avoid.
#
# Cold start is unavoidable, so each ratio is *seeded from a declared prior* and
# replaced by the measured median only once `calibration_min_samples` paired records
# exist for that key. The prior travels inside the calibration and each ratio names
# its own source, because a seeded number that reads as measured is worse than no
# number. And an undeclared ratio with too little history stays None: fail closed on
# an indeterminate answer rather than publish a confident zero.

# Where one ratio's value came from. Recorded per ratio, not per forecast: history
# accumulates unevenly (copilot bills credits and reports no USD at all), so a
# forecast is routinely measured in tokens and still seeded in money.
PRIOR_RATIO = "prior"
MEASURED_RATIO = "measured"
UNDECLARED_RATIO = "undeclared"


@dataclass(frozen=True)
class SpendPrior:
    """The declared seed for the three spend ratios, before any history exists.

    A ratio declared None is deliberately unknown — the forecast then reports no
    number for it rather than inventing one. *basis* is the provenance the numbers
    were derived from, recorded with every forecast so a reader can re-derive them.
    """

    tokens_per_working_set_token: float | None
    usd_per_million_tokens: float | None
    seconds_per_million_tokens: float | None
    basis: str


# The declared prior, derived from the only fully metered packages the tracker
# holds: the three basicly-u6jq.1 lanes (basicly-kjc5.32/.50/.51), whose
# `[harness-cost]` rollups carry the actual tokens, USD and wall clock. Their
# forecast fields are null — that is the hole this closes — so the working-set
# column is the governor's number as recorded in basicly-jr0l.21. Per-lane ratios,
# median taken (a mean of a 162x and a 421x sample lands where no lane has been):
#
#   lane        working set   tokens      mult    USD/Mtok   s/Mtok
#   kjc5.32          57_965    9_430_203  162.7      0.820    109.9
#   kjc5.50          47_847   16_002_352  334.4      0.733     92.2
#   kjc5.51          48_897   20_594_047  421.2      0.714     72.1
#
# Wall clock is summed dispatch duration, so it is agent-busy seconds for the
# package, not calendar time for the lane.
DECLARED_SPEND_PRIOR = SpendPrior(
    tokens_per_working_set_token=334.4,
    usd_per_million_tokens=0.733,
    seconds_per_million_tokens=92.2,
    basis="basicly-u6jq.1 proof run, 3 metered packages, per-lane medians",
)


@dataclass(frozen=True)
class CalibratedRatio:
    """One spend ratio, with where it came from and how much history backs it."""

    value: float | None
    # :data:`PRIOR_RATIO`, :data:`MEASURED_RATIO` or :data:`UNDECLARED_RATIO`.
    source: str
    # Samples that could have measured this ratio — reported even when the prior
    # won, so "9 of 10" is distinguishable from "nothing has ever been metered".
    samples: int = 0


@dataclass(frozen=True)
class SpendCalibration:
    """The ratios one package's spend forecast is computed with, and their provenance."""

    tokens_per_working_set_token: CalibratedRatio
    usd_per_million_tokens: CalibratedRatio
    seconds_per_million_tokens: CalibratedRatio
    # The prior in force, recorded even where it was replaced: it is what a later
    # reader needs to tell a seeded forecast from a measured one, and to audit the
    # seed itself once the measured numbers disagree with it.
    prior: SpendPrior
    model: str | None = None
    task_class: str | None = None
    # Paired records that matched (model, task_class) inside the window.
    pairs: int = 0

    @property
    def measured(self) -> bool:
        """True when history replaced at least one ratio."""
        return any(
            ratio.source == MEASURED_RATIO
            for ratio in (
                self.tokens_per_working_set_token,
                self.usd_per_million_tokens,
                self.seconds_per_million_tokens,
            )
        )


def _calibrated(values: list[float], prior: float | None, minimum: int) -> CalibratedRatio:
    """One ratio: the measured median past *minimum* samples, else the prior, else None.

    A median rather than a mean, for the reason :attr:`ForecastErrorReport.median_ratio`
    gives: the measured spread is 160x to 420x and one sample would drag a mean
    somewhere no dispatch has ever been. An empty sample set never measures, whatever
    *minimum* says — there is no median of nothing.
    """
    if values and len(values) >= minimum:
        return CalibratedRatio(statistics.median(values), MEASURED_RATIO, len(values))
    if prior is None:
        return CalibratedRatio(None, UNDECLARED_RATIO, len(values))
    return CalibratedRatio(prior, PRIOR_RATIO, len(values))


# The calibration bounds arrive as the two ints `[policy.sizing]` declares rather
# than as a SizingConfig: `config` imports `runner`, which imports this module, so
# typing them here would close an import cycle. Same stance as build_record — one
# keyword per input, and no module above pulled downwards.
def calibrate_spend(  # noqa: PLR0913
    report: ForecastErrorReport,
    *,
    model: str | None,
    task_class: str | None,
    min_samples: int,
    window: int,
    prior: SpendPrior = DECLARED_SPEND_PRIOR,
) -> SpendCalibration:
    """Resolve the spend ratios for one (*model*, *task_class*) from *report*.

    The sample set is the paired records — a record carrying both a forecast and a
    measured actual — for exactly this model and class, the newest *window* of them,
    with chars/4-estimated actuals excluded. Below *min_samples* the declared prior
    stands, per ratio.

    This is the *only* place a turn multiplier may be measured. It is legitimate here
    because the quantity being predicted is spend, which is what the samples record.
    The build factor predicts a working set and must never be calibrated the same way
    (basicly-z2wi).

    A null *model* or *task_class* matches nothing rather than everything: an
    unrecorded model is unknown provenance, and pooling those samples would rebuild
    the cross-model average this calibration exists to avoid.
    """
    pairs: list[ForecastError] = []
    if model and task_class:
        pairs = [
            error
            for error in report.errors
            if error.model == model and error.task_class == task_class and not error.estimated
        ]
        # report.errors is timestamp-sorted, so the tail is the newest window.
        pairs = pairs[-window:]
    costs = [
        error.actual_cost / error.actual_tokens * 1_000_000
        for error in pairs
        if error.actual_cost is not None
    ]
    seconds = [
        error.actual_wall_clock_s / error.actual_tokens * 1_000_000
        for error in pairs
        if error.actual_wall_clock_s is not None
    ]
    return SpendCalibration(
        tokens_per_working_set_token=_calibrated(
            [error.ratio for error in pairs], prior.tokens_per_working_set_token, min_samples
        ),
        usd_per_million_tokens=_calibrated(costs, prior.usd_per_million_tokens, min_samples),
        seconds_per_million_tokens=_calibrated(
            seconds, prior.seconds_per_million_tokens, min_samples
        ),
        prior=prior,
        model=model,
        task_class=task_class,
        pairs=len(pairs),
    )
