from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_kit_vault_shapes import WRITER_A, WRITER_B, Vault, _line, _segment

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_clone = _load("basicly_tracker_kit_vault_clone", "vault_clone.py")
cli = _load("tracker_cli_vault_clone", "cli.py")
git_runner = vault_clone.git_runner


@pytest.fixture
def origin(tmp_path: Path) -> Vault:
    made = Vault(tmp_path / "origin")
    made.genesis()
    return made


def _batch(vault: Vault, writer: str = WRITER_A, record: str = "r") -> str:
    path = vault.root / f"log/{writer}/00001.jsonl"
    claim: dict[str, object] = {"kind": "claim", "writer": writer, "record": record}
    if path.exists():
        vault.write(f"log/{writer}/00001.jsonl", _line(**claim), append=True)
    else:
        vault.write(f"log/{writer}/00001.jsonl", _segment(writer, claim))
    return vault.commit(f"batch {record}")


def _clone(tmp_path: Path, origin: Vault) -> Any:
    return vault_clone.locate(tmp_path / "state", str(origin.root))


def _git(clone: Any, *args: str) -> str:
    return git_runner.run(list(args), clone.bare).strip()


def test_state_root_follows_the_variable_then_the_platform_folder_then_home(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    named = str(tmp_path / "named")

    assert vault_clone.state_root({"BASICLY_TRACKER_STATE": named}, home, windows=False) == Path(
        named
    )
    assert vault_clone.state_root({"XDG_STATE_HOME": named}, home, windows=False) == (
        Path(named) / "basicly-tracker"
    )
    assert vault_clone.state_root({"LOCALAPPDATA": named}, home, windows=True) == (
        Path(named) / "basicly-tracker"
    )
    assert vault_clone.state_root({}, home, windows=False) == (
        home / ".local" / "state" / "basicly-tracker"
    )
    with pytest.raises(vault_clone.CloneError, match="absolute"):
        vault_clone.state_root({"BASICLY_TRACKER_STATE": "rel"}, home, windows=False)


def test_a_remote_vault_never_gets_the_file_protocol_and_a_bad_one_is_refused(
    tmp_path: Path,
) -> None:
    remote = vault_clone.locate(tmp_path, "https://example.invalid/acme/vault.git")

    assert remote.allow_file is False
    assert remote.folder.parent == tmp_path / "vaults"
    with pytest.raises(vault_clone.CloneError, match="neither a local repository folder"):
        vault_clone.locate(tmp_path, "https://user:token@example.invalid/v.git")


def test_the_first_fetch_clones_bare_with_mode_0700_and_the_main_refspec(
    tmp_path: Path, origin: Vault
) -> None:
    clone = _clone(tmp_path, origin)

    shown = vault_clone.fetch(clone)

    assert _git(clone, "rev-parse", "--is-bare-repository") == "true"
    assert _git(clone, "config", "remote.origin.fetch") == vault_clone.FETCH_REFSPEC
    assert shown["head"] == origin.git("rev-parse", "HEAD")
    if os.name != "nt":
        assert clone.folder.stat().st_mode & 0o777 == 0o700
        assert (tmp_path / "state").stat().st_mode & 0o777 == 0o700


def test_proof_t_refspec_a_pushed_batch_moves_the_head_and_seen(
    tmp_path: Path, origin: Vault
) -> None:
    bare = tmp_path / "vault.git"
    origin.git("clone", "-q", "--bare", str(origin.root), str(bare))
    clone = vault_clone.locate(tmp_path / "state", str(bare))
    vault_clone.fetch(clone)
    new_head = _batch(origin)
    origin.git("push", "-q", str(bare), "main")

    shown = vault_clone.fetch(clone)

    assert shown["head"] == new_head
    assert _git(clone, "rev-parse", vault_clone.HEAD_REF) == new_head
    assert _git(clone, "rev-parse", vault_clone.SEEN_REF) == new_head


def test_a_quarantined_commit_is_listed_and_seen_still_moves(tmp_path: Path, origin: Vault) -> None:
    clone = _clone(tmp_path, origin)
    vault_clone.fetch(clone)
    origin.write("notes.txt", "x\n")
    bad = origin.commit("outside the log")
    good = _batch(origin, WRITER_B)

    shown = vault_clone.fetch(clone)

    assert [q["commit"] for q in shown["quarantined"]] == [bad]
    assert shown["seen"] == good
    assert shown["moved"] is True


def test_a_head_that_lost_the_seen_commit_leaves_seen_and_says_retracted(
    tmp_path: Path, origin: Vault
) -> None:
    clone = _clone(tmp_path, origin)
    first = origin.git("rev-parse", "HEAD")
    seen = _batch(origin)
    vault_clone.fetch(clone)
    origin.git("reset", "-q", "--hard", first)
    _batch(origin, WRITER_B)

    shown = vault_clone.fetch(clone)

    assert shown["retracted"] is True
    assert _git(clone, "rev-parse", vault_clone.SEEN_REF) == seen


def test_a_failed_fetch_is_retried_once(tmp_path: Path, origin: Vault) -> None:
    clone = _clone(tmp_path, origin)
    fetches: list[int] = []

    def flaky(args: list[str], cwd: Path, **options: object) -> str:
        if args[0] == "fetch":
            fetches.append(1)
            if len(fetches) == 1:
                raise git_runner.GitCommandError("fetch", 128, "")
        return git_runner.run(args, cwd, **options)

    shown = vault_clone.fetch(clone, runner=flaky)

    assert len(fetches) == 2
    assert shown["fetched"] is True


def test_two_failed_fetches_show_the_last_state_and_its_age(tmp_path: Path, origin: Vault) -> None:
    clone = _clone(tmp_path, origin)
    vault_clone.fetch(clone, clock=lambda: 1000.0)
    head = origin.git("rev-parse", "HEAD")
    _batch(origin)

    def down(args: list[str], cwd: Path, **options: object) -> str:
        if args[0] == "fetch":
            raise git_runner.GitTimeoutError("git fetch did not finish")
        return git_runner.run(args, cwd, **options)

    shown = vault_clone.fetch(clone, runner=down, clock=lambda: 1042.0)

    assert shown["fetched"] is False
    assert shown["head"] == head
    assert shown["age_s"] == 42.0


def test_a_first_fetch_that_fails_twice_is_refused_by_name(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    clone = vault_clone.locate(tmp_path / "state", str(empty))

    with pytest.raises(vault_clone.CloneError, match="no earlier fetch exists"):
        vault_clone.fetch(clone)


def test_proof_t_fetch_lock_a_stalled_fetch_makes_the_second_one_wait(
    tmp_path: Path, origin: Vault
) -> None:
    clone = _clone(tmp_path, origin)
    vault_clone.fetch(clone)
    _batch(origin)
    release = threading.Event()
    spans: dict[str, list[float]] = {}
    heads: list[str] = []
    errors: list[Exception] = []

    def runner_for(name: str, stall: bool):
        def run(args: list[str], cwd: Path, **options: object) -> str:
            if args[0] != "fetch":
                return git_runner.run(args, cwd, **options)
            spans[name] = [time.monotonic()]
            if stall:
                release.wait(5)
            out = git_runner.run(args, cwd, **options)
            spans[name].append(time.monotonic())
            return out

        return run

    def worker(name: str, stall: bool) -> None:
        try:
            shown = vault_clone.fetch(clone, runner=runner_for(name, stall))
            heads.append(shown["head"] if shown["fetched"] else "not fetched")
        except (
            ValueError,
            OSError,
            git_runner.GitRunnerError,
            vault_clone.events.LedgerError,
        ) as exc:
            errors.append(exc)

    stalled = threading.Thread(target=worker, args=("a", True))
    stalled.start()
    while "a" not in spans:
        time.sleep(0.01)
    second = threading.Thread(target=worker, args=("b", False))
    second.start()
    time.sleep(0.3)
    waited = "b" not in spans
    release.set()
    stalled.join(10)
    second.join(10)

    assert errors == []
    assert waited
    assert spans["b"][0] >= spans["a"][1]
    assert heads == [origin.git("rev-parse", "HEAD")] * 2


def test_fetch_prints_the_clone_the_head_and_the_age_through_the_cli(
    tmp_path: Path, origin: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["fetch", "--vault", str(origin.root), "--state", str(tmp_path / "s")])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert shown["head"] == origin.git("rev-parse", "HEAD")
    assert Path(shown["clone"]).parent.parent == tmp_path / "s" / "vaults"
    assert shown["age_s"] >= 0


def test_fetch_refuses_an_unreadable_vault_name_through_the_cli(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["fetch", "--vault", "nowhere", "--state", str(tmp_path / "s")])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_REFUSED
    assert "neither a local repository folder" in shown["refused"]
    assert not (tmp_path / "s").exists()
