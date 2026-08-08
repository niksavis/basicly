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

from . import br, dispatch_phase, session, spend_calibration

USAGE_DIR = Path(".basicly/usage")
RUN_RECORDS_FILE = USAGE_DIR / "run-records.json"

# Substituted for the prompt argument in a persisted command, so a run-record is
# metadata-only and can never carry the prompt (or a secret embedded in it).
REDACTED_PROMPT = "<prompt-redacted>"

# Outcome labels for a dispatched run.
EXECUTED = "executed"  # ran to completion with exit 0
FAILED = "failed"  # ran to completion with a non-zero exit
HANDOFF = "handoff"  # no CLI invocation — handed to the driving agent/human
# The dispatch was attempted and died before any agent process started — a
# tracker read that failed, a missing CLI, an unresolvable model. Distinct from
# FAILED, which is an agent that ran and exited non-zero, and from HANDOFF,
# which was never going to run one: this is the label that says a recorded
# estimate cannot be hiding an agent's real spend (basicly-jr0l.64).
UNSTARTED = "unstarted"


# --- Dispatch phases and spend forecasting, re-bound (basicly-tcmy.5, jr0l.21) -
#
# Both vocabularies moved below this module — the phase names to
# :mod:`basicly.dispatch_phase` and the spend ratios to
# :mod:`basicly.spend_calibration` — and are re-bound here because this is the
# module every writer and every reader of a dispatch already imports. An alias
# rather than a wrapper: one object, so a name is the same whichever module it is
# read through, and there is no second definition to drift.
BUILD_PHASE = dispatch_phase.BUILD_PHASE
LANE_PHASE = dispatch_phase.LANE_PHASE
VALIDATE_PHASE = dispatch_phase.VALIDATE_PHASE
DECIDE_PHASE = dispatch_phase.DECIDE_PHASE
PROPOSE_PHASE = dispatch_phase.PROPOSE_PHASE
WRITE_PHASES = dispatch_phase.WRITE_PHASES
is_write_phase = dispatch_phase.is_write_phase

PRIOR_RATIO = spend_calibration.PRIOR_RATIO
MEASURED_RATIO = spend_calibration.MEASURED_RATIO
UNDECLARED_RATIO = spend_calibration.UNDECLARED_RATIO
SpendPrior = spend_calibration.SpendPrior
DECLARED_SPEND_PRIOR = spend_calibration.DECLARED_SPEND_PRIOR
CalibratedRatio = spend_calibration.CalibratedRatio
SpendCalibration = spend_calibration.SpendCalibration
spend_samples = spend_calibration.spend_samples
calibrate_spend = spend_calibration.calibrate_spend


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
    # Which terminal bound stopped this dispatch, for a run that did not reach its
    # own exit (``runner.SPEND_BOUND``/``QUIET_BOUND``); null on a clean run and on
    # the wall-clock backstop, which ``outcome`` already labels (basicly-lpsf).
    #
    # The field that makes the new bounds falsifiable. ``quiet_after`` had to be
    # declared without a measurement — no inter-event gap has ever been recorded,
    # because until basicly-rupz the stream every metered lane emits was requested,
    # paid for and thrown away — so the only way it stops being a guess is for the
    # ledger to say how often it fired and on what. A bound nothing records is a
    # bound nobody can tighten, which is exactly how ``runner_timeout`` came to sit
    # at 95% of the real work distribution for as long as it did.
    stopped_bound: str | None = None
    # Sizing inputs frozen at dispatch, so a later calibration cannot silently
    # re-derive them against a changed tree (D8 drift, basicly-kjc5.30).
    scope_tokens: int | None = None
    forecast_tokens: int | None = None
    # The **whole-lane spend** the same dispatch was forecast to cost — the quantity
    # ``tokens`` above measures, and the one a budget bounds.
    #
    # ``forecast_tokens`` beside it is a **working set**: the context a lane holds at
    # once, which is what a context window bounds. An agentic loop re-sends that
    # context every turn, so the two are denominated in different quantities and their
    # ratio is a turn multiplier rather than a forecast error — over 27 paired records
    # on this repo it ran 64x-793x (median 307x) and read as a forecast wrong by two
    # orders of magnitude (basicly-tcmy.34). The engine had already computed the
    # right-unit number all along: ``decompose.forecast_spend`` produces it and
    # ``supervise.admit_pass_spend`` refuses a pass on it. It simply never reached the
    # record, so the only forecast a completed lane could be compared against was the
    # one denominated in the other quantity.
    #
    # Recorded beside the working set rather than replacing it, because each has an
    # actual of its own and neither comparison can be made without both halves:
    # ``forecast_tokens`` against ``context_tokens``, and this against ``tokens``.
    forecast_spend_tokens: int | None = None
    # The *actual* the scope-and-forecast pair above predicts: how full the window was
    # when the lane finished (``runner.context_occupancy``), null wherever the
    # adapter cannot answer — claude's non-streaming envelope and copilot report
    # no per-turn occupancy, and a handoff ran nothing (basicly-fcls).
    #
    # Every working-set number this engine has ever gated on was a *proxy*: the
    # tokenized size of the declared scope, multiplied by a seed. Nothing recorded
    # the quantity the band is denominated in, so `working_set_max` has been
    # derived twice (basicly-3w44, basicly-ipx2) by re-reading the tree and
    # re-applying the formula to itself — an estimator validated against its own
    # output. This is the field that ends that: it is measured, it travels on the
    # marker, and once enough lanes carry one the ambient term and the per-class
    # factors can be fitted to it instead of declared.
    context_tokens: int | None = None
    # The window ``context_tokens`` was measured against, and which input decided it
    # (``runner.ADAPTER_WINDOW`` and friends). The pair is the point, and it is the
    # same declared-versus-measured discipline ``forecast_source`` follows one field
    # up: a window is a *capability claim* about the model a runner dispatches, and a
    # claim nobody re-checks goes stale silently — ``claude`` declared 200_000 while
    # these very records measured 223_221 (basicly-23ep). Carrying the source makes a
    # defaulted window distinguishable from a chosen one, and carrying the value makes
    # the contradiction checkable per record rather than only against today's config.
    # Null on a dispatch recorded before the fields existed.
    context_window: int | None = None
    context_window_source: str | None = None
    # The class the forecast was computed for, and where the forecast came from
    # (``decompose.FROZEN_FORECAST`` / ``DISPATCH_FORECAST``). Recorded rather than
    # re-derived because calibration reads a sample long after the fact and a closed
    # or compacted bead may no longer answer for its own class (basicly-jr0l.34).
    # The source separates a forecast registered before the work from one computed
    # at dispatch; averaging the two would read as prediction skill the estimator
    # does not have.
    task_class: str | None = None
    forecast_source: str | None = None
    # Where the build factor behind ``forecast_tokens`` came from
    # (``decompose.BUILD_FACTOR_SEED`` / ``BUILD_FACTOR_CONFIGURED``). The factor is
    # a bare multiplier on the forecast and nothing measures it — the calibration
    # that appeared to measured spend instead, and basicly-z2wi removed it — so a
    # record carrying the forecast
    # and not its provenance lets a declared number be read back as a measured one.
    # The sibling fields all name their source for the same reason
    # (``forecast_source``, ``SpendCalibration``, ``unsized_lane_tokens``); this was
    # the one input that did not. Null on a dispatch whose scope was unreadable, so
    # no factor was applied at all (basicly-tcmy.5).
    build_factor_source: str | None = None
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


def outcome_of(*, handoff: bool, returncode: int | None, started: bool = True) -> str:
    """Label a dispatch: handoff, unstarted, or executed/failed by its exit code.

    *started* is False only for a dispatch that died before its agent process
    existed. Its exit code is not "the agent failed" — there was no agent — so
    it takes :data:`UNSTARTED` rather than :data:`FAILED`.
    """
    if handoff:
        return HANDOFF
    if not started:
        return UNSTARTED
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
    started: bool = True,
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
    credits: float | None = None,  # noqa: A002 — the field it feeds is `credits`; renaming
    # the parameter alone would make the two disagree, and the shadowed builtin is the
    # interpreter's interactive easter egg, unreachable from library code.
    adapter_version: str | None = None,
    prompt_sha256: str | None = None,
    phase: str | None = None,
    stopped_bound: str | None = None,
    scope_tokens: int | None = None,
    forecast_tokens: int | None = None,
    forecast_spend_tokens: int | None = None,
    context_tokens: int | None = None,
    context_window: int | None = None,
    context_window_source: str | None = None,
    task_class: str | None = None,
    forecast_source: str | None = None,
    build_factor_source: str | None = None,
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
    three null when nothing executed. *started* is False for a dispatch that
    died before its agent process existed, which labels the outcome
    :data:`UNSTARTED`. The split counts and *credits* are the
    same telemetry at finer grain (basicly-2rn9), null for an adapter that
    reports no split and for a spend billed in USD rather than AI credits.
    *context_tokens* is the measured working set the sizing forecast was trying
    to predict (basicly-fcls), null wherever the adapter reports no occupancy;
    *context_window* and *context_window_source* are the declared window it was
    measured against and which input declared it (basicly-23ep).
    *forecast_spend_tokens* is the same dispatch's forecast in the unit *tokens*
    is measured in — whole-lane spend rather than working set (basicly-tcmy.34).
    """
    return RunRecord(
        agent=agent,
        outcome=outcome_of(handoff=handoff, returncode=returncode, started=started),
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
        stopped_bound=stopped_bound,
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        forecast_spend_tokens=forecast_spend_tokens,
        context_tokens=context_tokens,
        context_window=context_window,
        context_window_source=context_window_source,
        task_class=task_class,
        forecast_source=forecast_source,
        build_factor_source=build_factor_source,
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
    """Marker ids already recorded on *bead_id*; empty when the tracker cannot answer.

    Soft on purpose, and safely so: both callers use it to avoid writing a *second*
    copy of an idempotent record, so "no markers" and "no answer" both correctly mean
    write one now. A counter reads :func:`basicly.br.read_comments` instead.
    """
    found: set[str] = set()
    for comment in br.try_read_comments(repo_root, bead_id):
        text = str(comment.get("text", ""))
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
    if not br.try_add_comment(repo_root, bead_id, body):
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
    also writes a ``[harness-run]`` marker and both stores commit their own
    artifact. So this is the same telemetry as seen by a fresh clone — no br
    invocation, no local usage file (D10/D11).

    Which store answers is :func:`basicly.br.all_comment_texts`'s to decide. It has
    to be the same one :func:`record_marker` writes to, or a dispatch would record
    into the ledger and be counted out of the export (basicly-s5li).
    """
    history: dict[str, list[dict]] = {}
    for bead_id, texts in br.all_comment_texts(repo_root).items():
        payloads = marker_payloads(texts)
        if payloads:
            history[bead_id] = payloads
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
    if not br.try_add_comment(repo_root, bead_id, body):
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
    """Aggregate every ``[harness-cost]`` rollup the committed tracker records.

    The cost-per-landed-package unit, computable from the tracker alone: the
    marker is written only at ship, so one marker is one landed package. A fresh
    clone with no ``.basicly/usage/`` answers this as fully as the machine that
    ran the work — out of whichever store :func:`record_cost_marker` wrote to.
    """
    packages = 0
    actuals: list[dict] = []
    for texts in br.all_comment_texts(repo_root).values():
        rollups = marker_payloads(texts, COST_MARKER)
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
    # The phase the dispatch was recorded under, carried so a calibration can refuse
    # to sample a helper (basicly-tcmy.5). Null for a record written before the field
    # existed, which :func:`is_write_phase` reads as "not shown to be a lane".
    phase: str | None = None
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


def positive_int(entry: Mapping[str, object], key: str) -> int | None:
    """*entry*'s value at *key* when it is a usable positive count, else None.

    A zero forecast is rejected along with a missing one: it cannot be divided by,
    and a "forecast" of zero tokens is a recording defect rather than a prediction.

    Public because every reader of this ledger needs the same rule and a second copy
    would drift from it — ``decompose.spend_accuracy`` reads the same records to hold
    a forecast against its actual (basicly-tcmy.34).
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
            forecast = positive_int(entry, "forecast_tokens")
            actual = positive_int(entry, "tokens")
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
                    phase=_text(entry, "phase"),
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
