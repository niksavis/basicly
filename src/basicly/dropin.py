from __future__ import annotations

import dataclasses
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FRAGMENT_DIR = "basicly.d"

RATCHET_SECTION = "ratchet"
COUNT_DELTA = "count_delta"

BASE_COMMIT = "base_commit"

REBASELINED = "rebaselined"
REASON = "rebaseline_reason"

MAY_ONLY_FALL = "fall"
MAY_ONLY_TRACK = "track"


class FragmentError(Exception):
    pass


@dataclass(frozen=True)
class Baseline[Number: (int, float)]:
    frozen: dict[str, Number]
    count: int
    rebaselined: dict[str, tuple[str, ...]] = dataclasses.field(default_factory=dict)


def fragment_paths(repo_root: Path) -> tuple[Path, ...]:
    directory = repo_root / FRAGMENT_DIR
    return tuple(sorted(directory.glob("*.toml"))) if directory.is_dir() else ()


def documents(repo_root: Path) -> dict[str, dict]:

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

    found: list[tuple[str, dict]] = []
    for name, data in documents(repo_root).items():
        section = data.get(RATCHET_SECTION)
        table = section.get(gate) if isinstance(section, dict) else None
        if isinstance(section, dict) and isinstance(table, dict):
            _refuse_stale_measurement(repo_root, name, gate, section.get(BASE_COMMIT))
            found.append((name, table))
    return found


def _refuse_stale_measurement(repo_root: Path, name: str, gate: str, base: object) -> None:

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
    entries = table.get(key, {})
    if not isinstance(entries, dict):
        raise FragmentError(f"{name}: [{RATCHET_SECTION}.{gate}.{key}] must be a table")
    return entries


def _require_reason(name: str, gate: str, table: dict, entry: str) -> None:
    reason = table.get(REASON)
    if not isinstance(reason, str) or not reason.strip():
        raise FragmentError(
            f"{name}: [{RATCHET_SECTION}.{gate}.{REBASELINED}] raises {entry!r}, so the same "
            f"table must declare a non-empty {REASON}"
        )


def _refuse_loosening(name: str, gate: str, entry: str, moved: Any, recorded: Any) -> None:

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

    permitted = (int, float) if fractional else (int,)
    if not isinstance(value, permitted) or isinstance(value, bool):
        kind = "a numeric" if fractional else "an integer"
        raise FragmentError(
            f"{name}: [{RATCHET_SECTION}.{gate}] {key} must be {kind} delta, "
            f"got {value!r} — a fragment records what it changed, never a new total"
        )
    return value
