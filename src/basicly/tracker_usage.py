"""Measured usage of the ``br``/``bv`` CLI surface (basicly-vkh0.1).

Phase 6 replaces the tracker, and the scope of that replacement is meant to be a
*frozen surface list*: a subcommand or flag nobody exercises simply does not
exist in the replacement. That list has to come from measurement — writing it
from memory of our own usage is how a replacement acquires surface nobody needed
and misses the one flag a hook depends on.

**Two write paths, one format.** The engine records itself from
:mod:`basicly.br`, the single seam through which every engine call to the tracker
already passes, so a new call site is instrumented by construction and duration
is real. Interactive calls — an agent or a human typing ``br`` in a shell — are
recorded by the ``tool-usage`` PostToolUse hook. They are distinguished by
``site`` because the two sets carry different weight: the engine's set is the
hard requirement for a replacement, while the human's may be thinner and can be
served later or not at all.

**Flag names are recorded; values never are.** ``--json`` is recorded, its value
is not, and a positional argument is dropped entirely. That is not only privacy
— a value is an issue title, a file path, or a machine's home directory, so
recording values would make a committed ledger both a leak and a portability
defect. Names answer the only question the freeze asks: which surface is used.

**Spool, then promote.** Recording appends to a git-ignored spool rather than to
the committed ledger, because the harness refuses to land a worktree while the
base checkout carries dirt outside ``.beads/`` — a ledger touched by every
tracker call would block every landing, which is the defect basicly-jr0l.7
already cost us. ``promote`` folds the spool into the committed ledger at a
moment of the operator's choosing, so the sample accumulates across machines
through git while the working tree stays clean during work.

Telemetry, never a gate: every function here swallows its own errors, a corrupt
line is discarded rather than fatal, and no caller's exit status depends on any
of it.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# The write buffer. Lives under the self-ignoring usage dir, so it never dirties
# the tree; lost with the machine, which is exactly why `promote` exists.
SPOOL_FILE = Path(".basicly/usage/tracker-usage.jsonl")

# The committed, append-only sample. Separate directory from `usage/` precisely
# because that one ignores everything inside it.
LEDGER_FILE = Path(".basicly/ledger/tracker-usage.jsonl")

SITE_ENGINE = "engine"
SITE_INTERACTIVE = "interactive"

# A positional that could name a br subcommand. See :func:`is_surface_word` for
# why a stricter filter than "does not start with -" is required.
_SURFACE_WORD = re.compile(r"^[a-z][a-z0-9-]*$")

# A leading long flag standing in for a subcommand: `bv` has none at all, so a
# flag is the only name its invocation has.
_LONG_FLAG_SURFACE = re.compile(r"^--[a-z][a-z0-9-]*[a-z0-9]$")

# Top-level br commands that take a second word naming a distinct operation, so
# the pair is one surface. Generated from `br --help` and mirrored in
# `.basicly/ledger/tracker-surface.json`; `test_tracker_surface` asserts the two
# agree, which is what turns "keep this in step" into a gate.
#
# The set previously held five entries and was wrong in both directions: it missed
# `audit`, `doctor`, `epic`, `history`, `label` and `query` — so `br label add` and
# `br label list` collapsed to one surface named `label`, understating exactly the
# count a freeze reads — and it listed `catalog`, which is a *basicly* command and
# has never existed in br (basicly-vkh0.2).
GROUP_SUBCOMMANDS = frozenset({
    "audit",
    "comments",
    "config",
    "coordination",
    "dep",
    "doctor",
    "epic",
    "gate",
    "history",
    "label",
    "query",
    "robot-docs",
})

# Subcommands that only read. Used for the read/write ratio the cache design
# rests on: if reads dominate by the margin the 175x in-process figure implies,
# a derived snapshot is worth maintaining; if they do not, it is not.
#
# Deliberately not exhaustive, and unknown subcommands are reported as
# ``unclassified`` rather than guessed into a bucket. A wrong default would
# quietly bias the one number this ledger exists to produce, and the measurement
# itself is what reveals the surface we forgot.
# Two-word surfaces are listed in full: :func:`split_invocation` joins them, so a
# bare "list" here would never match "comments list". Exercising the report on a
# real run is what caught that — every two-word read was landing in
# ``unclassified`` and silently deflating the read side of the ratio.
READ_SUBCOMMANDS = frozenset({
    "blocked",
    "comments list",
    "config get",
    "dep cycles",
    # `dep list` was missing while `dep tree`/`dep cycles` were present, so five
    # measured reads sat in `unclassified` (basicly-vkh0.2). Found by reporting
    # the unclassified rows individually instead of trusting the bucket total.
    "dep list",
    "dep tree",
    "doctor",
    "gate list",
    "lint",
    "list",
    "ready",
    "schema",
    "scheduler",
    "show",
    "stats",
    "version",
    "where",
})
WRITE_SUBCOMMANDS = frozenset({
    "close",
    "comments add",
    "config set",
    "create",
    "delete",
    "dep add",
    "dep remove",
    "flush",
    "gate report",
    "import",
    "reopen",
    # The engine's second-most-called write, and it sat in `unclassified` from the
    # day the ledger landed. Either direction mutates — export rewrites the JSONL,
    # import rewrites the DB — so it is a write regardless of which way it runs.
    "sync",
    "update",
})


@dataclass(frozen=True)
class SurfaceRow:
    """One measured surface: a binary and subcommand, with how it was used."""

    binary: str
    subcommand: str
    calls: int
    engine_calls: int
    interactive_calls: int
    flags: tuple[str, ...]
    mean_ms: float | None
    access: str  # read | write | unclassified


def classify_access(subcommand: str) -> str:
    """``read``, ``write``, or ``unclassified`` for *subcommand*."""
    if subcommand in READ_SUBCOMMANDS:
        return "read"
    if subcommand in WRITE_SUBCOMMANDS:
        return "write"
    return "unclassified"


def is_surface_word(token: str) -> bool:
    """True when *token* could name a ``br`` subcommand.

    Every ``br`` command name is lowercase letters, digits and hyphens. Anything
    else in a positional slot is shell text that survived tokenisation, not a
    surface — and it reached the committed ledger: ``br --version 2>&1`` recorded
    the surface ``2>&1``, and ``br $g --help`` inside a shell loop recorded ``$g``
    (six such rows across four fake surfaces, basicly-vkh0.2). A freeze list is
    exactly the artifact that must not contain them, since its whole purpose is to
    say which surfaces exist.

    Rejecting the token rather than the whole invocation is deliberate: for
    ``br --version 2>&1`` the real surface is the leading flag, and dropping only
    the junk word still records it.
    """
    return bool(_SURFACE_WORD.match(token))


def is_valid_surface(subcommand: str) -> bool:
    """True when *subcommand* is a shape :func:`split_invocation` can legitimately emit.

    Two shapes are legitimate: one or two command words (``show``, ``dep add``), or
    a single long flag for a binary that has no subcommands (``bv --robot-next``,
    ``br --version``). Everything else is recorder junk. Used at the promote
    boundary so the committed ledger cannot inherit it.
    """
    if not subcommand:
        return False
    if subcommand.startswith("-"):
        return bool(_LONG_FLAG_SURFACE.match(subcommand))
    words = subcommand.split()
    return len(words) <= 2 and all(is_surface_word(word) for word in words)


def split_invocation(args: list[str] | tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    """The subcommand and the flag *names* in *args*.

    A leading flag (``br --version``) is a surface in its own right and becomes
    the subcommand, because there is no other name for it. Two-word subcommands
    (``dep add``, ``comments list``, ``gate report``) are joined so the pair is
    one surface — they are separate operations and freezing them as bare ``dep``
    would lose the distinction the replacement has to reproduce.

    ``--flag=value`` is truncated at the ``=``; a value in the next position is
    simply not collected, since only names are recorded.
    """
    words = [arg for arg in args if not arg.startswith("-") and is_surface_word(arg)]
    flags = sorted({arg.split("=", 1)[0] for arg in args if arg.startswith("-")})

    if not words:
        # No positional at all: the leading flag *is* the surface (`br --version`).
        return (flags[0] if flags else "", tuple(flags))

    subcommand = words[0]
    if len(words) > 1 and subcommand in GROUP_SUBCOMMANDS:
        subcommand = f"{subcommand} {words[1]}"
    return subcommand, tuple(flags)


def ledger_root(repo_root: Path) -> Path:
    """The checkout that owns the ledger for *repo_root*, following ``.beads/redirect``.

    **One ledger per repo, never one per worktree.** A loop worktree shares the base
    checkout's tracker through br's git-ignored ``redirect`` file, and the spool has
    to follow the same rule for the same reason. It did not, and the consequence was
    silent: :func:`is_enabled` saw the worktree's own checked-out
    ``.basicly/ledger/`` directory, spooled beside it, and the loop deleted the
    worktree at teardown — so **every engine tracker call made from a lane was
    discarded** (basicly-vkh0.8).

    That is not uniform sampling loss. Lane work is most of what the harness does,
    and it made ``where`` — which ``worktree._probe_redirect`` calls on every single
    provisioning — read as *never used* in the surface report. A false never-used
    entry is the worst error the freeze's input can carry, because it would drop a
    surface the engine depends on.

    Mirrors :func:`basicly.br.beads_dir` rather than inventing a second rule: the
    redirect names the base checkout's ``.beads``, whose parent is that checkout.
    Falls back to *repo_root* when the redirect is absent or unreadable, so a plain
    checkout is unaffected and telemetry never becomes a failure path.
    """
    root = Path(repo_root)
    redirect = root / ".beads" / "redirect"
    try:
        if redirect.is_file():
            target = Path(redirect.read_text(encoding="utf-8").strip())
            if target.is_dir() and target.name == ".beads":
                return target.parent
    except OSError:
        return root
    return root


def _spool_path(repo_root: Path) -> Path:
    return ledger_root(repo_root) / SPOOL_FILE


def is_enabled(repo_root: Path) -> bool:
    """True when *repo_root* has opted in by committing the ledger directory.

    The presence of ``.basicly/ledger/`` is the switch, and it needs no config
    key. This repo commits the directory, so recording is on here; a consumer
    installation does not have it, so nothing is written into somebody else's
    tree.

    That distinction is not cosmetic. Recording unconditionally created
    ``.basicly/usage/`` in a consumer repo as a side effect of any tracker call —
    caught by the uninstall test, which found ``.basicly`` surviving an uninstall
    that had removed everything it manages. Leaving a directory behind in a repo
    that never asked for our development telemetry is a defect twice over: it is
    an uninvited write, and for a distribution it multiplies across the install
    base.

    Asked of :func:`ledger_root`, so the switch and the spool it gates are the same
    authority — a worktree cannot be recording into a directory the base does not
    own.
    """
    return (ledger_root(repo_root) / LEDGER_FILE).parent.is_dir()


def record(  # noqa: PLR0913 — six independent facts about one observation
    repo_root: Path,
    binary: str,
    args: list[str] | tuple[str, ...],
    *,
    site: str,
    duration_ms: float | None = None,
    ok: bool = True,
    attempt: int = 1,
) -> None:
    """Append one invocation to the spool. Never raises.

    A no-op unless the repo has opted in (:func:`is_enabled`).

    A single ``write`` of one line keeps concurrent lanes from interleaving a
    partial record: several supervisor lanes share this file, and append mode
    with one write per record is the cheapest thing that survives that without a
    lock. A torn line would be discarded by the reader anyway.
    """
    try:
        if not is_enabled(repo_root):
            return
        subcommand, flags = split_invocation(args)
        if not subcommand:
            return
        entry = {
            "binary": binary,
            "subcommand": subcommand,
            "flags": list(flags),
            "site": site,
            "ok": ok,
        }
        if duration_ms is not None:
            entry["duration_ms"] = round(float(duration_ms), 1)
        if attempt > 1:
            # Only a retry carries this, so existing lines stay byte-identical and
            # the committed ledger does not churn (basicly-jr0l.42). Reading the
            # highest attempt per burst is how the retry bound stops being a guess.
            entry["attempt"] = attempt
        spool = _spool_path(repo_root)
        spool.parent.mkdir(parents=True, exist_ok=True)
        gitignore = spool.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        with spool.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")
    except Exception:  # nosec B110 — telemetry must never fail a tracker call
        pass


def timed(
    repo_root: Path,
    binary: str,
    args: list[str] | tuple[str, ...],
    *,
    site: str,
    attempt: int = 1,
):
    """Context manager recording *args* with its measured wall-clock duration.

    Uses a monotonic clock: a wall-clock delta can go backwards across an NTP
    step and produce a negative latency, and latency per surface is one of the
    two numbers this ledger exists to produce.
    """

    class _Timer:
        def __init__(self) -> None:
            self.ok = True

        def __enter__(self) -> _Timer:
            self._start = time.monotonic()
            return self

        def __exit__(self, exc_type, exc, tb) -> bool:
            elapsed_ms = (time.monotonic() - self._start) * 1000
            record(
                repo_root,
                binary,
                args,
                site=site,
                duration_ms=elapsed_ms,
                ok=self.ok and exc_type is None,
                attempt=attempt,
            )
            return False  # never swallow the caller's exception

    return _Timer()


def _read_jsonl(path: Path) -> list[dict]:
    """Every well-formed object in *path*; missing file and torn lines are empty/skipped."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return []
    entries: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("subcommand"), str):
            entries.append(entry)
    return entries


def load(repo_root: Path) -> list[dict]:
    """The committed ledger plus anything still spooled, ledger first.

    The two halves resolve differently on purpose. The ledger is a **tracked** file,
    so it belongs to the checkout you are on — reading it from *repo_root* is what
    makes a report reflect the branch under test. The spool is machine-local and
    shared with the base checkout (:func:`ledger_root`), so a report run inside a
    worktree still sees what its own lane recorded.
    """
    return _read_jsonl(Path(repo_root) / LEDGER_FILE) + _read_jsonl(_spool_path(repo_root))


def promote(repo_root: Path) -> tuple[int, int]:
    """Fold the spool into the committed ledger and clear it.

    Returns ``(promoted, discarded)``.

    Truncates the spool only after the ledger write has been flushed, so an
    interruption loses nothing: the worst case re-promotes records already in the
    ledger, and a duplicated observation biases a count rather than destroying
    the sample. Losing the spool silently would be the worse failure.

    **A record whose subcommand cannot be a surface is discarded here rather than
    committed.** The ledger is the input to a surface freeze, so a malformed
    observation in it is worse than a missing one — and the spool on any machine
    that ran an older recorder already holds some (``2>&1``, ``$g``). Validating at
    the boundary means the committed artifact is clean regardless of which recorder
    version produced the spool, instead of depending on everyone having upgraded.
    The count is returned, never swallowed: a caller reports what it dropped.

    Reads the shared spool (:func:`ledger_root`) and writes *repo_root*'s ledger:
    promoting is a step toward a commit, so the tracked file it grows must be the one
    on the current branch, while the observations it drains are the machine's.
    """
    root = Path(repo_root)
    spool = _spool_path(repo_root)
    spooled = _read_jsonl(spool)
    if not spooled:
        return 0, 0
    keep = [entry for entry in spooled if is_valid_surface(str(entry["subcommand"]))]
    discarded = len(spooled) - len(keep)
    ledger = root / LEDGER_FILE
    ledger.parent.mkdir(parents=True, exist_ok=True)
    with ledger.open("a", encoding="utf-8") as handle:
        for entry in keep:
            handle.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")
        handle.flush()
    spool.write_text("", encoding="utf-8")
    return len(keep), discarded


def summarize(repo_root: Path) -> list[SurfaceRow]:
    """One row per measured ``(binary, subcommand)``, most-used first."""
    entries = load(repo_root)
    grouped: dict[tuple[str, str], list[dict]] = {}
    for entry in entries:
        key = (str(entry.get("binary") or "br"), str(entry["subcommand"]))
        grouped.setdefault(key, []).append(entry)

    rows: list[SurfaceRow] = []
    for (binary, subcommand), group in grouped.items():
        flags: Counter[str] = Counter()
        for entry in group:
            raw = entry.get("flags")
            if isinstance(raw, list):
                flags.update(str(flag) for flag in raw)
        durations = [
            float(entry["duration_ms"])
            for entry in group
            if isinstance(entry.get("duration_ms"), int | float)
        ]
        rows.append(
            SurfaceRow(
                binary=binary,
                subcommand=subcommand,
                calls=len(group),
                engine_calls=sum(1 for e in group if e.get("site") == SITE_ENGINE),
                interactive_calls=sum(1 for e in group if e.get("site") == SITE_INTERACTIVE),
                flags=tuple(flag for flag, _ in flags.most_common()),
                mean_ms=(sum(durations) / len(durations)) if durations else None,
                access=classify_access(subcommand),
            )
        )
    rows.sort(key=lambda row: (-row.calls, row.binary, row.subcommand))
    return rows


def access_ratio(rows: list[SurfaceRow]) -> dict[str, int]:
    """Calls per access class, so the read/write ratio is readable at a glance."""
    totals: Counter[str] = Counter()
    for row in rows:
        totals[row.access] += row.calls
    return dict(totals)


# --- Recorded executions, read by the exercised-or-unproven gate (basicly-irrm) ---


def surface_executions(repo_root: Path) -> dict[str, int]:
    """Calls per measured surface, keyed as the release gate's evidence map wants them.

    Two keys per surface — ``"<binary> <subcommand>"`` (``br show``, ``bv
    --robot-next``) and the bare binary — because the two answer different questions and
    both are asked. A frozen-surface question is about the pair; the
    exercised-or-unproven gate (:func:`basicly.release.unexercised_capabilities`) asks
    whether an *executable* ever ran, and this ledger is the committed half of that
    evidence: a machine that never typed ``br`` in a shell has no counter for it, so
    reading only the counters would refuse a release over a capability this ledger
    proves ran.

    Empty rather than None for an unrecorded repo. The gate's None — *no ledger at all,
    so nothing is proven* — is a judgement over both ledgers, so the caller that reads
    both makes it; this half cannot tell "never recorded" from "recorded nothing", and
    inventing the distinction here would put two authorities on it.

    :func:`summarize` already resolves ledger versus spool and the worktree redirect, so
    this adds a key shape and nothing else.
    """
    counts: dict[str, int] = {}
    for row in summarize(repo_root):
        for key in (f"{row.binary} {row.subcommand}", row.binary):
            counts[key] = counts.get(key, 0) + row.calls
    return counts
