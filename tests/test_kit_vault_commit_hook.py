from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.conftest import VAULT_TEST_HOLDER
from tests.test_kit_vault_share import _machine, _share, bare

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


hook = _load("basicly_tracker_kit_vault_commit_hook", "vault_commit_hook.py")
vault_claim = _load("basicly_tracker_kit_vault_claim", "vault_claim.py")
cli = _load("tracker_cli_vault_commit_hook", "cli.py")
vault_queue = hook.vault_queue
vault_clone = hook.vault_clone
vault_shapes = vault_clone.vault_shapes
git_runner = hook.git_runner

ID = "acme-a1b2"
MESSAGES = {
    "id": f"fix(io): keep the trailing newline ({ID})\n",
    "none": "docs: fix a typo\n\nRecord: none\n",
    "nothing": "fix(io): keep the trailing newline\n",
    "merge": "Merge branch 'topic' into main\n",
    "revert": f'Revert "fix(io): keep the trailing newline ({ID})"\n\nThis reverts commit 0a1b.\n',
    "fixup": f"fixup! fix(io): keep the trailing newline ({ID})\n",
}


def _message(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "COMMIT_EDITMSG"
    path.write_text(text, encoding="utf-8", newline="")
    return path


def _check(clone: Any, state: Path, message: Path, session: str = "s1", **kw: Any) -> dict:
    return hook.check(hook.Commit(clone, state, message, session, time.time, **kw))


def _queued(clone: Any) -> list[dict]:
    return vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)


def _claim(clone: Any, state: Path, record: str, session: str = "s1") -> None:
    ask = vault_claim.Ask(clone, state, record, session, time.time, name=VAULT_TEST_HOLDER)
    assert vault_claim.claim(ask)["exit"] == vault_claim.LANDED


def test_t_hook_refuses_only_the_message_with_nothing_and_the_revert_inherits_the_id(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    shown = {
        name: _check(clone, state, _message(tmp_path, text)) for name, text in MESSAGES.items()
    }

    assert [name for name, report in shown.items() if report["exit"] != 0] == ["nothing"]
    assert shown["revert"]["record"] == ID
    assert shown["fixup"]["record"] == ID
    assert shown["merge"]["exempt"] == "Merge"


def test_a_message_with_nothing_is_refused_with_the_claim_command_and_record_none(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    message = _message(tmp_path, MESSAGES["nothing"])

    shown = _check(clone, state, message)

    assert shown["exit"] == hook.REFUSED
    assert shown["next"] == [f"claim RECORD --vault {clone.url}", "Record: none"]
    assert message.read_text(encoding="utf-8") == MESSAGES["nothing"]


@pytest.mark.parametrize(
    "text",
    [
        "fix: make the hook read-only\n",
        "Merge the two parsers into one\n",
        "fix: name acme-a1b2 in the body only\n\nIt touches acme-a1b2.\n",
        "fix: name it in the last body line\n\nSee the notes (acme-a1b2)\n",
    ],
)
def test_a_hyphenated_word_a_human_merge_or_an_unplaced_id_is_not_a_record_id(
    tmp_path: Path, bare: Path, text: str
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    assert _check(clone, state, _message(tmp_path, text))["exit"] == hook.REFUSED


def test_a_record_line_names_the_id_and_squash_inherits_like_fixup(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    trailer = _check(clone, state, _message(tmp_path, f"fix: a\n\nRecord: {ID}\n"))
    squash = _check(clone, state, _message(tmp_path, f"squash! fixup! fix: a ({ID})\n"))

    assert (trailer["exit"], trailer["record"]) == (hook.ACCEPTED, ID)
    assert (squash["exit"], squash["record"]) == (hook.ACCEPTED, ID)


def test_one_held_claim_is_appended_to_the_subject_and_the_body_is_kept(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _claim(clone, state, ID)
    message = _message(tmp_path, "# a comment\nfix(io): keep it\r\n\r\nThe body stays.\r\n")

    shown = _check(clone, state, message)

    assert (shown["exit"], shown["filled"], shown["record"]) == (hook.ACCEPTED, ID, ID)
    assert message.read_bytes() == (
        f"# a comment\nfix(io): keep it ({ID})\r\n\r\nThe body stays.\r\n".encode()
    )
    assert "warning" not in shown


def test_two_held_claims_refuse_and_list_both(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _claim(clone, state, ID)
    _claim(clone, state, "acme-c3d4")

    shown = _check(clone, state, _message(tmp_path, MESSAGES["nothing"]))

    assert shown["exit"] == hook.REFUSED
    assert shown["held"] == [ID, "acme-c3d4"]


def test_another_sessions_claim_is_not_filled_in(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _claim(clone, state, ID, session="s2")

    shown = _check(clone, state, _message(tmp_path, MESSAGES["nothing"]))

    assert shown["exit"] == hook.REFUSED
    assert shown["held"] == []


def test_a_held_record_is_accepted_without_a_warning(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _claim(clone, state, ID)

    shown = _check(clone, state, _message(tmp_path, MESSAGES["id"]))

    assert shown["exit"] == hook.ACCEPTED
    assert "warning" not in shown
    assert "miss" not in shown


@pytest.mark.parametrize("holder", ["", "s2"])
def test_a_record_this_session_does_not_hold_is_accepted_with_the_claim_command(
    tmp_path: Path, bare: Path, holder: str
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    if holder:
        _claim(clone, state, ID, session=holder)

    shown = _check(clone, state, _message(tmp_path, MESSAGES["id"]))

    assert shown["exit"] == hook.ACCEPTED
    assert "does not hold" in shown["warning"]
    assert shown["next"] == f"claim {ID} --vault {clone.url}"


def test_an_unsettled_claim_is_accepted_with_a_warning_and_the_claim_command(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    origin = vault_queue.Origin("s1", False, time.time)
    vault_queue.write(clone, [{"kind": "claim", "record": ID}], state=state, origin=origin)

    shown = _check(clone, state, _message(tmp_path, MESSAGES["id"]))

    assert shown["exit"] == hook.ACCEPTED
    assert "unsettled" in shown["warning"]
    assert shown["next"] == f"claim {ID} --vault {clone.url}"


def _unreachable(args: list[str], cwd: Path, **options: Any) -> str:
    if args[0] == "fetch":
        raise git_runner.GitTimeoutError("git fetch did not finish within 3 s")
    return git_runner.run(args, cwd, **options)


def test_an_unreachable_vault_queues_one_miss_event_that_shares_as_a_valid_batch(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _claim(clone, state, ID)
    (clone.folder / vault_clone.FETCHED_NAME).write_text("0\n", encoding="utf-8")

    shown = _check(clone, state, _message(tmp_path, MESSAGES["id"]), runner=_unreachable)

    misses = [line for line in _queued(clone) if line["kind"] == hook.MISS_KIND]
    assert shown["exit"] == hook.ACCEPTED
    assert shown["reached"] is False
    assert [line["id"] for line in misses] == [shown["miss"]]
    assert misses[0]["record"] == ID
    assert misses[0]["payload"]["why"] == hook.UNREACHABLE
    _share(clone, state)
    verdicts = vault_shapes.check(bare, "main")
    assert all(verdict.valid for verdict in verdicts)
    shared = vault_shapes.valid_events(verdicts)
    assert [event["id"] for event in shared if event.get("kind") == hook.MISS_KIND] == [
        shown["miss"]
    ]


def test_a_reachable_vault_and_a_refused_commit_queue_no_miss(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    _check(clone, state, _message(tmp_path, MESSAGES["id"]))
    (clone.folder / vault_clone.FETCHED_NAME).write_text("0\n", encoding="utf-8")
    _check(clone, state, _message(tmp_path, MESSAGES["nothing"]), runner=_unreachable)

    assert _queued(clone) == []


def test_a_fresh_fetch_is_not_repeated_on_commit(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _claim(clone, state, ID)
    calls: list[str] = []

    def counting(args: list[str], cwd: Path, **options: Any) -> str:
        calls.append(args[0])
        return git_runner.run(args, cwd, **options)

    shown = _check(clone, state, _message(tmp_path, MESSAGES["id"]), runner=counting)

    assert shown["reached"] is True
    assert calls == []


def test_commit_msg_vault_through_the_cli_exits_1_and_prints_record_none(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    message = _message(tmp_path, MESSAGES["nothing"])
    argv = ["commit-msg", "--vault", str(bare), "--state", str(tmp_path / "st"), str(message)]

    code = cli.main([*argv, "--session", "s1"])

    report = json.loads(capsys.readouterr().out)
    assert code == hook.REFUSED
    assert report["schema"].endswith(".commit-msg-vault.v1")
    assert "Record: none" in report["next"]
    assert report["next"][0].startswith("claim RECORD --vault ")
