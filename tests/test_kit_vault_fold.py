from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.test_kit_vault_shapes import Vault, _line, _segment

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_fold = _load("basicly_tracker_kit_vault_fold", "vault_fold.py")
cli = _load("tracker_cli_vault_fold", "cli.py")


def _event(kind: str, writer: str, session: str, **extra: object) -> dict[str, object]:
    return {"kind": kind, "writer": writer, "session": session, "record": "r", **extra}


def test_the_claim_in_the_earlier_commit_holds_and_the_later_one_is_void() -> None:
    folded = vault_fold.fold([_event("claim", "w1", "s1"), _event("claim", "w2", "s2", id="e2")])

    state = folded.records["r"]

    assert state.holder == ("w1", "s1")
    assert state.void == [{"id": "e2", "kind": "claim", "why": "held by w1/s1"}]


def test_unassign_by_someone_other_than_the_holder_is_void_and_keeps_the_holder() -> None:
    folded = vault_fold.fold([
        _event("claim", "w1", "s1"),
        _event("unassign", "w2", "s2", id="e2"),
        _event("claim", "w3", "s3", id="e3"),
    ])

    state = folded.records["r"]

    assert state.holder == ("w1", "s1")
    assert [v["id"] for v in state.void] == ["e2", "e3"]


def test_the_holder_can_give_a_record_back_or_hand_it_over() -> None:
    released = vault_fold.fold([_event("claim", "w1", "s1"), _event("unassign", "w1", "s1")])
    handed = vault_fold.fold([
        _event("claim", "w1", "s1"),
        _event("unassign", "w1", "s1", payload={"to": ["w2", "s2"]}),
    ])

    assert released.records["r"].holder is None
    assert handed.records["r"].holder == ("w2", "s2")


def test_an_unknown_fold_kind_is_kept_and_holder_changes_on_that_record_refuse() -> None:
    folded = vault_fold.fold([
        _event("claim", "w1", "s1"),
        _event("escalate", "w1", "s1", fold=True),
        _event("note-v9", "w1", "s1"),
    ])
    state = folded.records["r"]

    with pytest.raises(vault_fold.UpgradeNeededError, match="upgrade"):
        vault_fold.refuse_a_holder_change(state)

    assert state.upgrade_needed == "escalate"
    assert state.holder == ("w1", "s1")


def test_each_status_change_is_read_under_the_configuration_in_force_at_its_position() -> None:
    folded = vault_fold.fold([
        _event("status", "w1", "s1", payload={"status": "review"}, id="early"),
        {"kind": "config", "payload": {"statuses": ["open", "review", "closed"]}},
        _event("status", "w1", "s1", payload={"status": "review"}, id="late"),
        {"kind": "config", "payload": {"statuses": ["open", "closed"]}},
    ])

    state = folded.records["r"]

    assert [v["id"] for v in state.void] == ["early"]
    assert state.status == "review"


def test_proof_t_exclusive_fold_shows_one_holder_and_ten_void_claims(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = Vault(tmp_path / "vault")
    vault.genesis()
    writers = {"a" * 32: ["s1", "s2", "s3", "s4"], "b" * 32: ["s5", "s6", "s7"], "c" * 32: ["s8"]}
    order = [("a" * 32, "s1")] + [(w, s) for w, sessions in writers.items() for s in sessions]
    order = order[:1] + [pair for pair in order[1:] if pair != ("a" * 32, "s1")]
    order += [("b" * 32, "s5"), ("c" * 32, "s8"), ("a" * 32, "s2")]
    opened: set[str] = set()
    for number, (writer, session) in enumerate(order):
        path = f"log/{writer}/00001.jsonl"
        claim: dict[str, object] = {
            "kind": "claim",
            "writer": writer,
            "session": session,
            "record": "r",
            "id": str(number),
        }
        if writer in opened:
            vault.write(path, _line(**claim), append=True)
        else:
            vault.write(path, _segment(writer, claim))
            opened.add(writer)
        vault.commit(f"batch {number}")
    assert len(order) == 11

    code = cli.main(["vault-show", str(vault.root), "r"])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert shown["holder"] == {"writer": "a" * 32, "session": "s1", "name": ""}
    assert len(shown["void"]) == 10


def test_two_claims_for_one_record_in_one_commit_fold_in_order_and_only_the_first_holds(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    vault = Vault(tmp_path / "vault")
    vault.genesis()
    writer = "a" * 32
    first: dict[str, object] = {
        "kind": "claim",
        "writer": writer,
        "session": "s1",
        "record": "r",
        "id": "1",
    }
    second: dict[str, object] = {
        "kind": "claim",
        "writer": writer,
        "session": "s2",
        "record": "r",
        "id": "2",
    }
    vault.write(f"log/{writer}/00001.jsonl", _segment(writer, first, second))
    vault.commit("one batch with two claims")

    code = cli.main(["vault-show", str(vault.root), "r"])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert shown["holder"] == {"writer": writer, "session": "s1", "name": ""}
    assert [v["id"] for v in shown["void"]] == ["2"]
