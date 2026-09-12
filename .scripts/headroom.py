from __future__ import annotations

import argparse
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parent
sys.path[:0] = [str(SCRIPTS_DIR), str(REPO_ROOT / "src")]

import check_module_size as size  # noqa: E402 - the paths above come first
from ratchet import SCOPE_ROOTS, RatchetError  # noqa: E402 - the paths above come first

_LABEL = "headroom"

TIGHT_TOKENS = 615


@dataclass(frozen=True)
class Headroom:
    path: str
    tokens: int
    token_limit: int | None


def measure(repo: Path, cap: int = size.SCOPE_FILE_READ_CAP) -> list[Headroom]:
    tokens = size.load_ratchet(repo)
    return [
        Headroom(
            path=module.path,
            tokens=module.tokens,
            token_limit=None if module.waiver else tokens.frozen.get(module.path, cap),
        )
        for module in size.tracked_modules(repo)
    ]


def is_tight(room: Headroom, tokens: int = TIGHT_TOKENS) -> bool:
    return room.token_limit is not None and room.token_limit - room.tokens <= tokens


def render(room: Headroom) -> str:
    if room.token_limit is None:
        return f"{room.path}: {room.tokens} tokens (waived)"
    left = room.token_limit - room.tokens
    return f"{room.path}: {room.tokens}/{room.token_limit} tokens ({left} left)"


def _named(rooms: Iterable[Headroom], paths: Sequence[str]) -> int:
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
    tight = [room for room in rooms if is_tight(room)]
    for room in tight:
        print(f"{_LABEL}: {render(room)}")
    print(
        f"{_LABEL}: {len(tight)} of {len(rooms)} tracked modules are within "
        f"{TIGHT_TOKENS} tokens of a bound"
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report a module's room under the size ratchet, before writing to it."
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
