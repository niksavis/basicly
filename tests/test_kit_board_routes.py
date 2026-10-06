from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.kit_deployment_helpers import REPO_ROOT, _load
from tests.tracker_process_fixture import review_payload

if TYPE_CHECKING:
    import pytest

routes = _load(REPO_ROOT / ".basicly/core/kit/board/routes.py", "board_process_routes")
cli = routes.tracker_cli()


def _request(
    ledger: Path, method: str, path: str, body: object, capsys: pytest.CaptureFixture[str]
) -> tuple[int, dict]:
    result = cli.main(routes.write_argv(ledger, method, routes.API + path, body))
    return result, json.loads(capsys.readouterr().out)


def test_api_claim_and_completed_close_require_shared_process_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    status, saved = _request(
        ledger,
        "POST",
        "/records",
        {
            "prefix": "demo",
            "title": "save a task",
            "description": "When a task arrives, I want it saved, so I can find the work later.",
            "acceptance": "When a task is saved, show shall return its title.",
            "requirements": "Use the existing ledger.",
        },
        capsys,
    )
    assert status == cli.EXIT_OK
    record = saved["record"]
    path = f"/records/{record}"
    before = cli.events.read_events(ledger)[0]
    assert _request(ledger, "POST", path + "/claim", {"to": "agent"}, capsys)[0] == cli.EXIT_REFUSED
    assert (
        _request(ledger, "POST", path + "/close", {"reason": "Saved titles are readable."}, capsys)[
            0
        ]
        == cli.EXIT_REFUSED
    )
    assert (
        _request(
            ledger,
            "PATCH",
            path,
            {"status": "closed", "fields": {"close_reason": "Saved titles are readable."}},
            capsys,
        )[0]
        == cli.EXIT_REFUSED
    )
    assert cli.events.read_events(ledger)[0] == before
    _request(
        ledger,
        "POST",
        path + "/comments",
        {"text": "Agree to confirm the saved title through show."},
        capsys,
    )
    found = cli.events.read_events(ledger)[0]
    reference = max(event.seq for event in found if event.kind in cli.events.PROSE_KINDS)
    review = review_payload(("When a task is saved, show shall return its title.",), reference)
    assert (
        _request(ledger, "POST", path + "/review", {"evidence": review}, capsys)[0] == cli.EXIT_OK
    )
    assert _request(ledger, "POST", path + "/claim", {"to": "agent"}, capsys)[0] == cli.EXIT_OK
    checks = [
        {
            "criterion": check["criterion"],
            "command": check["command"],
            "result": "The saved title appears.",
            "exit_code": 0,
        }
        for check in review["checks"]
    ]
    assert (
        _request(ledger, "POST", path + "/confirm", {"evidence": {"checks": checks}}, capsys)[0]
        == cli.EXIT_OK
    )
    assert (
        _request(ledger, "POST", path + "/close", {"reason": "Saved titles are readable."}, capsys)[
            0
        ]
        == cli.EXIT_OK
    )
