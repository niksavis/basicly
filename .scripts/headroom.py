"""What a change may still add to a module before either size ratchet refuses it.

Two ratchets bound a module and they pull opposite ways: `module-size` bounds its tokens,
`comment-density` bounds its prose share, so extracting code fixes the first and worsens the
second. Measuring one feels like having measured, and the handover said "measure headroom
before writing prose" in plain words — it was still paid for three times in one 2026-08-14
session, every time by an agent who had measured tokens and not prose (basicly-co64):

============  ==================================================================
basicly-jn1x  `runner.py` 59.9% against a frozen 59.8, two rounds of trimming
              prose *after* the gate refused
basicly-c357  `differential.py` had +3 tokens **and** 0.0 points; the placement
              had to be redesigned into a new module
basicly-ejdm  `lane_split.py` landed at 56.5% against the 50% cap, on a new file
============  ==================================================================

**Not a gate.** Both ratchets already bind at commit time; this is the read that lets a
change be sized *before* it is written, which is the only point at which the information is
cheap. It exits 0 whatever it finds.

**Both halves come from the gates that own them** — `check_module_size.tracked_modules` and
`check_comment_density.tracked_modules`, imported as sensors the way
`improvement_controller.py` imports the first of them. So the answer is what the gates will
say at commit time, waivers and frozen baselines and the prose floor included, rather than a
third measurement free to disagree with both.

Run::

    uv run python .scripts/headroom.py src/basicly/cli.py
    uv run python .scripts/headroom.py
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path[:0] = [str(SCRIPTS_DIR), str(REPO_ROOT / "src")]

import check_comment_density as prose  # noqa: E402 - the paths above come first
import check_module_size as size  # noqa: E402 - the paths above come first
from ratchet import SCOPE_ROOTS, RatchetError  # noqa: E402 - the paths above come first

_LABEL = "headroom"

# What one unit's edit actually adds to one module, so what counts as no room left. Measured
# over this repo's own rebaseline records in `basicly.d` (2026-08-21): 44 `module_size`
# declarations, median 614.5 tokens rounded up; 5 `comment_density` declarations, largest
# 0.9 points. A
# module with less room than this is one ordinary unit away from refusing the next edit.
TIGHT_TOKENS = 615
TIGHT_POINTS = 1.0


@dataclass(frozen=True)
class Headroom:
    """What one module may still add before either ratchet refuses it.

    A ``None`` limit is a module waived on that gate, which has no bound. A ``None`` *share*
    is a module under `comment-density`'s token floor, which that gate does not measure.
    """

    path: str
    tokens: int
    token_limit: int | None
    share: float | None
    share_limit: float | None


def measure(repo: Path, cap: int = size.SCOPE_FILE_READ_CAP) -> list[Headroom]:
    """Both ratchets' room for every tracked module in *repo*, ordered by path.

    Raises:
        RatchetError: a baseline is unreadable, or git refused to list the tree.
    """
    tokens = size.load_ratchet(repo)
    shares = prose.load_ratchet(repo)
    measured = {module.path: module for module in prose.tracked_modules(repo)}
    rooms = []
    for module in size.tracked_modules(repo):
        seen = measured.get(module.path)
        rooms.append(
            Headroom(
                path=module.path,
                tokens=module.tokens,
                token_limit=None if module.waiver else tokens.frozen.get(module.path, cap),
                share=None if seen is None else seen.share,
                share_limit=None
                if seen is None or seen.waiver
                else shares.frozen.get(module.path, prose.CAP),
            )
        )
    return rooms


def is_tight(room: Headroom, tokens: int = TIGHT_TOKENS, points: float = TIGHT_POINTS) -> bool:
    """Whether an ordinary unit's edit would take *room* past either of its bounds."""
    if room.token_limit is not None and room.token_limit - room.tokens <= tokens:
        return True
    return (
        room.share is not None
        and room.share_limit is not None
        and room.share_limit - room.share <= points
    )


def render(room: Headroom) -> str:
    """One module's line: both bounds, each with what is left under it."""
    if room.token_limit is None:
        tokens = f"{room.tokens} tokens (waived)"
    else:
        tokens = f"{room.tokens}/{room.token_limit} tokens ({room.token_limit - room.tokens} left)"
    if room.share is None:
        share = f"under the {prose.MIN_TOKENS}-token prose floor"
    elif room.share_limit is None:
        share = f"{room.share}% prose (waived)"
    else:
        share = (
            f"{room.share}/{room.share_limit}% prose "
            f"({round(room.share_limit - room.share, 1)} left)"
        )
    return f"{room.path}: {tokens}; {share}"


def _named(rooms: Iterable[Headroom], paths: Sequence[str]) -> int:
    """Print the room for each of *paths*, refusing one that names no tracked module.

    Refusing rather than reporting a full cap's worth of room: a mistyped path is the one
    input whose fail-open answer ("plenty of room") is the answer the caller wants to hear.
    """
    found = {room.path: room for room in rooms}
    for path in paths:
        if path in found:
            print(f"{_LABEL}: {render(found[path])}")
    missing = [path for path in paths if path not in found]
    for path in missing:
        print(
            f"{_LABEL}: {path}: no tracked module in scope ({', '.join(SCOPE_ROOTS)}); "
            "`git add` a new one first",
            file=sys.stderr,
        )
    return 1 if missing else 0


def _tree(rooms: Sequence[Headroom]) -> int:
    """Print every module an ordinary edit would take past a bound, and how many that is."""
    tight = [room for room in rooms if is_tight(room)]
    for room in tight:
        print(f"{_LABEL}: {render(room)}")
    print(
        f"{_LABEL}: {len(tight)} of {len(rooms)} tracked modules are within "
        f"{TIGHT_TOKENS} tokens or {TIGHT_POINTS} point of a bound"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: both ratchets' room, for the named modules or for the whole tree."""
    parser = argparse.ArgumentParser(
        description="Report a module's room under both size ratchets, before writing to it."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="repo-relative modules to report; with none, every module close to a bound",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="the checkout to measure (default: this script's repository)",
    )
    args = parser.parse_args(argv)

    try:
        rooms = measure(args.repo)
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1
    return _named(rooms, args.paths) if args.paths else _tree(rooms)


if __name__ == "__main__":
    sys.exit(main())
