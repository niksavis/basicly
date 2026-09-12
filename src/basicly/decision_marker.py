from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

from .tracker import read_comments

MARKER = "[harness-decision]"

KINDS = ("needs-input", "escalation", "checkpoint", "stall", "validate")

_ID_SEP = "#"

BY_TOKEN = re.compile(r"^[A-Za-z0-9._:-]+$")


@dataclass(frozen=True)
class DecisionItem:
    decision_id: str
    issue_id: str
    kind: str
    question: str
    detail: str = ""
    answer: str | None = None
    answered_by: str | None = None
    queued_at: str = ""

    @property
    def pending(self) -> bool:
        return self.answer is None


def decision_id_for(issue_id: str, kind: str, question: str, generation: int = 1) -> str:

    digest = hashlib.sha256(f"{kind}:{question}".encode()).hexdigest()[:10]
    suffix = digest if generation == 1 else f"{digest}-{generation}"
    return f"{issue_id}{_ID_SEP}{suffix}"


def split_decision_id(decision_id: str) -> tuple[str, str]:
    issue_id, sep, digest = decision_id.rpartition(_ID_SEP)
    if not sep or not issue_id or not digest:
        raise ValueError(f"malformed decision id {decision_id!r}; expected <issue>{_ID_SEP}<hash>")
    return issue_id, digest


def render_enqueue(decision_id: str, kind: str, question: str, detail: str) -> str:
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

    body: dict[str, object] = {"answer": text}
    if rationale:
        body["rationale"] = rationale
    if confidence is not None:
        body["confidence"] = confidence
    return f"{MARKER} id={decision_id} answered by={by}\n{json.dumps(body, sort_keys=True)}"


def _marker_parts(text: str) -> tuple[dict[str, str], list[str], dict] | None:

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
