from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_kit_board_server import cli, client, ledger

__all__ = ["client", "ledger"]

if TYPE_CHECKING:
    from tests.kit_board_client import Client


def test_summary_search_finds_canonical_context_and_history_without_loading_it(
    client: Client, ledger: Path
) -> None:
    status, made = client.call(
        "POST",
        "/api/v1/records",
        {
            "title": "A concise card",
            "description": "## Context\n\nA cobalt-widget must be easy to find.",
            "acceptance": "- The card appears in search.",
            "fields": {"issue_type": "task", "priority": 2},
        },
    )
    assert status == 201
    record = made["record"]
    before_refusal = cli.events.read_events(ledger)[0]
    status, refused = client.call(
        "POST",
        f"/api/v1/records/{record}/comments",
        {"text": "archived-saffron evidence " + "detail " * 1000},
    )
    assert status == 422 and "shorten" in str(refused) and "artifact" in str(refused)
    assert cli.events.read_events(ledger)[0] == before_refusal
    for _ in range(2):
        assert (
            client.call(
                "POST",
                f"/api/v1/records/{record}/comments",
                {"text": "archived-saffron evidence " + "detail " * 500},
            )[0]
            == 200
        )
    before = cli.events.read_events(ledger)[0]
    for query in ("cobalt-widget", "ARCHIVED-SAFFRON"):
        status, report = client.call("GET", f"/api/v1/records?summary=1&query={query}")
        assert status == 200 and [row["record"] for row in report["records"]] == [record]
        row = report["records"][0]
        assert row["fields"]["title"] == "A concise card"
        assert "description" not in row["fields"] and "comments" not in row
    _, full = client.call("GET", f"/api/v1/records/{record}")
    assert "cobalt-widget" in full["fields"]["description"]
    assert any("archived-saffron" in comment["text"] for comment in full["comment_log"])
    assert cli.events.read_events(ledger)[0] == before


def test_search_flag_like_text_is_data_and_ambiguous_filters_are_refused(
    client: Client, ledger: Path
) -> None:
    before = cli.events.read_events(ledger)[0]
    status, report = client.call("GET", "/api/v1/records?query=--help&summary=1")
    assert status == 200 and report["records"] == []
    assert client.call("GET", "/api/v1/records?summary=wrong")[0] == 400
    assert client.call("GET", "/api/v1/records?query=first&query=second")[0] == 400
    assert cli.events.read_events(ledger)[0] == before
