from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

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


vault_claim = _load("basicly_tracker_kit_vault_claim", "vault_claim.py")
cli = _load("tracker_cli_vault_claim", "cli.py")
vault_queue = vault_claim.vault_queue
git_runner = vault_claim.git_runner


def _ask(clone: Any, state: Path, session: str, record: str = "acme-a1b2", **kw: Any) -> Any:
    return vault_claim.Ask(clone, state, record, session, time.time, **kw)


def _queued(clone: Any) -> list[dict]:
    return vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)


def test_a_claim_lands_with_exit_0_and_the_same_session_again_gets_7(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    first = vault_claim.claim(_ask(clone, state, "s1"))
    again = vault_claim.claim(_ask(clone, state, "s1"))

    assert first["exit"] == vault_claim.LANDED
    assert again["exit"] == vault_claim.YOURS
    assert _queued(clone) == []


def test_a_record_held_elsewhere_exits_3_with_no_push_and_names_the_take_command(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    vault_claim.claim(_ask(clone, state, "s1"))
    before = git_runner.run(["rev-parse", "main"], bare).strip()

    shown = vault_claim.claim(_ask(clone, state, "s2"))

    assert shown["exit"] == vault_claim.LOST
    assert "--take" in shown["next"]
    assert git_runner.run(["rev-parse", "main"], bare).strip() == before


@pytest.mark.parametrize(("session", "record"), [("", "acme-a1b2"), ("s1", ""), ("s1", "--x")])
def test_a_missing_session_or_record_exits_1_with_the_fix(
    tmp_path: Path, bare: Path, session: str, record: str
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    shown = vault_claim.claim(_ask(clone, state, session, record))

    assert shown["exit"] == vault_claim.FIX
    assert shown["fix"]


def test_an_unanswered_fetch_exits_5_queues_nothing_and_prints_the_command(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    def silent(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            raise git_runner.GitTimeoutError("git fetch did not finish within 5 s")
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(_ask(clone, state, "s1", runner=silent))

    assert shown["exit"] == vault_claim.UNREACHABLE
    assert shown["check"].startswith("git fetch ")
    assert _queued(clone) == []


def test_an_internal_error_exits_6_with_a_log_and_no_traceback(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    def broken(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            raise RuntimeError("boom")
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(_ask(clone, state, "s1", runner=broken))

    assert shown["exit"] == vault_claim.INTERNAL
    assert "Traceback" not in json.dumps(shown)
    assert "RuntimeError: boom" in Path(str(shown["log"])).read_text(encoding="utf-8")
    assert "sync" in shown["next"]


def test_every_git_call_gets_only_the_time_that_remains(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    timeouts: list[tuple[str, float]] = []

    def slow(args: list[str], cwd: Path, **options: Any) -> str:
        verb = "push" if "push" in args else args[0]
        timeouts.append((verb, float(options.get("timeout", 0))))
        if args[0] == "fetch":
            time.sleep(0.5)
        return git_runner.run(args, cwd, **options)

    vault_claim.claim(_ask(clone, state, "s1", runner=slow), deadline=4.0)

    probe = next(t for name, t in timeouts if name == "fetch")
    later = [t for name, t in timeouts if name == "push"]
    assert probe <= vault_claim.PROBE
    assert all(t <= 4.0 for _name, t in timeouts)
    assert later and all(t < 3.6 for t in later)


def test_a_claim_past_its_deadline_exits_4_and_names_wait_and_cancel(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    def refusing(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            raise git_runner.GitCommandError("push", 1, "rejected")
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(_ask(clone, state, "s1", runner=refusing), deadline=1.0)

    assert shown["exit"] == vault_claim.UNSETTLED
    assert any("--wait" in step for step in shown["next"])
    assert any("--cancel" in step for step in shown["next"])
    assert len(_queued(clone)) == 1


def test_claim_vault_through_the_cli_exits_0_then_3_naming_take(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    base = ["claim", "demo-a1b2", "--vault", str(bare), "--state", str(tmp_path / "s")]

    first = cli.main([*base, "--session", "s1"])
    capsys.readouterr()
    second = cli.main([*base, "--session", "s2"])
    shown = json.loads(capsys.readouterr().out)

    assert (first, second) == (0, 3)
    assert "--take" in shown["next"]


def test_proof_t_exclusive_ten_sessions_claim_one_record_and_exactly_one_wins(
    tmp_path: Path, bare: Path
) -> None:
    states = [tmp_path / "machines" / "big"] * 8 + [tmp_path / "machines" / f"m{n}" for n in (1, 2)]
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                str(KIT / "cli.py"),
                "claim",
                "acme-a1b2",
                "--vault",
                str(bare),
                "--state",
                str(state),
                "--session",
                f"s{n}",
            ],
            stdout=subprocess.PIPE,
            text=True,
            env={**os.environ},
        )
        for n, state in enumerate(states)
    ]
    for proc in procs:
        proc.communicate(timeout=120)

    codes = Counter(proc.returncode for proc in procs)
    assert codes == Counter({0: 1, 3: 9})


def test_proof_t_urgent_retry_no_claim_gives_up_before_13_attempts_while_time_remains(
    tmp_path: Path, bare: Path
) -> None:
    machines = [_machine(tmp_path, bare, f"m{n:02d}") for n in range(13)]
    results: list[tuple[int, int, float]] = []

    def run(n: int) -> None:
        clone, state = machines[n]
        pushes: list[int] = []

        def latent(args: list[str], cwd: Path, **options: Any) -> str:
            if "push" in args:
                pushes.append(1)
                time.sleep(0.15)
            return git_runner.run(args, cwd, **options)

        started = time.monotonic()
        shown = vault_claim.claim(_ask(clone, state, f"s{n}", f"r-{n}", runner=latent), deadline=30)
        results.append((int(shown["exit"]), len(pushes), time.monotonic() - started))

    threads = [threading.Thread(target=run, args=(n,)) for n in range(13)]
    for thread in threads:
        thread.start()
        time.sleep(1 / 13)
    for thread in threads:
        thread.join(120)

    assert len(results) == 13
    assert all(code == 0 or attempts >= 13 or spent >= 30 for code, attempts, spent in results)
    assert Counter(code for code, _a, _s in results) == Counter({0: 13})


def test_proof_t_claim_burst_at_one_tenth_scale_answers_in_time_and_settles(
    tmp_path: Path, bare: Path
) -> None:
    deadline, stall = 3.0, 4.0
    machines = [_machine(tmp_path, bare, f"m{n:02d}") for n in range(13)]
    answers: list[tuple[int, float]] = []
    stalled = threading.Event()

    def run(n: int) -> None:
        clone, state = machines[n]

        def maybe_stalled(args: list[str], cwd: Path, **options: Any) -> str:
            if n == 0 and args[0] == "fetch" and not stalled.is_set():
                stalled.set()
                time.sleep(min(stall, float(options.get("timeout", stall))))
                raise git_runner.GitTimeoutError("git fetch did not finish")
            return git_runner.run(args, cwd, **options)

        started = time.monotonic()
        ask = _ask(clone, state, f"s{n}", runner=maybe_stalled)
        shown = vault_claim.claim(ask, deadline=deadline)
        answers.append((int(shown["exit"]), time.monotonic() - started))

    threads = [threading.Thread(target=run, args=(n,)) for n in range(13)]
    for thread in threads:
        thread.start()
        time.sleep(1 / 13)
    for thread in threads:
        thread.join(60)
    for clone, state in machines:
        if _queued(clone):
            _share(clone, state)
    holders = {vault_claim.holder(clone, "acme-a1b2") for clone, _state in machines}

    assert len(answers) == 13
    assert all(spent <= deadline + 1.0 for _code, spent in answers)
    assert len(holders) == 1
    assert None not in holders
    assert all(code in (0, 3, 4) for code, _spent in answers)
    assert sum(1 for code, _spent in answers if code == 0) <= 1


def test_an_uncontended_claim_fetches_once_and_never_probes_with_ls_remote(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    verbs: list[str] = []

    def counting(args: list[str], cwd: Path, **options: Any) -> str:
        verbs.append("push" if "push" in args else args[0])
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(_ask(clone, state, "s1", runner=counting))

    assert shown["exit"] == vault_claim.LANDED
    assert verbs.count("fetch") == 1
    assert "ls-remote" not in verbs
    assert verbs.count("push") == 1
