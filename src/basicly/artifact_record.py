"""How a handoff artifact is written down on a bead and read back.

One responsibility, and it is the recorded form: hand a payload to the store, find that
artifact again on a unit, and decode the retired marker form where that is all a unit has.
Whether a state may accept one is :mod:`basicly.handoff`'s, so the boundary is *recorded
form* against *judgement*.

The transport is one ``artifact`` event, kind as a typed field and body under a payload key
the per-event cap does not name (D-36, :func:`tracker.add_artifact`). It was a
``[harness-artifact]`` comment marker until `basicly-pp7q4i`, and that cost 31 of the 54
artifacts written under it: a marker body is free text, free text is cut at 4096 bytes, and
JSON cut mid-token is not JSON.

**The marker stays a reader.** Its 44 rows are on disk, the log is never rewritten, and the
cut bodies cannot be recovered, so a unit carrying only a marker still resolves to one, and
:func:`cut_violation` is what the 31 refuse through — :func:`handoff.entry_verdict` reports a
truncated body rather than silence.

Nothing bounds the new body by size. D-36 bounds an artifact by taking out what the ledger
can already derive instead, which `basicly-gvlpxm` applied to the changed-path list.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import comment_rows, tracker

# The retired family this module still reads. Declared here because
# `.scripts/check_marker_families.py` reconciles what `src/basicly/` declares against what
# the store holds, and 44 rows hold this one.
MARKER = "[harness-artifact]"
_KIND_PREFIX = "kind="


def recorded_payload(text: str, kind: str) -> object | None:
    """What a marker line of *kind* carries, or None when *text* is not one (pure).

    A line of the right kind whose payload is not JSON returns the **raw string** rather
    than None. That is the difference between *no artifact* and *a corrupted one*, and only
    the second may refuse a state: the schema then reports a type violation naming the
    top-level instance, which is a reason an operator can act on.
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


def _marker_payload(repo_root: Path, issue_id: str, kind: str) -> object | None:
    """The *kind* artifact off *issue_id*'s retired markers, or None when it carries none.

    The **last** matching marker wins: a unit re-decomposed under a changed plan recorded a
    second one, and BUILD is held to the plan that produced the children now on the tracker.

    Raises:
        RuntimeError: the store could not answer. Never an empty history, which would read
            as "no artifact, carry on" — and this answer feeds a refusal.
    """
    found = None
    for comment in tracker.read_comments(repo_root, issue_id):
        payload = recorded_payload(str(comment.get(tracker.COMMENT_TEXT_KEY, "")).strip(), kind)
        if payload is not None:
            found = payload
    return found


def cut_violation(repo_root: Path, issue_id: str, kind: str, payload: object) -> str | None:
    """Why *payload* is unusable when the marker cap cut it, or None when it was stored whole.

    Public because the reason is a fact about the recorded form and the refusal is not: only
    this module knows a body was cut. Only the retired transport can produce one — an
    ``artifact`` event's body is never cut, so this answers None for everything recorded since
    `basicly-pp7q4i`, and 31 stored markers are why it still runs. The row is found by content
    rather than by re-selecting the last, so this cannot come to disagree with :func:`read`
    about which row it describes; :func:`handoff.entry_verdict` reaches it only after a
    refusal, which is what makes a second fold of the ledger affordable. Both sizes go in the
    reason because the pair is what separates a body the transport destroyed from one a
    producer malformed.
    """
    for row in tracker.read_comments(repo_root, issue_id):
        if comment_rows.TRUNCATED_KEY not in row:
            continue
        stored = str(row.get(tracker.COMMENT_TEXT_KEY, ""))
        if recorded_payload(stored.strip(), kind) != payload:
            continue
        # Unstripped: the cap measured the whole stored field, so the pair of sizes is
        # only comparable against the same bytes it counted.
        return (
            "the recorded body was truncated by the event text cap to "
            f"{len(stored.encode('utf-8'))} bytes of {row[comment_rows.ORIGINAL_LENGTH_KEY]} "
            "and cannot be recovered from the append-only log; re-record the artifact "
            "from the producing state"
        )
    return None


def read(repo_root: Path, issue_id: str, kind: str) -> object | None:
    """The artifact of *kind* recorded on *issue_id*, or None when it carries none.

    The event answers first. Nothing writes a marker any more, so a unit holding both was
    re-recorded through the event; reading the marker first would hand BUILD back the
    truncated body this order exists to stop serving.

    Raises:
        RuntimeError: the store could not answer.
    """
    recorded = tracker.read_artifacts(repo_root, issue_id).get(kind)
    if recorded is not None:
        return recorded
    return _marker_payload(repo_root, issue_id, kind)


def write(repo_root: Path, issue_id: str, kind: str, payload: dict) -> None:
    """Record *payload* on *issue_id* as the *kind* artifact, once.

    Kept as this module's half of the pair so producer and consumer name one seam, and
    `test_curate` patches it to prove an invalid record is never written. Idempotence is the
    store's — an event whose content-derived id the ledger already holds is skipped — so a
    state re-entered on every advance records one.

    Records whatever it is handed. Validation is the caller's and happens first; see
    :func:`handoff.record`, the only caller.

    Raises:
        RuntimeError: the artifact did not reach the authoritative store.
    """
    tracker.add_artifact(repo_root, issue_id, kind, payload)
