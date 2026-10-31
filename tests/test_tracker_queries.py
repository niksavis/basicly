from __future__ import annotations

from pathlib import Path

import pytest

from basicly import owned_store


@pytest.mark.parametrize("child_status", ["open", "in_progress", "deferred", "closed"])
def test_only_unfinished_children_hold_a_parent(tmp_path: Path, child_status: str) -> None:
    queries = owned_store.packaged_kit("queries")
    graph = owned_store.packaged_kit("record_graph")
    events = queries.events
    drafts = []
    for record, status in (
        ("demo-root", "open"),
        ("demo-parent", "open"),
        ("demo-done", "closed"),
        ("demo-child", child_status),
    ):
        drafts.extend([
            events.Draft(
                record,
                events.KIND_CREATED,
                {
                    "title": record,
                    "issue_type": "task",
                    "size": "small",
                    "description": "## Context\nPick the next task.",
                    "acceptance_criteria": "- When ready, then show the task.",
                },
            ),
            events.Draft(record, events.KIND_STATUS, {"status": status}),
        ])
    for child, parent in (
        ("demo-parent", "demo-root"),
        ("demo-done", "demo-parent"),
        ("demo-child", "demo-parent"),
    ):
        drafts.append(events.Draft(child, "edge", {"to": parent, "type": "parent-child"}))
    events.append(tmp_path, drafts)
    expected = child_status == "closed"
    ready = {row["record"] for row in queries.ready(tmp_path)["records"]}
    blocked = {row["record"]: row for row in queries.blocked(tmp_path)["records"]}
    assert ("demo-parent" in ready) is expected
    assert ("demo-parent" not in blocked) is expected
    if not expected:
        assert blocked["demo-parent"]["children"] == ["demo-child"]
    views, children = queries.views_and_children(tmp_path)
    assert children["demo-parent"] == ["demo-child", "demo-done"]
    shown = graph.Graph(views, queries.folded(tmp_path))
    assert shown.startable("demo-parent") is expected
    assert shown.plan("demo-root")["waves"][0][0]["startable"] is expected
    assert [row["id"] for row in shown.plan("demo-parent")["done"]] == (
        ["demo-child", "demo-done"] if expected else ["demo-done"]
    )
