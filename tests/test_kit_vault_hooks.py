from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_kit_vault_clone import _fetch
from tests.test_kit_vault_shapes import Vault
from tests.test_kit_vault_share import _ids, _machine, _write, bare

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
__all__ = ["bare"]


def _hook(event: str, clone: Any, state: Path, **options: Any) -> dict:
    options.setdefault("clock", time.time)
    return vault_hooks.hook(event, clone, state, **options)


def _status(clone: Any) -> dict:
    return vault_hooks.status(clone, clock=time.time)


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_hooks = _load("basicly_tracker_kit_vault_hooks", "vault_hooks.py")
cli = _load("tracker_cli_vault_hooks", "cli.py")
vault_clone = vault_hooks.vault_clone
vault_queue = vault_hooks.vault_queue
vault_share = vault_hooks.vault_share


@pytest.fixture(autouse=True)
def _no_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "BASICLY_TRACKER_SESSION"):
        monkeypatch.delenv(name, raising=False)


def _queued(clone: Any) -> int:
    return len(vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME))


def _cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict]:
    code = cli.main(argv)
    return code, json.loads(capsys.readouterr().out)


def test_a_start_hook_fetches_at_most_once_every_20_seconds(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")

    first = _hook("session-start", clone, state, clock=lambda: 1000.0)
    soon = _hook("prompt", clone, state, clock=lambda: 1010.0)
    later = _hook("prompt", clone, state, clock=lambda: 1021.0)

    assert first["fetched"] is not None
    assert soon["fetched"] is None
    assert later["fetched"] is not None


def test_a_start_hook_shares_only_when_the_bulk_slot_is_due(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _write(clone, state, 2, "a")
    stamp = clone.folder / vault_share.SHARED_AT
    clone.folder.mkdir(parents=True, exist_ok=True)
    stamp.write_text("1000.0\n", encoding="utf-8")

    early = _hook("prompt", clone, state, clock=lambda: 1100.0)
    due = _hook("prompt", clone, state, clock=lambda: 1200.0)

    assert early["shared"] is None
    assert early["queued"] == 2
    assert due["shared"]["shared"] == 2
    assert due["queued"] == 0


@pytest.mark.parametrize("event", ["stop", "session-end"])
def test_an_end_hook_shares_the_whole_queue(tmp_path: Path, bare: Path, event: str) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    written = _write(clone, state, 5, "a")

    shown = _hook(event, clone, state)

    assert shown["shared"]["shared"] == 5
    assert sorted(_ids(bare)) == sorted(written)


def test_status_shows_the_data_age_without_a_network_call(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    _write(clone, state, 1, "a")
    _hook("stop", clone, state)
    _write(clone, state, 1, "b")
    bare.rename(tmp_path / "gone.git")

    shown = _status(clone)

    assert shown["fetch_age_s"] is not None
    assert shown["queued"] == 1
    assert shown["oldest_age_s"] is not None
    assert len(shown["writers"]) == 1
    assert shown["retracted"] == []


def test_a_hook_never_fails_the_agent_when_the_vault_is_unreachable(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "s"
    for text in ("shared at once", "queued"):
        _cli(
            ["comment", "r-1", "--vault", str(bare), "--text", text, "--state", str(state)], capsys
        )
    bare.rename(tmp_path / "gone.git")
    (tmp_path / "vault.git").mkdir()

    code, shown = _cli(["hook", "stop", "--vault", str(bare), "--state", str(state)], capsys)

    assert code == cli.EXIT_OK
    assert "skipped" in shown
    assert shown["queued"] == 1


def test_the_demonstration_hook_stop_exits_0_and_status_prints_the_age(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = str(tmp_path / "s")

    stop, stopped = _cli(["hook", "stop", "--vault", str(bare), "--state", state], capsys)
    _hook("session-start", vault_clone.locate(Path(state), str(bare)), Path(state))
    status, shown = _cli(["status", "--vault", str(bare), "--state", state], capsys)

    assert stop == status == cli.EXIT_OK
    assert stopped["queued"] == 0
    assert shown["fetch_age_s"] is not None


def test_proof_t_visible_b_sees_a_comment_after_a_stops_and_b_starts(
    tmp_path: Path, bare: Path
) -> None:
    a_clone, a_state = _machine(tmp_path, bare, "a")
    b_clone, b_state = _machine(tmp_path, bare, "b")
    written = _write(a_clone, a_state, 1, "hello")

    _hook("stop", a_clone, a_state)
    _hook("session-start", b_clone, b_state)
    shown = _status(b_clone)

    writer = vault_queue.writer_of(a_state)
    assert shown["writers"][writer]["id"] == written[0]
    assert shown["fetch_age_s"] < 30


def test_proof_t_person_share_a_write_shares_when_due_else_at_a_later_command(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = str(tmp_path / "s")
    base = ["--vault", str(bare), "--state", state]
    clone = vault_clone.locate(Path(state), str(bare))

    _code, first = _cli(["comment", "r-1", "--text", "one", *base], capsys)
    _code, second = _cli(["comment", "r-1", "--text", "two", *base], capsys)
    (clone.folder / vault_share.SHARED_AT).write_text("0\n", encoding="utf-8")
    _code, later = _cli(["fetch", *base], capsys)

    assert first["shared"]["shared"] == 1
    assert second["shared"] is None
    assert later["shared"]["shared"] == 1
    assert _queued(clone) == 0
    assert sum(_ids(bare).values()) == 2


def test_proof_t_no_code_write_a_thousand_writes_and_hooks_leave_the_code_clone_clean(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    code = Vault(tmp_path / "code")
    code.write("app.py", "print(1)\n")
    code.commit("code")
    monkeypatch.chdir(code.root)
    base = ["--vault", str(bare), "--state", str(tmp_path / "state")]

    for number in range(1000):
        cli.main(["comment", "r-1", "--text", f"n{number}", *base])
        if number % 100 == 99:
            cli.main(["hook", "prompt", *base])
            cli.main(["hook", "stop", *base])
    capsys.readouterr()

    assert code.git("status", "--porcelain") == ""
    assert sum(_ids(bare).values()) == 1000


_AGENT = textwrap.dedent(
    """
    import importlib.util, io, json, contextlib, sys
    kit, phase, bare, state, count = sys.argv[1:6]
    spec = importlib.util.spec_from_file_location("agent_cli", kit)
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    waits = []
    def run(argv):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cli.main(argv)
        report = json.loads(out.getvalue())
        shared = report.get("shared") if isinstance(report.get("shared"), dict) else {}
        waits.extend(w for w in (report.get("lock_wait_s"), shared.get("lock_wait_s")) if w)
    base = ["--vault", bare, "--state", state]
    if phase == "write":
        for n in range(int(count)):
            run(["comment", "r-1", "--text", f"e{n}", *base])
    else:
        run(["hook", "stop", *base])
    print(json.dumps({"max_wait": max(waits, default=0.0)}))
    """
)


def test_proof_t_agents_eight_sessions_in_three_worktrees_land_each_event_once(
    tmp_path: Path, bare: Path
) -> None:
    code = Vault(tmp_path / "code")
    code.write("app.py", "print(1)\n")
    code.commit("code")
    trees = [code.root]
    for name in ("wt1", "wt2"):
        code.git("worktree", "add", "-q", str(tmp_path / name))
        trees.append(tmp_path / name)
    agent = tmp_path / "agent.py"
    agent.write_text(_AGENT, encoding="utf-8")
    state = str(tmp_path / "state")

    def phase(name: str) -> list[float]:
        procs = [
            subprocess.Popen(
                [sys.executable, str(agent), str(KIT / "cli.py"), name, str(bare), state, "125"],
                cwd=trees[n % 3],
                env={**os.environ, "BASICLY_TRACKER_SESSION": f"s{n}"},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            for n in range(8)
        ]
        done = [proc.communicate(timeout=300) for proc in procs]
        assert [proc.returncode for proc in procs] == [0] * 8, [err for _out, err in done]
        return [json.loads(out)["max_wait"] for out, _err in done]

    waits = phase("write") + phase("stop")

    landed = _ids(bare)
    assert sum(landed.values()) == 1000
    assert set(Counter(landed).values()) == {1}
    assert max(waits) <= 1.0


def test_a_write_whose_share_fails_still_succeeds_and_says_the_share_was_skipped(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = str(tmp_path / "s")
    _fetch(vault_clone.locate(Path(state), str(bare)))
    bare.rename(tmp_path / "gone.git")
    (tmp_path / "vault.git").mkdir()

    code, shown = _cli(
        ["comment", "r-1", "--vault", str(bare), "--text", "x", "--state", state], capsys
    )

    assert code == cli.EXIT_OK
    assert shown["queued"] == 1
    assert "skipped" in shown["shared"]
