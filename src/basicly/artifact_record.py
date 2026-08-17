"""How a handoff artifact is written down on a bead and read back.

One responsibility, and it is the recorded form: render a payload as the one marker line
that carries it, find that line again among a unit's markers, and decode it. Nothing here
knows what an artifact must contain or whether a state may accept one — that is
:mod:`basicly.handoff`'s, and the boundary is *recorded form* against *judgement*, the
same cut :mod:`basicly.plan_record` was split from :mod:`basicly.plan_gate` on. Split out
of ``handoff`` when the module-size ratchet caught it crossing the cap.

The transport is the ``[harness-artifact]`` marker family, a sibling of
``[harness-policy]``, ``[harness-run]`` and ``[harness-classification]``, written and read
through the same ``tracker.add_comment``/``tracker.read_comments`` seam they all use — so an
artifact lands in ``br`` on today's rung and becomes a ledger ``comment`` event the moment
``[tracker] mode`` flips to ``owned``. ``handoff``'s module docstring states why that seam
rather than a new ledger event kind, and the argv-length bound it carries below ``owned``.

**One family, and the kind is a field.** A reader asking "what did this unit hand on" asks
one question rather than one per artifact name, and a seventh artifact adds a kind rather
than a marker name that has to be kept in step with this module.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import tracker

# The marker family this module owns, and how the kind is carried ahead of the payload.
MARKER = "[harness-artifact]"
_KIND_PREFIX = "kind="


def marker_body(kind: str, payload: dict) -> str:
    """One artifact as the marker line that carries it (pure).

    Compact separators and sorted keys: the body is compared for equality to keep the
    write idempotent, so two runs recording the same facts have to render one string.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{MARKER} {_KIND_PREFIX}{kind} {encoded}"


def recorded_payload(text: str, kind: str) -> object | None:
    """What a marker line of *kind* carries, or None when *text* is not one (pure).

    A line of the right kind whose payload is not JSON returns the **raw string** rather
    than None. That is the difference between *no artifact* and *a corrupted one*, and
    only the second may refuse a state: the schema then reports a type violation naming
    the top-level instance, which is a reason an operator can act on.
    """
    if not text.startswith(MARKER):
        return None
    rest = text[len(MARKER) :].strip()
    if not rest.startswith(_KIND_PREFIX):
        return None
    kind_field, _, encoded = rest[len(_KIND_PREFIX) :].partition(" ")
    if kind_field != kind:
        return None
    try:
        return json.loads(encoded)
    except ValueError:
        return encoded


def read(repo_root: Path, issue_id: str, kind: str) -> object | None:
    """The artifact of *kind* recorded on *issue_id*, or None when it carries none.

    The **last** matching marker wins. A unit re-decomposed under a changed plan records a
    second artifact, and the plan BUILD is held to is the one that produced the children
    now on the tracker, not the one that was superseded.

    Reads through the hard seam (:func:`tracker.read_comments`), which raises rather than
    reporting an empty history: this answer feeds a refusal, and a tracker that did not
    answer must not read as "no artifact, carry on".

    Raises:
        RuntimeError: the store could not answer.
    """
    found = None
    for comment in tracker.read_comments(repo_root, issue_id):
        payload = recorded_payload(str(comment.get(tracker.COMMENT_TEXT_KEY, "")).strip(), kind)
        if payload is not None:
            found = payload
    return found


def write(repo_root: Path, issue_id: str, kind: str, payload: dict) -> None:
    """Record *payload* on *issue_id* as the *kind* artifact, once.

    Idempotent on the whole body, like ``classify``'s marker: a state is re-entered on
    every advance until its checkpoint clears, and one marker per attempt would bury the
    artifact under its own copies.

    Records whatever it is handed. Validation is the caller's and happens first — see
    :func:`handoff.record`, which is the only caller and the reason this one is not
    tempted to rule on what it writes.

    Raises:
        RuntimeError: the marker did not reach the authoritative store.
    """
    body = marker_body(kind, payload)
    if any(
        str(comment.get(tracker.COMMENT_TEXT_KEY, "")).strip() == body
        for comment in tracker.read_comments(repo_root, issue_id)
    ):
        return
    tracker.add_comment(repo_root, issue_id, body)
