from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import decision_marker
from tests import fake_tracker

if TYPE_CHECKING:
    import pytest


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class _FakeBr:
    def __init__(self) -> None:
        self.comments: dict[str, list[str]] = {}

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "list"]:
            texts = self.comments.get(args[2], [])
            return _Proc(json.dumps([{"text": text, "created_at": ""} for text in texts]))
        raise AssertionError(f"unexpected br call: {args}")


def _install(monkeypatch: pytest.MonkeyPatch, fake: _FakeBr) -> None:
    fake_tracker.install(monkeypatch, fake)


def test_garbled_markers_never_wedge_the_queue(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

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
