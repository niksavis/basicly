"""The curator's reply, as the validated ``release-record`` a shipped unit carries.

SHIP's judgement step: bind every claim the release makes to its evidence, and name the
ones that have none rather than dropping them quietly. The writer of a claim is the wrong
context to audit it, which is why a separate role answers this at all.

Nothing here dispatches — :mod:`basicly.loop` owns that and hands the reply down — and
nothing here fails a ship, because the package has already merged.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import handoff

# The curator is asked for one JSON object and usually fences it in prose, so the object
# is located rather than assumed — a strict parse of the whole reply refuses that answer.
_OPEN = "{"
_CLOSE = "}"


def payload_from_reply(text: str, issue_id: str) -> dict | None:
    """The ``release-record`` payload in *text*, or None when it carries none (pure).

    None is a stated answer: an empty artifact would assert that a release makes no
    claims. ``schema_version`` and ``issue`` are supplied rather than asked of the model,
    so a mistyped one cannot turn a judgement failure into a schema failure.
    """
    start = text.find(_OPEN)
    end = text.rfind(_CLOSE)
    if start < 0 or end < start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    payload["schema_version"] = handoff.SCHEMA_VERSION
    payload["issue"] = issue_id
    return payload


def record(repo_root: Path, issue_id: str, reply: str) -> str:
    """Record the curator's *reply* as *issue_id*'s release record; say what happened.

    One clause for the ship's detail line, never a raised error. Bound, refused and not
    attempted have to be distinguishable, because silence reads as the first.
    """
    if not handoff.adopted(repo_root, handoff.RELEASE_RECORD):
        return ""
    payload = payload_from_reply(reply, issue_id)
    if payload is None:
        return "the curator bound no claims"
    try:
        handoff.record(repo_root, issue_id, handoff.RELEASE_RECORD, payload)
    except (handoff.ArtifactError, RuntimeError) as exc:
        return f"the release record was refused: {exc}"
    bound = len(payload.get("claims", ()))
    dropped = len(payload.get("unsupported", ()))
    return f"release record: {bound} claim(s) bound, {dropped} unsupported"
