from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

from basicly import tracker_usage, write_verbs
from basicly.owned_store import TrackerDivergenceError

UNMIRRORED_WRITES = frozenset({"init", "sync"})


MIRROR_PROVENANCE = write_verbs.MIRROR_PROVENANCE

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


_ECHO_PLACEHOLDER = json.dumps({"id": "unminted", "status": write_verbs.CREATED_STATUS})
