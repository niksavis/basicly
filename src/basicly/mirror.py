"""Which tracker writes have an owned-ledger translation, and the refusal for the rest.

The boundary is *which verbs are translatable* against *what a verb means*, which is
:mod:`basicly.write_verbs`. Nothing here touches a file.
"""

# comment-density-waiver: cohesion: 53.4% of 845 tokens, because the code left. This module is now a
# dispatch table and one function: the nine translations moved to `write_verbs` under
# basicly-5m2xfd, taking 3000 tokens of code and their own docstrings with them, so the
# denominator fell and the contract did not. What remains is the frozen-surface argument in
# `drafts` and a ruff `D`-mandated `Raises:` block. Merging back is not available - 3721 + 845
# crosses the 4000 cap. Seventh instance in one pass of the mechanism basicly-e2r08j records.

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from basicly import tracker_usage, write_verbs
from basicly.owned_store import TrackerDivergenceError

# Writes that carry no record fact, so there is nothing to translate. Named rather than
# defaulted to "skip", because the default for an unrecognised write is a refusal — see
# :func:`drafts`. The ledger needs no equivalent of either: it *is* the committed
# artifact (git is its transport, §4) and `events.append` creates its directory.
UNMIRRORED_WRITES = frozenset({"init", "sync"})


# The nine translations live in `write_verbs`, which knows nothing about dispatch. Named here
# rather than imported by `from`, so one object serves every consumer as before the split.
MIRROR_PROVENANCE = write_verbs.MIRROR_PROVENANCE

# The record-write surface, as the translation each one takes. A dispatch table rather
# than a chain of comparisons so the mirrored set is *readable as a set* — it is the
# thing a reviewer has to check against the measured surface, and a branch buried in a
# function body is not.
_MIRRORED_WRITES: dict[str, Callable[[Any, Sequence[str], str], list[object]]] = {
    "close": write_verbs._close_drafts,
    "comments add": write_verbs._comment_drafts,
    "create": write_verbs._create_drafts,
    "dep add": write_verbs._dep_drafts,
    "dep remove": write_verbs._dep_remove_drafts,
    "gate report": write_verbs._gate_drafts,
    "update": write_verbs._update_drafts,
}


def drafts(kit_module: Any, args: Sequence[str], stdout: str) -> list[object]:
    """The owned-ledger drafts recording the same fact *args* just wrote to br.

    Empty for a read and for the two writes that state nothing about a record. Every
    other write is translated, and one this function does not know **raises**: the
    surface was frozen by measurement (`basicly.tracker_usage`), so a write outside it
    is a new dependency on br that nobody decided to take, and the mirror is the only
    place that can still see it before the two stores drift.

    Raises:
        TrackerDivergenceError: *args* is a write with no owned-ledger translation.
    """
    surface, _ = tracker_usage.split_invocation(list(args))
    if tracker_usage.classify_access(surface) == "read" or surface in UNMIRRORED_WRITES:
        return []
    translate = _MIRRORED_WRITES.get(surface)
    if translate is None:
        raise TrackerDivergenceError(
            f"{surface!r} is not a write this tracker knows how to record; the verbs that "
            f"are: {', '.join(sorted(_MIRRORED_WRITES))}. Add a translation to "
            f"mirror._MIRRORED_WRITES, or list it in mirror.UNMIRRORED_WRITES if it "
            f"states nothing about a record"
        )
    return translate(kit_module, args, stdout)


# What a `br create` echoes back, faked so :func:`refuse_untranslatable` can run the
# real translator before the real echo exists. Only `_create_drafts` reads the echo,
# and only for the minted id, which no argv can carry.
_ECHO_PLACEHOLDER = json.dumps({"id": "unminted", "status": write_verbs.CREATED_STATUS})
