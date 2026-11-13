from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess  # nosec B404
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from basicly import owned_store, owned_write, tracker_query
from tests.test_kit_vault_share import bare
from tests.test_kit_vault_view import _carry, _push
from tests.tracker_process_fixture import recorded_review, review_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
CLI = KIT / "cli.py"
LEDGER = ".basicly/ledger"
VAULT_PREFIX = "acme"
OTHER_PREFIX = "other"
TRIGGER = "When a team shares a backlog, I want to see who holds a story, so I can avoid it."
SHAPED = ["--description", TRIGGER, "--acceptance", "- it is seen", "--requirements", "- stdlib"]
__all__ = ["bare"]


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load("tracker_cli_vault_cli_wiring", "cli.py")
vault_write = cli.commands.vault_write
events = cli.events
vault_claim = vault_write.vault_claim
vault_clone = vault_write.vault_clone
vault_config = vault_write.vault_config
vault_queue = vault_write.vault_queue
vault_share = vault_claim.vault_share


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    markers = (*vault_write.writers.AGENT_MARKERS, vault_write.writers.CLAUDE_CODE_MARKER)
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "XDG_STATE_HOME", *markers):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s1")


def _repo(tmp_path: Path, vault: Path) -> Path:
    root = tmp_path / "repo"
    (root / LEDGER).mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    _configure(root, vault)
    return root


def _configure(root: Path, vault: Path) -> None:
    text = vault_config.text(str(vault), VAULT_PREFIX)
    (root / vault_config.CONFIG_PATH).write_text(text, encoding="utf-8")


def _kit(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *argv],
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )


def _report(done: subprocess.CompletedProcess) -> Any:
    assert done.stdout, done.stderr
    return json.loads(done.stdout)


def _clone(tmp_path: Path, vault: Path) -> Any:
    return vault_clone.locate(tmp_path / "state", str(vault))


def _share(tmp_path: Path, vault: Path) -> None:
    vault_share.share(_clone(tmp_path, vault), tmp_path / "state", plan=vault_share.Plan(time.time))


def _hold_the_share_slot(tmp_path: Path, vault: Path) -> Any:
    clone = _clone(tmp_path, vault)
    vault_clone.private(clone.folder)
    (clone.folder / vault_share.SHARED_AT).write_text(f"{time.time()}\n", encoding="utf-8")
    return clone


def _queued_ledger_lines(clone: Any) -> list[dict]:
    lines = vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)
    return [line for line in lines if line["kind"] == vault_write.LEDGER_KIND]


def _published(tmp_path: Path, vault: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, str]:
    source = tmp_path / "source-ledger"
    made: dict[str, str] = {}
    for prefix in (VAULT_PREFIX, OTHER_PREFIX):
        cli.main(["create", str(source), "--prefix", prefix, "--title", prefix, *SHAPED])
        made[prefix] = json.loads(capsys.readouterr().out)["record"]
        recorded_review(cli, source, made[prefix])
    capsys.readouterr()
    found = [json.loads(events.to_json(e)) for e in events.read_events(source)[0]]
    _push(tmp_path, vault, "m1", _carry(found))
    return made


def _rows(report: dict[str, Any]) -> set[str]:
    return {str(row["record"]) for row in report["records"]}


def _engine(root: Path, monkeypatch: pytest.MonkeyPatch, *argv: str) -> argparse.Namespace:
    monkeypatch.chdir(root)
    parser = argparse.ArgumentParser()
    tracker_query.add_parsers(parser.add_subparsers(dest="command", required=True))
    return parser.parse_args(argv)


def _engine_report(
    root: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *argv: str,
) -> tuple[int, Any]:
    args = _engine(root, monkeypatch, *argv)
    code = tracker_query.HANDLERS[args.command](args)
    return code, json.loads(capsys.readouterr().out)


@pytest.mark.parametrize("verb", ["list", "ready"])
def test_the_kit_reads_the_prefix_view_and_every_project_with_all_projects(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str], verb: str
) -> None:
    made = _published(tmp_path, bare, capsys)
    root = _repo(tmp_path, bare)

    mine = _kit(root, verb, LEDGER)
    every = _kit(root, verb, LEDGER, "--all-projects")

    assert mine.returncode == 0, mine.stdout + mine.stderr
    assert every.returncode == 0, every.stdout + every.stderr
    assert _rows(_report(mine)) == {made[VAULT_PREFIX]}
    assert _rows(_report(every)) == set(made.values())


@pytest.mark.parametrize("verb", ["list", "ready"])
def test_the_engine_reads_the_prefix_view_and_every_project_with_all_projects(
    tmp_path: Path,
    bare: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    verb: str,
) -> None:
    made = _published(tmp_path, bare, capsys)
    root = _repo(tmp_path, bare)

    mine = _engine_report(root, monkeypatch, capsys, verb, *(["--json"] if verb == "ready" else []))
    every = _engine_report(
        root, monkeypatch, capsys, verb, "--all-projects", *(["--json"] if verb == "ready" else [])
    )

    assert mine[0] == every[0] == 0
    assert _rows(mine[1]) == {made[VAULT_PREFIX]}
    assert _rows(every[1]) == set(made.values())


def test_all_projects_without_a_vault_is_refused_by_name(tmp_path: Path, bare: Path) -> None:
    root = _repo(tmp_path, bare)
    (root / vault_config.CONFIG_PATH).unlink()

    done = _kit(root, "list", LEDGER, "--all-projects")

    assert done.returncode == cli.EXIT_REFUSED
    assert str(vault_config.CONFIG_PATH) in _report(done)["refused"]


def _shown(record: str, holder: object) -> list[dict[str, object]]:
    return [
        {"id": record, "status": "open", "holder": holder, "links": []},
        {"id": f"{VAULT_PREFIX}-zz", "found": False},
    ]


def test_show_json_prints_the_vault_view_show_list_from_the_kit_and_the_engine(
    tmp_path: Path,
    bare: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = _published(tmp_path, bare, capsys)
    root = _repo(tmp_path, bare)
    record = made[VAULT_PREFIX]

    found = _kit(root, "show", LEDGER, record, "--json")
    absent = _kit(root, "show", LEDGER, f"{VAULT_PREFIX}-zz", "--json")
    engine = _engine_report(root, monkeypatch, capsys, "show", record, "--json")

    assert found.returncode == 0, found.stdout + found.stderr
    assert _report(found) == _shown(record, None)[:1]
    assert absent.returncode == cli.EXIT_REFUSED
    assert _report(absent) == _shown(record, None)[1:]
    assert engine == (0, _shown(record, None)[:1])


def test_show_json_without_a_vault_is_refused_by_name(tmp_path: Path, bare: Path) -> None:
    root = _repo(tmp_path, bare)
    (root / vault_config.CONFIG_PATH).unlink()

    done = _kit(root, "show", LEDGER, f"{VAULT_PREFIX}-zz", "--json")

    assert done.returncode == cli.EXIT_REFUSED
    assert str(vault_config.CONFIG_PATH) in _report(done)["refused"]


def test_create_takes_the_prefix_from_the_vault_config_when_the_ledger_names_none(
    tmp_path: Path, bare: Path
) -> None:
    root = _repo(tmp_path, bare)
    clone = _hold_the_share_slot(tmp_path, bare)

    done = _kit(root, "create", LEDGER, "--title", "vault born")

    assert done.returncode == 0, done.stdout + done.stderr
    assert _report(done)["record"].startswith(f"{VAULT_PREFIX}-")
    assert {line["record"] for line in _queued_ledger_lines(clone)} == {_report(done)["record"]}


def test_a_refused_vault_claim_exits_with_the_vault_code(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, bare)
    record = f"{VAULT_PREFIX}-a1"

    landed = _kit(root, "claim", LEDGER, record)
    yours = _kit(root, "claim", LEDGER, record)
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s2")
    lost = _kit(root, "claim", LEDGER, record)
    nowhere = tmp_path / "not-a-vault"
    nowhere.mkdir()
    _configure(root, nowhere)
    unreachable = _kit(root, "claim", LEDGER, record)

    assert landed.returncode == 0, landed.stdout + landed.stderr
    assert yours.returncode == vault_claim.YOURS == 7
    assert lost.returncode == vault_claim.LOST == 3
    assert _report(lost)["holder"]["session"] == "s1"
    assert unreachable.returncode == vault_claim.UNREACHABLE == 5
    assert _report(unreachable)["check"].startswith("git fetch ")


def test_an_unsettled_vault_claim_exits_4(
    tmp_path: Path,
    bare: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    root = _repo(tmp_path, bare)
    record = f"{VAULT_PREFIX}-a1"
    unsettled = {"exit": vault_claim.UNSETTLED, "record": record, "unsettled": "fixture"}
    monkeypatch.setattr(vault_claim, "claim", lambda _ask, **_options: dict(unsettled))
    monkeypatch.chdir(root)

    code = cli.main(["claim", LEDGER, record])

    assert code == vault_claim.UNSETTLED == 4
    assert json.loads(capsys.readouterr().out)["unsettled"] == "fixture"


def test_unassign_in_a_vault_repository_releases_through_the_vault_with_a_session(
    tmp_path: Path, bare: Path
) -> None:
    root = _repo(tmp_path, bare)
    record = f"{VAULT_PREFIX}-a1"
    assert _kit(root, "claim", record).returncode == 0
    _share(tmp_path, bare)

    lost = _kit(root, "unassign", record, "--session", "s2")
    given = _kit(root, "unassign", record, "--session", "s1")
    _share(tmp_path, bare)

    assert lost.returncode == vault_claim.LOST, lost.stdout + lost.stderr
    assert given.returncode == 0, given.stdout + given.stderr
    assert vault_claim.holder(_clone(tmp_path, bare), record) is None


def test_the_ledger_forms_still_parse_and_write_through_the_vault(
    tmp_path: Path, bare: Path
) -> None:
    root = _repo(tmp_path, bare)
    (root / vault_config.CONFIG_PATH).unlink()
    created = _kit(root, "create", LEDGER, "--prefix", VAULT_PREFIX, "--title", "local")
    record = _report(created)["record"]
    found = [json.loads(events.to_json(event)) for event in events.read_events(root / LEDGER)[0]]
    _push(tmp_path, bare, "fixture-publisher", _carry(found))
    _configure(root, bare)
    clone = _hold_the_share_slot(tmp_path, bare)
    before = sorted((root / LEDGER).iterdir())

    noted = _kit(root, "comment", LEDGER, record, "a note through the vault")
    queued = [line[vault_write.LEDGER_FIELD]["kind"] for line in _queued_ledger_lines(clone)]
    claimed = _kit(root, "claim", LEDGER, record)
    held = vault_claim.holder(clone, record)
    released = _kit(root, "unassign", LEDGER, record)
    checked = _kit(root, "fsck", LEDGER)
    _share(tmp_path, bare)

    for done in (noted, claimed, released, checked):
        assert done.returncode == 0, done.stdout + done.stderr
    assert queued == [events.KIND_NOTE]
    assert held is not None
    assert held[1] == "s1"
    assert vault_claim.holder(clone, record) is None
    assert sorted((root / LEDGER).iterdir()) == before


def test_the_vault_comment_form_still_parses(tmp_path: Path, bare: Path) -> None:
    root = _repo(tmp_path, bare)
    created = _kit(root, "create", LEDGER, "--title", "A real vault comment target")
    assert created.returncode == 0, created.stdout + created.stderr
    record = _report(created)["record"]

    done = _kit(root, "comment", record, "--text", "a vault note")

    assert done.returncode == 0, done.stdout + done.stderr


def _owned_repo(tmp_path: Path) -> Path:
    root = tmp_path / "engine"
    target = root / owned_store.KIT_TRACKER_DIR
    target.mkdir(parents=True)
    for source in sorted(KIT.glob("*.py")):
        shutil.copy2(source, target / source.name)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _review_evidence(kit: Any, ledger: Path, record: str) -> str:
    found = kit.events.read_events(ledger)[0]
    state = kit.events.fold(found).records[record]
    reference = max(
        event.seq
        for event in found
        if event.record == record and event.kind in kit.events.PROSE_KINDS
    )
    return json.dumps(review_payload(kit.review.process.criteria(state.fields), reference))


def _engine_evidence(commands: Any, ledger: Path, record: str, verb: str) -> str:
    review = _review_evidence(commands, ledger, record)
    if verb == "review":
        return review
    payload = json.loads(review)
    commands.record_process(ledger, record, payload)
    commands.comment(ledger, record, "The fixture confirms the seeded criterion.")
    checks = [
        {
            "criterion": check["criterion"],
            "command": check["command"],
            "result": check["expected"],
            "exit_code": 0,
        }
        for check in payload["checks"]
    ]
    return json.dumps({"checks": checks})


@pytest.mark.parametrize("verb", ["review", "confirm"])
def test_engine_review_and_confirm_in_a_vault_repository_queue_through_the_vault(
    tmp_path: Path, bare: Path, verb: str
) -> None:
    root = _owned_repo(tmp_path)
    ledger = owned_store.ledger_dir(root)
    commands = owned_store.kit(root, "commands")
    fields = {
        "title": "reviewed",
        "description": TRIGGER,
        "acceptance_criteria": "- it is seen",
        "requirements": "- stdlib",
    }
    record = commands.create_root(ledger, fields, prefix=VAULT_PREFIX)[0].record
    commands.comment(ledger, record, "The fixture agrees each criterion and its check.")
    evidence = _engine_evidence(commands, ledger, record, verb)
    found = [json.loads(events.to_json(event)) for event in events.read_events(ledger)[0]]
    _push(tmp_path, bare, "fixture-publisher", _carry(found))
    _configure(root, bare)
    clone = _hold_the_share_slot(tmp_path, bare)
    before = {path.name: path.read_bytes() for path in ledger.glob("*.jsonl")}

    stamped, _landed = owned_write.append(root, [verb, record, "--evidence", evidence])

    assert stamped
    assert {line["record"] for line in _queued_ledger_lines(clone)} == {record}
    assert {path.name: path.read_bytes() for path in ledger.glob("*.jsonl")} == before
