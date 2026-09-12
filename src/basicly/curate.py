from __future__ import annotations

import json
from pathlib import Path

from . import handoff

_OPEN = "{"
_CLOSE = "}"


def payload_from_reply(text: str, issue_id: str) -> dict | None:

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
