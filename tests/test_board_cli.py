"""The `basicly board` surface: the grammar that moved, and the artifact Mode A writes.

The grammar half exists because an extraction that changes the surface is not an extraction.
`board validate` is a wired `[[verify.checks]]` entry, so its exit codes and its output are a
contract this move had to carry unchanged; the assertions here are the ones that would have
caught a `required=True` becoming `required=False` in the wrong direction.

The artifact half asserts what a consumer gets: two files, a page that fetches nothing, and a
sidecar that is the contract rather than a copy of the page. `test_board_render.py` owns what
is *inside* the page - this module owns that it was written, where, and with what said about it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import board_cli, board_schema, cli, supervise

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "board"


def _run(argv: list[str], where: Path, monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.chdir(where)
    return cli.main(argv)


def test_the_help_carries_the_frozen_freshness_claim_and_never_the_word_it_refuses(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The overclaim guard, in both directions.

    C1's operative rule is that a live-attached mode "displays the snapshot's age and never
    uses the word real-time", so the absence is asserted as well as the claim - a surface that
    spends the word in order to deny it has still spent it, and the denial is what erodes.
    """
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
    """The three exit codes `board-schema` and any script around it branch on."""
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
    """A missing file is a report and exit 1, never a traceback at a consumer."""
    assert _run(["board", "validate", "no-such-file.json"], REPO_ROOT, monkeypatch) == 1
    assert board_schema.UNREADABLE in capsys.readouterr().out


def test_the_group_registers_validate_and_leaves_the_artifact_on_no_subcommand() -> None:
    """The None route is a registered handler, not a fallthrough.

    `basicly board --out X` is the documented Mode A invocation, so this group's subparser is
    optional where every other one in `cli.py` is required. Registered rather than defaulted,
    because a future subcommand with no handler must fail loudly instead of writing a page.
    """
    parsed = cli._build_parser().parse_args(["board", "--out", "b.html"])
    assert parsed.board_command is None
    assert board_cli._HANDLERS[None] is board_cli.cmd_emit
    assert set(board_cli._HANDLERS) == {None, "serve", "validate"}


def test_the_command_group_lives_in_its_own_module_and_cli_keeps_only_the_wiring() -> None:
    """The extraction, asserted where it can regress: `cli.py` naming a board handler again."""
    source = (REPO_ROOT / "src" / "basicly" / "cli.py").read_text(encoding="utf-8")
    assert "board_cli.add_parsers(subparsers)" in source
    assert '"board": board_cli.cmd_board,' in source
    assert "def cmd_board" not in source
    assert "board_schema" not in source


def test_mode_a_writes_the_page_and_the_contract_beside_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The consumer transcript, on this repo: two files, and the sidecar is the document.

    Run against the real checkout because that is the only place a producer has a ledger to
    fold; what is asserted is the shape of the output, never a count this tree happens to hold.
    """
    out = tmp_path / "nested" / "board.html"
    assert _run(["board", "--out", str(out)], REPO_ROOT, monkeypatch) == 0
    printed = capsys.readouterr().out

    page = out.read_text(encoding="utf-8")
    assert board_schema.VERSION in page
    assert "<script" not in page and "<link" not in page

    sidecar = out.parent / board_cli.SNAPSHOT_NAME
    document = json.loads(sidecar.read_text(encoding="utf-8"))
    assert document["schema"] == board_schema.VERSION
    assert board_schema.verdict(REPO_ROOT, document).readable

    assert "snapshot built in" in printed
    assert str(out) in printed and str(sidecar) in printed
    assert "self-contained" in printed


def test_mode_a_refuses_to_write_a_page_with_no_destination(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No default path, on purpose.

    A command that writes a file into the current directory unasked is a command that has
    already written one somewhere a consumer did not look.
    """
    assert _run(["board"], REPO_ROOT, monkeypatch) == 2
    assert "--out is required" in capsys.readouterr().err


def test_the_session_section_is_omitted_rather_than_guessed(tmp_path: Path) -> None:
    """A root invented here would be a claim about which pass is running, drawn on a wall."""
    assert board_cli._session_facts(tmp_path) is None


def test_a_held_lock_supplies_the_session_facts_the_producer_may_not_derive(tmp_path: Path) -> None:
    """The C11 inversion, exercised: this layer reads the lock and passes the facts down.

    The producer may not read it itself - the import would close
    `supervise -> board_snapshot -> supervise`, since the supervisor emits a snapshot too.
    """
    lock = tmp_path / supervise.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "session_id": "abc", "root_issue": "x-1"}))
    facts = board_cli._session_facts(tmp_path)
    assert facts is not None
    assert facts.root_issue == "x-1"
    assert facts.session_id == "abc"
    assert facts.supervised is True
    assert facts.stale is False
