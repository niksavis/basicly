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

from __future__ import annotations

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


class FragmentError(Exception):
    """A fragment could not be read, or holds something where a number belongs."""


@dataclass(frozen=True)
class Baseline[Number: (int, float)]:
    """A ratchet's recorded state with every fragment's contribution applied."""

    frozen: dict[str, Number]
    count: int


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


def compose[Number: (int, float)](
    repo_root: Path,
    gate: str,
    *,
    frozen: Mapping[str, Number],
    count: int,
    fractional: bool = False,
) -> Baseline[Number]:
    """*gate*'s recorded baseline with every fragment's deltas added to it.

    *gate* is named as ``[tool.<gate>]`` in ``pyproject.toml`` spells it, and *frozen* and
    *count* are what that table records. An entry whose deltas bring it to zero is dropped
    rather than recorded as ``0``, which is the rule those tables already state for a debt
    that has been paid off.

    Set *fractional* when the entries are shares rather than counts, as
    ``comment_density``'s are; ``count_delta`` counts entries and stays whole either way.
    ``basicly.d/README.md`` says why it is a parameter and not read off *frozen*.

    Raises:
        FragmentError: A fragment declares a delta of the wrong kind.
    """
    composed: dict[str, Number] = dict(frozen)
    total = count
    for name, table in _ratchet_tables(repo_root, gate):
        total += _delta(name, gate, table.get(COUNT_DELTA, 0), COUNT_DELTA, fractional=False)
        for entry, value in _frozen_table(name, gate, table).items():
            composed[entry] = composed.get(entry, 0) + _delta(
                name, gate, value, entry, fractional=fractional
            )
    return Baseline({key: value for key, value in composed.items() if value != 0}, total)


def _ratchet_tables(repo_root: Path, gate: str) -> list[tuple[str, dict]]:
    """Each fragment's ``[ratchet.<gate>]`` table, with the fragment it came from."""
    found: list[tuple[str, dict]] = []
    for name, data in documents(repo_root).items():
        section = data.get(RATCHET_SECTION)
        table = section.get(gate) if isinstance(section, dict) else None
        if isinstance(table, dict):
            found.append((name, table))
    return found


def _frozen_table(name: str, gate: str, table: dict) -> dict:
    """The fragment's per-entry deltas, refusing anything that is not a table."""
    frozen = table.get("frozen", {})
    if not isinstance(frozen, dict):
        raise FragmentError(f"{name}: [{RATCHET_SECTION}.{gate}.frozen] must be a table")
    return frozen


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
