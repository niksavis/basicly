from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basicly import (
    board_assets,
    board_cli,
    board_facts,
    board_schema,
    cli,
    decisions,
    loop_state,
    policy,
    supervise,
    tracker,
)
from tests.board_offline import assert_offline

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "board"
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _run(argv: list[str], where: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.chdir(where)
    return cli.main(argv)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "probe")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _owned_repo(root: Path, *records: str) -> Path:

    (root / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, root / tracker.KIT_TRACKER_DIR / source.name)
    (root / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (root / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    kit = tracker.kit(root)
    kit.events.append(
        tracker.ledger_dir(root),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return root


def _lock(root: Path, root_issue: str) -> None:
    lock = root / supervise.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "session_id": "abc", "root_issue": root_issue}))


def test_the_help_carries_the_frozen_freshness_claim_and_never_the_word_it_refuses(
    capsys: pytest.CaptureFixture[str],
) -> None:

    with pytest.raises(SystemExit) as exit_info:
        cli.main(["board", "--help"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert board_cli.FRESHNESS in " ".join(printed.split())
    assert "real-time" not in printed
    assert "real time" not in printed


def test_the_group_still_answers_validate_exactly_as_it_did_before_the_move(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name, expected in (
        ("minimal-v1.json", 0),
        ("wrong-major.json", board_schema.REFUSED),
        ("broken-section-v1.json", board_schema.PARTLY_RENDERABLE),
    ):
        assert _run(["board", "validate", str(FIXTURES / name)], REPO_ROOT, monkeypatch) == expected
        assert board_schema.VERSION in capsys.readouterr().out or expected == board_schema.REFUSED


def test_an_unreadable_snapshot_path_is_reported_rather_than_raised(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert _run(["board", "validate", "no-such-file.json"], REPO_ROOT, monkeypatch) == 1
    assert board_schema.UNREADABLE in capsys.readouterr().out


def test_the_group_registers_validate_and_leaves_the_artifact_on_no_subcommand() -> None:

    parsed = cli._build_parser().parse_args(["board", "--out", "b.html"])
    assert parsed.board_command is None
    assert board_cli._HANDLERS[None] is board_cli.cmd_emit
    assert set(board_cli._HANDLERS) == {None, "serve", "validate"}


def test_the_command_group_lives_in_its_own_module_and_cli_keeps_only_the_wiring() -> None:
    source = (REPO_ROOT / "src" / "basicly" / "cli.py").read_text(encoding="utf-8")
    assert "board_cli.add_parsers(subparsers)" in source
    assert '"board": board_cli.cmd_board,' in source
    assert "def cmd_board" not in source
    assert "board_schema" not in source


def test_mode_a_writes_the_page_and_the_contract_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    out = tmp_path / "nested" / "board.html"
    assert _run(["board", "--out", str(out)], REPO_ROOT, monkeypatch) == 0
    printed = capsys.readouterr().out

    page = out.read_text(encoding="utf-8")
    assert board_schema.VERSION in page
    assert_offline(page)

    sidecar = out.parent / board_cli.SNAPSHOT_NAME
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    assert document["schema"] == board_schema.VERSION
    assert board_schema.verdict(REPO_ROOT, document).readable

    assert "snapshot built in" in printed
    assert str(out) in printed and str(sidecar) in printed
    assert "vendored stylesheet" in printed
    assert (out.parent / board_assets.DIRNAME / board_assets.STYLESHEET).is_file()


def test_mode_a_refuses_to_write_a_page_with_no_destination(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:

    assert _run(["board"], REPO_ROOT, monkeypatch) == 2
    assert "--out is required" in capsys.readouterr().err


def test_the_caller_supplies_every_derivation_the_producer_refuses(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path, "bd-1", "bd-2")
    policy.record_wait_request(repo, "bd-1", "ship")
    decisions.enqueue(
        repo, "bd-1", "checkpoint", "approve the ship checkpoint for bd-1?", human_required=False
    )
    config = loop_state.load_policy_config(repo)
    lights_out = policy.PolicyConfig(required_gates=("verify",), max_rework=2, autonomy="L3")
    granted = policy.issue_grant_guarded(
        repo, "bd-1", "L2", 8_000_000, lights_out, interactive=True
    )
    assert granted.status == "approved"
    _lock(repo, "bd-1")

    document: dict[str, Any] = board_facts.document(repo)
    rows = {row["id"]: row for row in document["units"]}

    assert rows["bd-1"]["phase"] == loop_state.read_node_state(repo, "bd-1", config).phase
    assert rows["bd-1"]["ready"] is True
    assert document["backlog"]["ready"] == 2
    assert document["asks"][0]["wait_id"] == "bd-1#wait-ship"
    assert document["asks"][0]["question"] == "approve the ship checkpoint for bd-1?"
    assert document["asks"][0]["waiting_s"] >= 0
    assert document["session"]["grant_level"] == "L2"
    assert document["session"]["token_budget"] == 8_000_000
    assert "spent_tokens" not in document["session"]
    assert board_schema.verdict(REPO_ROOT, document).exit_code == 0


def test_a_checkout_with_no_tracker_costs_the_derivations_and_not_the_page(
    tmp_path: Path,
) -> None:

    assert board_facts.readiness(tmp_path) is None
    assert board_facts.phases(tmp_path) == {}
    assert board_facts.questions(tmp_path, {"asks": "not a list"}) == {}
    assert set(board_facts.document(tmp_path)) == {
        "schema",
        "generated_at",
        "freshness",
        "generator",
        "repo",
        "lanes",
    }
