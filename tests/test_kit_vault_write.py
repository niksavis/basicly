from __future__ import annotations

import importlib.util
import json
import os
import subprocess  # nosec B404
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_kit_vault_share import bare
from tests.tracker_process_fixture import recorded_review, review_payload

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
__all__ = ["bare"]


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


commands = _load("tracker_commands_vault_write", "commands.py")
vault_write = commands.vault_write
writers = commands.writers
events = commands.events
vault_queue = vault_write.vault_queue
vault_clone = vault_write.vault_clone
vault_claim = vault_write.vault_claim
vault_config = vault_write.vault_config
vault_share = vault_claim.vault_share
vault_shapes = vault_clone.vault_shapes
CLI = KIT / "cli.py"
SEED_PREFIX = "seed"
VAULT_PREFIX = "acme"


@dataclass(frozen=True)
class Repo:
    root: Path
    ledger: Path
    record: str
    other: str
    clone: Any
    state: Path


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    markers = (*writers.AGENT_MARKERS, writers.CLAUDE_CODE_MARKER)
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "XDG_STATE_HOME", *markers):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s1")


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)


def _seeded(tmp_path: Path, vault: Path, *, configured: bool = True) -> Repo:
    root = tmp_path / "repo"
    root.mkdir()
    _git_init(root)
    ledger = root / ".basicly" / "ledger"
    shaped = {
        "description": "When a child is ready, I want it offered, so I can pick it up.",
        "acceptance_criteria": "- the seeded record keeps its criterion",
        "requirements": "- standard library only",
    }
    record = commands.create_root(ledger, {"title": "a", **shaped}, prefix=SEED_PREFIX)[0].record
    other = commands.create_root(ledger, {"title": "b"}, prefix=SEED_PREFIX)[0].record
    commands.comment(ledger, record, "The fixture agrees each criterion and its check.")
    if configured:
        text = vault_config.text(str(vault), VAULT_PREFIX)
        (root / vault_config.CONFIG_PATH).write_text(text, encoding="utf-8")
    state = tmp_path / "state"
    return Repo(root, ledger, record, other, vault_clone.locate(state, str(vault)), state)


def _hold_the_share_slot(repo: Repo) -> None:
    vault_clone.private(repo.clone.folder)
    stamp = repo.clone.folder / vault_share.SHARED_AT
    stamp.write_text(f"{time.time()}\n", encoding="utf-8")


def _ledger_bytes(ledger: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(ledger.iterdir())
        if path.name != events.LOCK_NAME
    }


def _queued(repo: Repo) -> list[dict]:
    return vault_queue.pending(repo.clone.folder / vault_queue.QUEUE_NAME)


def _review(repo: Repo) -> dict[str, Any]:
    found = events.read_events(repo.ledger)[0]
    state = events.fold(found).records[repo.record]
    reference = max(
        event.seq
        for event in found
        if event.record == repo.record and event.kind in events.PROSE_KINDS
    )
    return review_payload(commands.review.process.criteria(state.fields), reference)


def _confirm(repo: Repo) -> list:
    recorded_review(commands, repo.ledger, repo.record)
    checks = [
        {
            "criterion": check["criterion"],
            "command": check["command"],
            "result": check["expected"],
            "exit_code": 0,
        }
        for check in _review(repo)["checks"]
    ]
    commands.comment(repo.ledger, repo.record, "The fixture confirms the seeded criterion.")
    return checks


Write = Callable[[Repo, Any], list]

WRITES: dict[str, tuple[Callable[[Repo], Any], Write]] = {
    "update": (
        lambda _r: None,
        lambda r, _s: commands.update(r.ledger, r.record, fields={"priority": 1}),
    ),
    "close": (
        lambda _r: None,
        lambda r, _s: commands.close(
            r.ledger, [r.record], reason="dropped", resolution="cancelled"
        ),
    ),
    "comment": (
        lambda _r: None,
        lambda r, _s: commands.comment(r.ledger, r.record, "a note through the vault"),
    ),
    "dep": (
        lambda _r: None,
        lambda r, _s: commands.add_dependency(r.ledger, r.record, r.other, edge_type="blocks"),
    ),
    "review": (
        _review,
        lambda r, payload: commands.record_process(r.ledger, r.record, payload),
    ),
    "confirm": (
        _confirm,
        lambda r, checks: commands.record_process(
            r.ledger, r.record, {"checks": checks}, completed=True
        ),
    ),
}


def _without_vault(repo: Repo) -> Repo:
    (repo.root / vault_config.CONFIG_PATH).unlink()
    return repo


def _with_vault(repo: Repo, vault: Path) -> Repo:
    text = vault_config.text(str(vault), VAULT_PREFIX)
    (repo.root / vault_config.CONFIG_PATH).write_text(text, encoding="utf-8")
    return repo


@pytest.mark.parametrize("verb", sorted(WRITES))
def test_a_write_with_a_vault_queues_one_ledger_line_per_event_and_leaves_the_ledger(
    tmp_path: Path, bare: Path, verb: str
) -> None:
    prepare, write = WRITES[verb]
    repo = _without_vault(_seeded(tmp_path, bare))
    given = prepare(repo)
    _with_vault(repo, bare)
    _hold_the_share_slot(repo)
    before = _ledger_bytes(repo.ledger)

    written = write(repo, given)

    lines = _queued(repo)
    assert written
    assert _ledger_bytes(repo.ledger) == before
    assert [line["kind"] for line in lines] == [vault_write.LEDGER_KIND] * len(written)
    assert [line[vault_write.LEDGER_FIELD] for line in lines] == [
        json.loads(events.to_json(event)) for event in written
    ]
    assert {line["record"] for line in lines} == {repo.record}
    assert all(line["session"] == "s1" and line["writer"] and line["id"] for line in lines)


def test_a_carried_ledger_event_parses_back_as_the_event_the_ledger_would_hold(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare)
    _hold_the_share_slot(repo)

    (written,) = commands.update(repo.ledger, repo.record, fields={"priority": 2})

    carried = _queued(repo)[0][vault_write.LEDGER_FIELD]
    parsed = events.from_json(json.dumps(carried))
    assert parsed == written
    assert (
        parsed.seq
        == max(e.seq for e in events.read_events(repo.ledger)[0] if e.record == repo.record) + 1
    )
    assert set(carried) >= {"id", "kind", "record", "payload", "actor", "ts", "seq"}


def test_create_with_a_vault_mints_the_config_prefix_and_queues_without_the_ledger(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare)
    _hold_the_share_slot(repo)
    before = _ledger_bytes(repo.ledger)

    written = commands.create_root(repo.ledger, {"title": "vault born"}, prefix=SEED_PREFIX)

    assert written[0].record.startswith(f"{VAULT_PREFIX}-")
    assert _ledger_bytes(repo.ledger) == before
    assert [line[vault_write.LEDGER_FIELD]["kind"] for line in _queued(repo)] == [
        events.KIND_CREATED,
        events.KIND_STATUS,
    ]


def test_a_queued_ledger_line_redacts_before_the_id_so_the_id_still_matches(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare)
    _hold_the_share_slot(repo)
    secret = "ghp" + "_" + "Q7v2Lm9Rk4Tz8Wp1Xc6Bn3Hs5Jd0Fg2Ya7Ue"

    commands.comment(repo.ledger, repo.record, f"the token {secret} leaked")

    carried = _queued(repo)[0][vault_write.LEDGER_FIELD]
    assert secret not in json.dumps(carried)
    assert carried["id"] == events.event_id_for(
        carried["record"], carried["kind"], carried["payload"]
    )


def test_a_shared_ledger_line_is_a_valid_vault_event(tmp_path: Path, bare: Path) -> None:
    repo = _seeded(tmp_path, bare)
    _hold_the_share_slot(repo)
    (written,) = commands.update(repo.ledger, repo.record, fields={"priority": 3})

    vault_share.share(repo.clone, repo.state, plan=vault_share.Plan(time.time))

    verdicts = vault_shapes.check(bare, "main")
    assert all(verdict.valid for verdict in verdicts)
    carried = [
        line[vault_write.LEDGER_FIELD]
        for line in vault_shapes.valid_events(verdicts)
        if line.get("kind") == vault_write.LEDGER_KIND
    ]
    assert carried == [json.loads(events.to_json(written))]
    assert _queued(repo) == []


def _refusal(call: Callable[[], object]) -> dict:
    with pytest.raises(vault_write.VaultRefusedError) as raised:
        call()
    return raised.value.report


def test_a_vault_claim_answers_with_the_built_exit_codes_and_queues_no_ledger_line(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _seeded(tmp_path, bare)
    before = _ledger_bytes(repo.ledger)

    (landed,) = commands.claim(repo.ledger, repo.record, "someone")
    again = _refusal(lambda: commands.claim(repo.ledger, repo.record, "someone"))
    taken = _refusal(lambda: commands.claim(repo.ledger, repo.record, "someone", take=True))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s2")
    lost = _refusal(lambda: commands.claim(repo.ledger, repo.record, "someone"))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "")
    nameless = _refusal(lambda: commands.claim(repo.ledger, repo.record, "someone"))

    assert landed.record == repo.record
    assert landed.id
    assert again["exit"] == vault_claim.YOURS
    assert taken["exit"] == vault_claim.FIX
    assert f"unassign {repo.record} --take" in taken["fix"]
    assert lost["exit"] == vault_claim.LOST
    assert lost["holder"]["session"] == "s1"
    assert nameless["exit"] == vault_claim.FIX
    assert vault_claim.holder(repo.clone, repo.record)[1] == "s1"
    assert _ledger_bytes(repo.ledger) == before
    assert not [line for line in _queued(repo) if line["kind"] == vault_write.LEDGER_KIND]


def _share(repo: Repo) -> None:
    vault_share.share(repo.clone, repo.state, plan=vault_share.Plan(time.time))


def test_a_vault_unassign_by_the_holder_gives_the_record_back(tmp_path: Path, bare: Path) -> None:
    repo = _seeded(tmp_path, bare)
    commands.claim(repo.ledger, repo.record, "someone")
    before = _ledger_bytes(repo.ledger)

    (given,) = commands.unassign(repo.ledger, repo.record, "someone")
    _share(repo)

    assert given.record == repo.record
    assert vault_claim.holder(repo.clone, repo.record) is None
    assert _ledger_bytes(repo.ledger) == before


def test_a_vault_unassign_by_another_exits_3_and_with_take_releases(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _seeded(tmp_path, bare)
    commands.claim(repo.ledger, repo.record, "someone")
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s2")

    lost = _refusal(lambda: commands.unassign(repo.ledger, repo.record, "other"))
    commands.unassign(repo.ledger, repo.record, "other", take=True)
    _share(repo)

    assert lost["exit"] == vault_claim.LOST
    assert lost["next"] == f"unassign {repo.record} --take"
    assert vault_claim.holder(repo.clone, repo.record) is None


def test_a_vault_unassign_of_an_unheld_record_exits_1_and_an_unreachable_vault_exits_5(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare)
    unheld = _refusal(lambda: commands.unassign(repo.ledger, repo.record, "someone"))
    nowhere = tmp_path / "not-a-vault"
    nowhere.mkdir()
    _with_vault(repo, nowhere)

    unreachable = _refusal(lambda: commands.unassign(repo.ledger, repo.record, "someone"))

    assert unheld["exit"] == vault_claim.FIX
    assert unreachable["exit"] == vault_claim.UNREACHABLE
    assert unreachable["check"].startswith("git fetch ")


def test_without_a_vault_every_write_lands_in_the_ledger_and_queues_nothing(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare, configured=False)
    recorded_review(commands, repo.ledger, repo.record)
    before = _ledger_bytes(repo.ledger)

    created = commands.create_root(repo.ledger, {"title": "plain"}, prefix=SEED_PREFIX)
    updated = commands.update(repo.ledger, repo.record, fields={"priority": 1})
    claimed = commands.claim(repo.ledger, repo.record, "someone")
    released = commands.unassign(repo.ledger, repo.record, "someone")

    held = {event.id for event in events.read_events(repo.ledger)[0]}
    assert created[0].record.startswith(f"{SEED_PREFIX}-")
    assert {event.id for event in (*created, *updated, *claimed, *released)} <= held
    assert _ledger_bytes(repo.ledger) != before
    assert not (repo.state / "vaults").exists()


def _cli(root: Path, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(CLI), *argv],
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_cli_create_in_a_vault_repository_queues_and_leaves_the_ledger(
    tmp_path: Path, bare: Path
) -> None:
    repo = _without_vault(_seeded(tmp_path, bare))
    assert (
        _cli(repo.root, "config", ".basicly/ledger", "set", "prefix", SEED_PREFIX).returncode == 0
    )
    _with_vault(repo, bare)
    _hold_the_share_slot(repo)
    before = _ledger_bytes(repo.ledger)

    done = _cli(repo.root, "create", ".basicly/ledger", "--title", "T")

    assert done.returncode == 0, done.stdout + done.stderr
    assert json.loads(done.stdout)["record"].startswith(f"{VAULT_PREFIX}-")
    assert _ledger_bytes(repo.ledger) == before
    assert {line["kind"] for line in _queued(repo)} == {vault_write.LEDGER_KIND}
