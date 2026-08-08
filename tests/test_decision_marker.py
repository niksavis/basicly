"""Tests for the recorded form of a queue item (basicly-kjc5.4, design 7.1/7.3).

The round trip is the subject: what :func:`render_enqueue` writes is what
:func:`items_by_id` must read back, and everything else on the bead — a human's
comment, a marker from a newer or a broken writer — has to be skipped rather than
raised, because one garbled comment must not wedge the read of a whole bead's
queue.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import br, decision_marker

if TYPE_CHECKING:
    import pytest


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    """br stand-in answering ``comments list`` from a seeded per-issue list."""

    def __init__(self) -> None:
        self.comments: dict[str, list[str]] = {}

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            return _Proc(json.dumps([{"text": text, "created_at": ""} for text in texts]))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    monkeypatch.setattr(br, "run_br", fake)
    monkeypatch.setattr(br, "try_run_br", fake)


def test_garbled_markers_never_wedge_the_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Malformed headers/payloads and foreign comments are skipped, not raised.

    The one well-formed item is written by :func:`render_enqueue` rather than
    hand-spelled, so this is also the round-trip control: were the writer and the
    reader to disagree about the header, the real item would be dropped with the
    junk and this would fail rather than pass an emptier queue.
    """
    fake = _FakeBr()
    _install(monkeypatch, fake)
    real_id = decision_marker.decision_id_for("epic.1", "needs-input", "which db?")
    fake.comments["epic.1"] = [
        "plain comment",
        "[harness-decision] id=nosep kind=needs-input\n{}",
        '[harness-decision] id=epic.1#aaa kind=vibe\n{"question": "q"}',
        "[harness-decision] id=epic.1#bbb kind=needs-input\nnot json",
        '[harness-decision] id=epic.1#ccc answered by=human\n{"answer": 42}',
        decision_marker.render_enqueue(real_id, "needs-input", "which db?", ""),
    ]

    items = decision_marker.items_by_id(tmp_path, "epic.1")

    assert [item.decision_id for item in items.values() if item.pending] == [real_id]
