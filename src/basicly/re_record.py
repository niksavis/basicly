from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import tracker_argv, tracker_usage
from basicly.owned_store import TrackerDivergenceError

if TYPE_CHECKING:
    from collections.abc import Sequence


def read_the_seams_own_flags(args: Sequence[str]) -> tuple[list[str], bool]:

    surface, _ = tracker_usage.split_invocation(list(args))
    if unreadable := tracker_argv.unreadable_flags(surface, args):
        raise TrackerDivergenceError(
            f"{surface} reads nothing from {', '.join(unreadable)}, so it would record what "
            f"the rest of {' '.join(args)} says and drop that silently. The flags {surface} "
            f"reads: {', '.join(sorted(tracker_argv.GUARDED_FLAGS[surface]))}"
        )
    kept = tracker_argv.without_flags(args, {tracker_argv.REPEAT_FLAG}, ())
    return kept, len(kept) != len(args)
