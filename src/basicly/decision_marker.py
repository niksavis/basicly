"""The recorded form of a queue item: the comment marker it is written as.

One responsibility, and it is the round trip. A decision has no side-state — it
exists only as ``[harness-decision]`` comments on the bead it blocks — so this
module is where that wire format is defined, written, and read back.
:func:`render_enqueue` and :func:`render_answer` are the only writers,
:func:`items_by_id` the only reader, and they sit together for the reason
:mod:`basicly.plan_record` states about its own pair: a reader in one module and
a writer in another eventually disagree, and here the disagreement is a
permanently unanswerable item.

The format's two constraints are not incidental. The header line is
whitespace-delimited ``key=value`` fields, so an attribution carrying a space
could smuggle an ``id=`` that redirects an answer to another item and a newline
could corrupt the header/payload split — hence :data:`BY_TOKEN`. And an id is
``<issue>#<hash>`` with ``#`` rather than a dot because bead ids contain dots.

Reading is best-effort throughout: a garbled header, an unparsable payload, or an
answer whose text is not a string is skipped, never raised. One malformed comment
must not wedge the read of a bead's whole queue.

Split out of ``decisions`` when the module-size ratchet caught that module
growing. The boundary is *recorded form* against *queue behaviour*: nothing here
decides whether an item may be enqueued, who may answer it, or what an answer
costs — :mod:`basicly.decisions` does all of that — which is why this module
needs no import back into it.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .br import read_comments

# Comment marker carrying one queue item (or its answer) — first line is the
# machine-readable header, the JSON payload follows on the next line.
MARKER = "[harness-decision]"

# What kind of judgment the item asks for. All are human-required by default;
# the supervisor may route delegable kinds through the decider first (7.1).
KINDS = ("needs-input", "escalation", "checkpoint", "stall", "validate")

# Separator between the bead id and the content hash in a decision id. A dot
# would be ambiguous — bead ids contain dots (basicly-kjc5.4).
_ID_SEP = "#"

# Attribution values land on the marker header line, so they must be a single
# strict token: whitespace would smuggle extra header fields (an `id=` here
# redirects the answer to another item), and a newline would corrupt the
# header/payload split and wedge the item as silently unanswered.
BY_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass(frozen=True)
class DecisionItem:
    """One queue item, parsed back from its markers on the bead."""

    decision_id: str  # <issue>#<hash6> — stable and content-derived
    issue_id: str
    kind: str
    question: str
    detail: str = ""
    answer: str | None = None
    answered_by: str | None = None
    # The tracker's stamp on the enqueue marker: when the queue started holding
    # this item, and so when the wait for its answer began (basicly-kjc5.51).
    # Empty on the item :func:`basicly.decisions.enqueue` just wrote — the tracker
    # stamps the comment, so only an item read back from ``br`` carries it.
    queued_at: str = ""

    @property
    def pending(self) -> bool:
        """True while no answer marker has been recorded for this id."""
        return self.answer is None


def decision_id_for(issue_id: str, kind: str, question: str, generation: int = 1) -> str:
    """The stable, content-derived id an (issue, kind, question) item gets.

    *generation* > 1 names a re-opened item: the same fact blocked again after
    an earlier answer, so it needs a fresh, separately-answerable id.
    """
    digest = hashlib.sha256(f"{kind}:{question}".encode()).hexdigest()[:10]
    suffix = digest if generation == 1 else f"{digest}-{generation}"
    return f"{issue_id}{_ID_SEP}{suffix}"


def split_decision_id(decision_id: str) -> tuple[str, str]:
    """Split a decision id into (issue_id, hash); raises on a malformed id."""
    issue_id, sep, digest = decision_id.rpartition(_ID_SEP)
    if not sep or not issue_id or not digest:
        raise ValueError(f"malformed decision id {decision_id!r}; expected <issue>{_ID_SEP}<hash>")
    return issue_id, digest


def render_enqueue(decision_id: str, kind: str, question: str, detail: str) -> str:
    """The comment text that puts an item on the queue."""
    payload = json.dumps({"question": question, "detail": detail}, sort_keys=True)
    return f"{MARKER} id={decision_id} kind={kind}\n{payload}"


def render_answer(
    decision_id: str,
    text: str,
    *,
    by: str,
    rationale: str | None = None,
    confidence: float | None = None,
) -> str:
    """The comment text that answers *decision_id*, attributed to *by*.

    *rationale* and *confidence* are omitted when absent rather than written as
    nulls, so a human's answer stays a two-key payload and only the decider's
    carries the audit trail it is judged on (design 7.1).

    Caller-side validation of *by* is deliberate: :func:`basicly.decisions.answer`
    rejects a bad attribution before it looks the item up, so the caller sees "that
    is not a valid answerer" rather than "no such decision".
    """
    body: dict[str, object] = {"answer": text}
    if rationale:
        body["rationale"] = rationale
    if confidence is not None:
        body["confidence"] = confidence
    return f"{MARKER} id={decision_id} answered by={by}\n{json.dumps(body, sort_keys=True)}"


def _marker_parts(text: str) -> tuple[dict[str, str], list[str], dict] | None:
    """The (header fields, header tokens, JSON payload) of one marker, or None.

    Best-effort like the sibling marker parsers: a malformed header or payload
    is skipped, never raised — a garbled item must not wedge the queue read.
    """
    stripped = text.strip()
    if not stripped.startswith(MARKER):
        return None
    lines = stripped.splitlines()
    tokens = lines[0].split()[1:]
    fields = dict(token.split("=", 1) for token in tokens if "=" in token)
    if _ID_SEP not in fields.get("id", ""):
        return None
    try:
        payload = json.loads("\n".join(lines[1:]) or "{}")
    except json.JSONDecodeError:
        return None
    return (fields, tokens, payload) if isinstance(payload, dict) else None


def _parse_marker(
    text: str, issue_id: str, created_at: str = ""
) -> DecisionItem | tuple[str, str, str] | None:
    """Parse one comment: a DecisionItem, an (id, by, answer) tuple, or None.

    *created_at* is the tracker's stamp on the comment, carried onto an enqueue
    marker as the item's :attr:`DecisionItem.queued_at`.
    """
    parts = _marker_parts(text)
    if parts is None:
        return None
    fields, tokens, payload = parts
    decision_id = fields["id"]
    if "answered" in tokens:
        answer_text = payload.get("answer")
        if not isinstance(answer_text, str):
            return None
        return (decision_id, fields.get("by", "unknown"), answer_text)
    kind = fields.get("kind", "")
    question = payload.get("question")
    if kind not in KINDS or not isinstance(question, str) or not question.strip():
        return None
    detail = payload.get("detail")
    return DecisionItem(
        decision_id=decision_id,
        issue_id=issue_id,
        kind=kind,
        question=question.strip(),
        detail=detail.strip() if isinstance(detail, str) else "",
        queued_at=created_at,
    )


def items_by_id(repo_root: Path, issue_id: str) -> dict[str, DecisionItem]:
    """All items recorded on one bead, answers folded in, keyed by decision id.

    Markers are read in tracker (oldest-first) order and the first of each id
    wins, so a re-enqueued duplicate cannot displace the item whose stamp the
    wait meter measures from, and only the first answer is folded in.
    """
    items: dict[str, DecisionItem] = {}
    answers: dict[str, tuple[str, str]] = {}
    for comment in read_comments(repo_root, issue_id):
        parsed = _parse_marker(
            str(comment.get("text", "")), issue_id, str(comment.get("created_at", ""))
        )
        if parsed is None:
            continue
        if isinstance(parsed, DecisionItem):
            items.setdefault(parsed.decision_id, parsed)
        else:
            answers.setdefault(parsed[0], (parsed[1], parsed[2]))
    for decision_id, (by, text) in answers.items():
        item = items.get(decision_id)
        if item is not None and item.pending:
            items[decision_id] = DecisionItem(
                decision_id=item.decision_id,
                issue_id=item.issue_id,
                kind=item.kind,
                question=item.question,
                detail=item.detail,
                answer=text,
                answered_by=by,
                queued_at=item.queued_at,
            )
    return items
