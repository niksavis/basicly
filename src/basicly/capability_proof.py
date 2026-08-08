"""Whether every capability this repo declares it ships has an execution on record.

Exercised-or-unproven (basicly-irrm): no tag for a capability nothing ever ran.
It is the deterministic form of this repo's own rule that a capability claim on a
consumer-facing surface must be exercised before it is published — a false claim in
code is caught by a gate, one in a README is caught by a consumer — and it is the
shape every Phase S defect had: an instrument built, shipped, and never run.

The inventory is **derived, never hand-listed**, and the evidence is **the ledgers
already on disk**. Both halves are answered here because they are one question
asked twice: what does this repo claim, and what has this checkout recorded. A
curated inventory can be curated down to nothing and then passes forever, which is
the vacuous-instrument defect the gate exists to remove.

Fails closed. With capabilities declared and no ledger at all every one of them is
unproven, so the caller refuses — the alternative reads a git-ignored file's absence
as a pass, which is precisely how a gate ends up green while doing nothing.

Split out of ``release`` when the module-size ratchet caught that module growing.
The boundary is *the claim* against *the release*: nothing here reads a version,
writes a file or knows a tag exists — :func:`basicly.release.blocking_reasons` is
the one caller, and it treats these reasons exactly like every other refusal it
collects. That is also why the split leaves no import back: the evidence comes from
:mod:`basicly.usage` and :mod:`basicly.tracker_usage`, which sit below both.
"""

from __future__ import annotations

from pathlib import Path

from . import tracker_usage, usage
from .config import load_verify_config

# The one kind of capability the repo declares today. Named so a second kind is an
# entry rather than a second copy of the gate.
CAPABILITY_VERIFY_CHECK = "verify check"


def shipped_capabilities(repo_root: Path) -> tuple[tuple[str, str], ...]:
    """The capabilities *repo_root* declares it ships, derived from its committed config.

    **Derived, never hand-listed.** A curated inventory can be curated down to nothing
    and then passes forever, which is the vacuous-instrument defect this gate exists to
    remove: the import contract forbade modules that cannot exist and reported ``1 kept,
    0 broken`` for months while proving nothing.

    Today the declaration is the ``[[verify.checks]]`` gate set — each check names an
    executable the repo commits to running, so a check whose tool has never executed
    here is a gate that is declared and does nothing (``permissions-check`` shipped
    wired to no gate; ``vulture`` is declared at ``pyproject.toml:37`` and called from
    nowhere).

    Each pair is ``(label, witness)``: the label names the capability the way a refusal
    reports it, and the witness is the key :func:`recorded_executions` would carry it
    under. A record with named fields would read better here and is deliberately not
    used — its fields would be read only by this module, which is the shape
    ``wired-or-deleted`` rejects, and there is no consumer outside this module to wire
    one to. A pair carries the same two values without claiming to be a public type.

    The witness is the check's ``name``, namespaced by ``usage.VERIFY_CHECK_PREFIX`` and
    recorded by the one component that ever executes a declared check:
    :func:`basicly.verify.run_check` records every check it watches pass. It used to be
    the check's ``command[0]``, read off the ``tool-usage`` hook's counters, and that
    measured *who typed a word* rather than whether the check ran — a witness that was
    simultaneously unsatisfiable and unfalsifiable (basicly-3yi3). Unsatisfiable for a
    check nothing invokes by hand: ``vulture`` exists only as a declaration, so its
    counter was never created and no verify run could create it, and the gate blocked
    v0.7.0 over a check that had just passed. Unfalsifiable for a check behind a wrapper:
    ``wired-or-deleted`` runs as ``uv run python .scripts/...``, and ``uv``'s thousands
    of executions would stay healthy if the check were deleted outright. A name is
    unique per declaration and nothing but the engine running that declaration can earn
    it, which closes both directions with one key.

    A tuple of pairs rather than a mapping, because two checks may legitimately share a
    name and a mapping would silently drop one — an inventory that quietly shrinks is
    the same defect as one curated down to nothing.

    Empty for a repo that declares no checks: a consumer with no ``[verify]`` section
    has made no capability claim for this gate to hold, and refusing its release would
    be the gate inventing a requirement instead of reading one. ``test_release.py`` holds
    the other half — that *this* repo's inventory is never empty — because an inventory
    that names nothing cannot refuse anything.
    """
    return tuple(
        (
            f"{CAPABILITY_VERIFY_CHECK} {check.name!r}",
            f"{usage.VERIFY_CHECK_PREFIX}{check.name}",
        )
        for check in load_verify_config(repo_root).checks
    )


def recorded_executions(repo_root: Path) -> dict[str, int] | None:
    """Executions per capability key across the ledgers on disk, or None with no ledger.

    All three halves, unioned here rather than inside any one of them:
    :mod:`basicly.usage` and :mod:`basicly.tracker_usage` are independent siblings in the
    engine's bottom tier (``.importlinter``), so neither may read the other, and this
    module is the consumer that legitimately reads both. Skipping a half would fabricate
    a refusal from evidence that exists.

    * the verify engine's own per-check ledger (:func:`usage.load_verify_checks`), keyed
      here under ``usage.VERIFY_CHECK_PREFIX`` so a check's name can never collide with
      a tool of the same name. This is the half every capability in today's inventory is
      witnessed by, and the only one that reports a capability *passing* rather than
      merely being named on a command line.
    * the ``tool-usage`` hook's counters, keyed by the executable name as the recorder
      wrote it — ``ruff``, ``basicly``, and ``skill:<slug>`` for a skill invocation. A
      counter the hook created and never incremented is dropped: a zero is not an
      execution.
    * the tracker ledger's measured surfaces (:func:`tracker_usage.surface_executions`),
      which is the *committed* half and so answers on a machine whose counters are empty.

    The last two witness no capability the inventory declares today, and that is the
    correction basicly-3yi3 made rather than an oversight: what they record is that a
    human or an agent ran a *tool*, which is not evidence that the check wrapping it ran
    (``uv`` at 6091 executions would look identical with ``wired-or-deleted`` deleted).
    They stay because this function answers "what executions does this checkout have on
    record", the ledger set is what the ``None`` below is judged over, and a second kind
    of capability — one genuinely witnessed by a typed tool — is then an inventory entry
    rather than a change here.

    ``None`` means no ledger exists at all, and the caller must keep it apart from a
    recorded zero: absence of a record is not evidence that a capability ran. All are
    read from *repo_root* — the counters because the hook writes them into the checkout's
    own ``.basicly/usage/``, and a release is refused from a linked worktree anyway.

    A dispatch record (:mod:`basicly.run_record`) is deliberately not folded in: nothing
    in the inventory is witnessed by an agent dispatch, and a key nobody writes would
    read as a measured zero.
    """
    counters = usage.load_usage(repo_root)
    check_runs = usage.load_verify_checks(repo_root)
    surfaces = tracker_usage.surface_executions(repo_root)
    if counters is None and check_runs is None and not surfaces:
        return None
    counts: dict[str, int] = {}
    for name, entry in (counters or {}).items():
        if executions := _counted(entry):
            counts[str(name)] = executions
    for name, entry in (check_runs or {}).items():
        if executions := _counted(entry):
            counts[f"{usage.VERIFY_CHECK_PREFIX}{name}"] = executions
    for key, calls in surfaces.items():
        counts[key] = counts.get(key, 0) + calls
    return counts


def _counted(entry: object) -> int:
    """The executions a counter entry records, or 0 — a zero is not an execution."""
    if not isinstance(entry, dict):
        return 0
    count = entry.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count
    return 0


def unexercised_capabilities(repo_root: Path) -> tuple[str, ...]:
    """Reasons a tag would publish an unproven capability claim, in report order.

    Fails closed on missing evidence. With capabilities declared and no ledger at all,
    every one of them is *unproven* rather than exercised, so the release refuses and
    says which ledger it looked for — the alternative reads a git-ignored file's absence
    as a pass, which is precisely how a gate ends up green while doing nothing.

    Reported one line per capability rather than one aggregate line, for the reason
    :func:`blocking_reasons` reports all of its reasons together: each names a distinct
    thing a human fixes, by exercising it or by dropping the claim.
    """
    capabilities = shipped_capabilities(repo_root)
    if not capabilities:
        return ()
    counts = recorded_executions(repo_root)
    if not counts:
        return (
            f"no execution ledger at {usage.VERIFY_CHECKS_FILE.as_posix()}, "
            f"{usage.USAGE_FILE.as_posix()} or {tracker_usage.LEDGER_FILE.as_posix()}, so "
            f"every declared capability is unproven ({len(capabilities)} declared): "
            "exercise them on this machine and re-run (`basicly verify` records every "
            "check it runs; `basicly usage report` shows what is recorded)",
        )
    return tuple(
        f"unexercised capability: {label} — nothing has recorded an execution under "
        f"{witness!r}; exercise it or drop the claim before tagging (`basicly verify` "
        "records a check it runs and watches pass, in each mode that declares it)"
        for label, witness in capabilities
        if counts.get(witness, 0) <= 0
    )
