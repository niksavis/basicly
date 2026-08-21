"""The per-lane drop-in fragments that compose into this repo's shared landing anchors.

A lane that would otherwise append to a shared anchor — a ``[[verify.checks]]`` entry in
``basicly.toml``, a number in one of ``pyproject.toml``'s ratchet tables — writes
``basicly.d/<bead-id>.toml`` instead, so two lanes cannot write one file and the collision
is impossible rather than detected (basicly-ef7t, applying basicly-4746 to the two anchors
that bounced three of five lanes on 2026-08-08). ``basicly.d/README.md`` is the convention.

Every ratchet number in a fragment is a **delta**, never a total, and that is what makes the
split work instead of moving the conflict one file along: two lanes each adding one ``S603``
suppression both measure the tree-wide total as 16, so both record 16, and the merged tree
holds 17 and fails a gate neither lane's rebase conflicted on. Addition is commutative, so a
composed baseline does not depend on landing order.

Stdlib only: the ratchet gates under ``.scripts/`` read this on every commit.
"""

# comment-density-waiver: cohesion: this module's payload is the convention three ratchet gates
# enforce, and every refusal in it is a rule a lane will read in a failure message rather
# than in the code - why a number here is a delta and never a total, why `fractional` is a
# parameter rather than inferred, why a raised baseline needs its own counted table, and
# (basicly-nwx4ku) why a recorded measurement base refuses on ancestry while its absence
# and git's own "cannot tell" do not. The same shape as `.scripts/ratchet.py` beside it,
# whose waiver says the payload is the rationale the gates enforce. Measured at 53.0%,
# 1839 prose tokens against 1633 of code: reaching 50% means cutting the
# absence-is-not-a-violation rationale, which is the one paragraph standing between the
# next reader and a guard that stops every lane in flight.

from __future__ import annotations

import dataclasses
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The Unix drop-in convention `changelog.d` already follows: `<name>.d` is a directory of
# fragments that compose into `<name>`.
FRAGMENT_DIR = "basicly.d"

# The section a fragment declares its ratchet contributions in. `count_delta` moves a
# tree-wide total; `[ratchet.<gate>.frozen]` holds one delta per recorded entry.
RATCHET_SECTION = "ratchet"
COUNT_DELTA = "count_delta"

# The commit a fragment's measurements were taken at. Once per fragment, not once per
# gate: a lane measures the tree at one head, and a second copy of one sha is a second
# thing to keep true.
BASE_COMMIT = "base_commit"

# The one case `frozen` may not carry: a baseline that has to rise (basicly-e2mz.20). Its own
# table because the point is that it is countable — the gate prints how many came through.
REBASELINED = "rebaselined"
REASON = "rebaseline_reason"

# Which way a gate's frozen entry is allowed to move, declared by the gate because only the
# gate knows what its subject means (`.scripts/ratchet.py` states that boundary). `fall` is
# module-size and comment-density, where a raised baseline is a loosening. `track` is
# The third, whose record must *equal* the tree — it fails on "up from" and on "down from"
# alike, so a lane's plus-one there is the record staying true, not a licence.
MAY_ONLY_FALL = "fall"
MAY_ONLY_TRACK = "track"


class FragmentError(Exception):
    """A fragment could not be read, or holds something where a number belongs."""


@dataclass(frozen=True)
class Baseline[Number: (int, float)]:
    """A ratchet's recorded state with every fragment's contribution applied."""

    frozen: dict[str, Number]
    count: int
    # Entries whose baseline a fragment deliberately raised, and **every** fragment that
    # raised it. Surfaced so the gate can print the count: an unreported rebaseline is the
    # defect basicly-e2mz.20 records, where a disclosed loosening and a silent one looked the
    # same. Keyed by entry to the *last* declarer, this reported four declarations on
    # `tests/test_loop.py` as one, so the count an operator reads to judge a file's debt was
    # the count of files rather than of loosenings (basicly-wpqdag).
    rebaselined: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)


def fragment_paths(repo_root: Path) -> tuple[Path, ...]:
    """The ``.toml`` fragments, sorted by name so composition is byte-identical anywhere."""
    directory = repo_root / FRAGMENT_DIR
    return tuple(sorted(directory.glob("*.toml"))) if directory.is_dir() else ()


def documents(repo_root: Path) -> dict[str, dict]:
    """Every fragment parsed, keyed by repo-relative path, in filename order.

    Raises:
        FragmentError: A fragment is unreadable or is not TOML. Never skipped — a lane's
            declaration going quiet is the failure this directory exists to remove.
    """
    parsed: dict[str, dict] = {}
    for path in fragment_paths(repo_root):
        name = f"{FRAGMENT_DIR}/{path.name}"
        try:
            parsed[name] = tomllib.loads(path.read_text(encoding="utf-8"))
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise FragmentError(f"{name}: {exc}") from exc
    return parsed


def compose[Number: (int, float)](  # noqa: PLR0913 - reason in basicly.d/basicly-e2mz.20.toml
    repo_root: Path,
    gate: str,
    *,
    frozen: Mapping[str, Number],
    count: int,
    fractional: bool = False,
    may_only: str = MAY_ONLY_FALL,
) -> Baseline[Number]:
    """*gate*'s recorded baseline with every fragment's deltas added to it.

    *gate* is named as ``[tool.<gate>]`` in ``pyproject.toml`` spells it, and *frozen* and
    *count* are what that table records. An entry whose deltas bring it to zero is dropped
    rather than recorded as ``0``, which is the rule those tables already state for a debt
    that has been paid off.

    Set *fractional* when the entries are shares rather than counts, as
    ``comment_density``'s are; ``count_delta`` counts entries and stays whole either way.
    ``basicly.d/README.md`` says why it is a parameter and not read off *frozen*.

    *may_only* is the gate's own answer to which direction is safe — see
    :data:`MAY_ONLY_FALL`. Under ``fall`` a delta that would raise a recorded baseline, or
    create one the table does not name, is refused; the fragment declares it under
    :data:`REBASELINED` with a reason instead, and that is counted.

    Raises:
        FragmentError: A fragment declares a delta of the wrong kind, one that loosens a
            baseline outside the declared-and-counted route, or a :data:`BASE_COMMIT` this
            head does not contain.
    """
    composed: dict[str, Number] = dict(frozen)
    total = count
    rebaselined: dict[str, tuple[str, ...]] = {}
    for name, table in _ratchet_tables(repo_root, gate):
        total += _delta(name, gate, table.get(COUNT_DELTA, 0), COUNT_DELTA, fractional=False)
        for entry, value in _entry_table(name, gate, table, REBASELINED).items():
            _require_reason(name, gate, table, entry)
            composed[entry] = composed.get(entry, 0) + _delta(
                name, gate, value, entry, fractional=fractional
            )
            rebaselined[entry] = (*rebaselined.get(entry, ()), name)
        for entry, value in _entry_table(name, gate, table, "frozen").items():
            moved = _delta(name, gate, value, entry, fractional=fractional)
            if may_only == MAY_ONLY_FALL:
                _refuse_loosening(name, gate, entry, moved, frozen.get(entry))
            composed[entry] = composed.get(entry, 0) + moved
    kept = {key: value for key, value in composed.items() if value != 0}
    return Baseline(kept, total, {k: v for k, v in rebaselined.items() if k in kept})


def _ratchet_tables(repo_root: Path, gate: str) -> list[tuple[str, dict]]:
    """Each fragment's ``[ratchet.<gate>]`` table, with the fragment it came from.

    Every contributing fragment's :data:`BASE_COMMIT` is checked here, so a stale
    measurement stops only the gates that fragment actually moves.
    """
    found: list[tuple[str, dict]] = []
    for name, data in documents(repo_root).items():
        section = data.get(RATCHET_SECTION)
        table = section.get(gate) if isinstance(section, dict) else None
        if isinstance(section, dict) and isinstance(table, dict):
            _refuse_stale_measurement(repo_root, name, gate, section.get(BASE_COMMIT))
            found.append((name, table))
    return found


def _refuse_stale_measurement(repo_root: Path, name: str, gate: str, base: object) -> None:
    """Refuse a fragment whose recorded measurement base is not an ancestor of the head.

    A delta composes in any order, which is what this directory is for; the *headroom* the
    lane measured before choosing that delta does not. Two lanes branched from one commit
    each measured ``merge.py`` at exactly 2 tokens of headroom, each spent that same 2, and
    the composed tree failed a gate neither branch failed (basicly-nwx4ku). Ancestry rather
    than equality: work landing on top of a measurement does not stale it, only a base this
    head does not contain.

    Absence is not a violation — the field is hand-written, every fragment that predates it
    records none, and refusing those would stop every lane in flight. Nor is git's third
    answer: it cannot resolve a history that is not there, which is a fixture composing over
    a tree copied without its ``.git`` or a shallow clone, and failing there would be
    failing on the absence of a repository.

    Raises:
        FragmentError: *base* is present but not a commit-ish string, or is one ``HEAD``
            does not contain.
    """
    if base is None:
        return
    if not isinstance(base, str) or not base.strip():
        raise FragmentError(
            f"{name}: [{RATCHET_SECTION}] {BASE_COMMIT} must be the commit this fragment's "
            f"measurements were taken at, got {base!r}"
        )
    recorded = base.strip()
    if _is_ancestor(repo_root, recorded) is not False:
        return
    raise FragmentError(
        f"{name}: [{RATCHET_SECTION}.{gate}] was measured at {recorded}, which HEAD does not "
        f"contain, so the headroom those deltas were sized against is not this tree's. "
        f"Re-measure on this head and record the {BASE_COMMIT} you measured at"
    )


def _is_ancestor(repo_root: Path, commit: str) -> bool | None:
    """Whether *commit* is an ancestor of ``HEAD``, or None where git could not answer.

    ``git merge-base --is-ancestor`` exits 0 for yes and 1 for no. Anything else — 128 for a
    revision or a repository it cannot resolve, no git to ask — is the third answer, and it
    is returned as itself rather than folded into either verdict.

    ``which`` rather than the bare name: an unfindable git is one of the cases that has to
    answer None anyway, so resolving it here removes a second failure mode rather than
    adding one.
    """
    git = shutil.which("git")
    if git is None:
        return None
    try:
        completed = subprocess.run(  # noqa: S603 — resolved binary, literal argv, no shell
            [git, "-C", str(repo_root), "merge-base", "--is-ancestor", commit, "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return {0: True, 1: False}.get(completed.returncode)


def _entry_table(name: str, gate: str, table: dict, key: str) -> dict:
    """One of the fragment's per-entry delta tables, refusing anything that is not a table."""
    entries = table.get(key, {})
    if not isinstance(entries, dict):
        raise FragmentError(f"{name}: [{RATCHET_SECTION}.{gate}.{key}] must be a table")
    return entries


def _require_reason(name: str, gate: str, table: dict, entry: str) -> None:
    """A rebaseline says why, in the fragment, or it is refused."""
    reason = table.get(REASON)
    if not isinstance(reason, str) or not reason.strip():
        raise FragmentError(
            f"{name}: [{RATCHET_SECTION}.{gate}.{REBASELINED}] raises {entry!r}, so the same "
            f"table must declare a non-empty {REASON}"
        )


def _refuse_loosening(name: str, gate: str, entry: str, moved: Any, recorded: Any) -> None:
    """Refuse a delta that raises a recorded baseline, or invents one the table omits.

    Both are the shape ``.scripts/ratchet.py`` calls impossible — "the list is closed", and
    "an entry added there is a line in ``pyproject.toml`` that a reviewer sees". A fragment
    is neither, so the doctrine held only as long as nobody wrote one (basicly-e2mz.20).
    """
    if moved <= 0:
        return
    where = f"[{RATCHET_SECTION}.{gate}.frozen]"
    if recorded is None:
        raise FragmentError(
            f"{name}: {where} declares {entry!r} = +{moved}, which would create a baseline for "
            f"a subject the closed list does not name; bring it under the cap, or declare it "
            f"under {REBASELINED} with a {REASON}"
        )
    raise FragmentError(
        f"{name}: {where} declares {entry!r} = +{moved}, raising the recorded {recorded} to "
        f"{recorded + moved}; a frozen subject may only fall. Declare it under {REBASELINED} "
        f"with a {REASON} if the baseline genuinely has to rise"
    )


def _delta(name: str, gate: str, value: object, key: str, *, fractional: bool) -> Any:
    """*value* as a delta, refusing a bool because ``isinstance(True, int)`` is true.

    ``Any``, not ``int | float``: the union would widen a counting ratchet's composed
    baseline to a float at the type level. The refusal below is the guarantee.
    """
    permitted = (int, float) if fractional else (int,)
    if not isinstance(value, permitted) or isinstance(value, bool):
        kind = "a numeric" if fractional else "an integer"
        raise FragmentError(
            f"{name}: [{RATCHET_SECTION}.{gate}] {key} must be {kind} delta, "
            f"got {value!r} — a fragment records what it changed, never a new total"
        )
    return value
