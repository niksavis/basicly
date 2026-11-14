from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from tests.test_kit_vault_shapes import KIT, WRITER_A, Vault, _segment


def _guard():
    name = "basicly_tracker_kit_vault_push_guard"
    spec = importlib.util.spec_from_file_location(name, KIT / "vault_push_guard.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _wire(new: str, old: str, ref: str = "refs/heads/main") -> str:
    return f"refs/heads/main {new} {ref} {old}\n"


def _event() -> dict:
    return {
        "id": "c" * 32,
        "kind": "comment",
        "record": "demo-a1",
        "writer": WRITER_A,
        "session": "fixture",
        "at": 1.0,
        "payload": {"text": "Known tracker event."},
    }


def test_guard_accepts_initial_genesis_and_a_complete_normal_batch(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    genesis = vault.genesis()
    guard = _guard()
    assert guard.check(vault.root, _wire(genesis, "0" * 40))["commits"] == 1
    vault.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, _event()))
    tip = vault.commit("valid batch")
    assert guard.check(vault.root, _wire(tip, genesis))["commits"] == 1


@pytest.mark.parametrize("ref", ["refs/heads/topic", "refs/tags/version"])
def test_guard_refuses_other_refs_before_their_objects_are_used(ref: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="refs/heads/main"):
        _guard().check(tmp_path, _wire("a" * 40, "b" * 40, ref))


def test_guard_refuses_a_deletion_before_reading_repository_objects(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="delet"):
        _guard().check(tmp_path, _wire("0" * 40, "b" * 40))


def test_guard_refuses_bad_middle_commit_even_after_its_file_is_removed(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    genesis = vault.genesis()
    vault.write("application.py", "print('unrelated code')\n")
    vault.commit("unrelated code")
    vault.git("rm", "application.py")
    tip = vault.commit("remove unrelated code")
    with pytest.raises(ValueError, match="commit"):
        _guard().check(vault.root, _wire(tip, genesis))


def test_guard_refuses_rewriting_an_existing_event(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    vault.genesis()
    path = f"log/{WRITER_A}/00001.jsonl"
    vault.write(path, _segment(WRITER_A, _event()))
    before = vault.commit("valid batch")
    vault.write(path, _segment(WRITER_A, {**_event(), "payload": {"text": "Changed history."}}))
    tip = vault.commit("rewrite history")
    with pytest.raises(ValueError, match="commit"):
        _guard().check(vault.root, _wire(tip, before))


def test_guard_refuses_non_event_json_in_a_valid_segment(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    before = vault.genesis()
    vault.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, {"writer": WRITER_A}))
    tip = vault.commit("unrelated json")
    with pytest.raises(ValueError, match="event"):
        _guard().check(vault.root, _wire(tip, before))


def test_guard_refuses_non_fast_forward_history_without_changing_refs(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    genesis = vault.genesis()
    vault.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, _event()))
    old = vault.commit("published branch")
    vault.git("checkout", "-qb", "other", genesis)
    vault.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, {**_event(), "id": "d" * 32}))
    new = vault.commit("divergent history")
    before = vault.git("show-ref")
    with pytest.raises(ValueError, match="rewrite"):
        _guard().check(vault.root, _wire(new, old))
    assert vault.git("show-ref") == before


@pytest.mark.parametrize("wire", ["bad input\n", "main nope refs/heads/main nope\n"])
def test_guard_refuses_malformed_push_input_before_git_calls(tmp_path: Path, wire: str) -> None:
    with pytest.raises(ValueError):
        _guard().check(tmp_path, wire)
