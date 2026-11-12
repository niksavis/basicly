from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import textwrap
import threading
import time
from collections import Counter
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_kit_vault_shapes import WRITER_B, Vault, _line, _segment

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_share = _load("basicly_tracker_kit_vault_share", "vault_share.py")
cli = _load("tracker_cli_vault_share", "cli.py")
vault_clone = vault_share.vault_clone
vault_queue = vault_share.vault_queue
vault_shapes = vault_clone.vault_shapes
git_runner = vault_share.git_runner


class CrashError(Exception):
    pass


@pytest.fixture
def bare(tmp_path: Path) -> Path:
    source = Vault(tmp_path / "source")
    source.genesis()
    target = tmp_path / "vault.git"
    source.git("clone", "-q", "--bare", str(source.root), str(target))
    return target


def _machine(tmp_path: Path, bare: Path, name: str) -> tuple[Any, Path]:
    state = tmp_path / "machines" / name
    return vault_clone.locate(state, str(bare)), state


def _write(clone: Any, state: Path, count: int, tag: str) -> list[str]:
    drafts = [vault_queue.comment("acme-a1b2", f"{tag} {n}") for n in range(count)]
    origin = vault_queue.Origin("s", False, time.time)
    return vault_queue.write(clone, drafts, state=state, origin=origin)["events"]


def _ids(bare: Path) -> Counter:
    events = vault_shapes.valid_events(vault_shapes.check(bare, "main"))
    return Counter(str(e["id"]) for e in events if e.get("kind") == "comment")


def test_a_share_pushes_one_batch_commit_and_empties_the_queue(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    written = _write(clone, state, 3, "a")

    shown = vault_share.share(clone, state)
    head = git_runner.run(["rev-parse", "main"], bare).strip()

    assert shown == {"shared": 3, "head": head, "pending": 0}
    assert vault_shapes.classify(bare, head).shape == "batch"
    assert sorted(_ids(bare)) == sorted(written)
    tracked = git_runner.run(["rev-parse", vault_clone.HEAD_REF], clone.bare).strip()
    seen = git_runner.run(["rev-parse", vault_clone.SEEN_REF], clone.bare).strip()
    assert tracked == seen == head
    assert sorted(p.name for p in clone.folder.glob("share-*")) == []


def test_a_second_share_appends_to_the_same_segment(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    _write(clone, state, 2, "a")
    vault_share.share(clone, state)
    _write(clone, state, 2, "b")

    vault_share.share(clone, state)
    head = git_runner.run(["rev-parse", "main"], bare).strip()
    listed = git_runner.run(["ls-tree", "-r", "--name-only", head, "log/"], bare).split()

    assert len(listed) == 1
    assert sum(_ids(bare).values()) == 4


def test_proof_t_share_smoke_three_clones_share_concurrently_and_each_event_lands_once(
    tmp_path: Path, bare: Path
) -> None:
    machines = [_machine(tmp_path, bare, f"m{n}") for n in range(3)]
    written = [i for clone, state in machines for i in _write(clone, state, 100, "e")]
    failures: list[str] = []

    def drain(clone: Any, state: Path) -> None:
        for _round in range(20):
            try:
                if vault_share.share(clone, state)["pending"] == 0:
                    return
            except vault_share.ShareError as exc:
                failures.append(str(exc))
        failures.append("queue never emptied")

    threads = [threading.Thread(target=drain, args=machine) for machine in machines]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    landed = _ids(bare)
    assert "queue never emptied" not in failures
    assert len(written) == 300
    assert sorted(landed) == sorted(written)
    assert set(landed.values()) == {1}


def _refusing(pushes: list[int]) -> Any:
    def run(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            pushes.append(1)
            raise git_runner.GitCommandError("push", 1, "rejected")
        return git_runner.run(args, cwd, **options)

    return run


def test_a_refused_bulk_share_rebuilds_at_most_five_times_and_keeps_the_queue(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    _write(clone, state, 2, "a")
    pushes: list[int] = []

    with pytest.raises(vault_share.ShareError, match="refused the share 5 times"):
        vault_share.share(clone, state, runner=_refusing(pushes))

    assert len(pushes) == 5
    assert len(vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)) == 2


def test_an_urgent_share_retries_until_its_deadline(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    _write(clone, state, 1, "a")
    pushes: list[int] = []
    ticks = iter(range(1000))
    step = 0.25

    with pytest.raises(vault_share.ShareError, match=r"deadline|refused"):
        vault_share.share(
            clone,
            state,
            plan=vault_share.Plan(urgent=True, deadline_s=12),
            runner=_refusing(pushes),
            clock=lambda: next(ticks) * step,
        )

    assert len(pushes) > vault_share.BULK_ATTEMPTS


def test_a_valid_ended_event_stops_the_share_and_keeps_the_queue(
    tmp_path: Path, bare: Path
) -> None:
    other = Vault(tmp_path / "other")
    other.git("pull", "-q", str(bare), "main")
    ended = {
        "kind": "ended",
        "writer": WRITER_B,
        "payload": {"reason": "rolled-back", "successor": "ledger"},
    }
    other.write(f"log/{WRITER_B}/00001.jsonl", _segment(WRITER_B, ended))
    other.commit("end")
    other.git("push", "-q", str(bare), "HEAD:main")
    before = git_runner.run(["rev-parse", "main"], bare).strip()
    clone, state = _machine(tmp_path, bare, "m1")
    _write(clone, state, 2, "a")

    shown = vault_share.share(clone, state)

    assert shown["ended"] == {"reason": "rolled-back", "successor": "ledger"}
    assert shown["pending"] == 2
    assert git_runner.run(["rev-parse", "main"], bare).strip() == before


def _crashing(point: str) -> Any:
    pushed: list[str] = []

    def run(args: list[str], cwd: Path, **options: Any) -> str:
        moves_seen = "update-ref" in args and pushed and pushed[0] in args
        if point == "before-seen" and moves_seen:
            raise CrashError(point)
        out = git_runner.run(args, cwd, **options)
        if "push" in args:
            pushed.append(args[-1].split(":")[0])
            if point == "after-push":
                raise CrashError(point)
        if point == "after-seen" and moves_seen:
            raise CrashError(point)
        return out

    return run


@pytest.mark.parametrize("point", ["after-push", "before-seen", "after-seen"])
def test_a_share_that_crashes_at_any_step_leaves_each_event_once_after_the_next_sync(
    tmp_path: Path, bare: Path, point: str
) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    written = _write(clone, state, 4, "a")

    with pytest.raises(CrashError):
        vault_share.share(clone, state, runner=_crashing(point))
    shown = vault_share.share(clone, state)

    assert shown["pending"] == 0
    assert _ids(bare) == Counter(dict.fromkeys(written, 1))


def test_sync_vault_prints_the_shared_count_and_the_new_head(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    state = tmp_path / "s"
    cli.main(["comment", "acme-a1b2", "--vault", str(bare), "--text", "hi", "--state", str(state)])
    capsys.readouterr()

    code = cli.main(["sync", "--vault", str(bare), "--state", str(state)])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert shown["shared"] == 1
    assert shown["head"] == git_runner.run(["rev-parse", "main"], bare).strip()


_WORKER = textwrap.dedent(
    """
    import json, subprocess, sys
    cli, bare, *states = sys.argv[1:]
    for state in states:
        for _ in range(60):
            done = subprocess.run(
                [sys.executable, cli, "sync", "--vault", bare, "--state", state],
                capture_output=True, text=True,
            )
            if json.loads(done.stdout).get("pending") == 0:
                break
        else:
            sys.exit(f"queue in {state} never emptied")
    """
)


def test_proof_t_writers_sixteen_processes_share_fifty_clones_and_each_event_lands_once(
    tmp_path: Path, bare: Path
) -> None:
    machines = [_machine(tmp_path, bare, f"m{n:02d}") for n in range(50)]
    written = [i for clone, state in machines for i in _write(clone, state, 200, "w")]
    worker = tmp_path / "worker.py"
    worker.write_text(_WORKER, encoding="utf-8")
    groups = [[str(state) for _clone, state in machines[n::16]] for n in range(16)]

    procs = [
        subprocess.Popen(
            [sys.executable, str(worker), str(KIT / "cli.py"), str(bare), *group],
            stderr=subprocess.PIPE,
            text=True,
        )
        for group in groups
    ]
    errors = [proc.communicate(timeout=600)[1] for proc in procs]

    landed = _ids(bare)
    assert [proc.returncode for proc in procs] == [0] * 16, errors
    assert len(written) == 10_000
    assert len(landed) == 10_000
    assert set(landed.values()) == {1}
    assert sorted(landed) == sorted(written)


def test_an_unknown_writer_line_in_the_queue_is_never_pushed(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    _write(clone, state, 1, "a")
    queue = clone.folder / vault_queue.QUEUE_NAME
    with queue.open("a", encoding="utf-8") as stream:
        stream.write(_line(kind="comment", writer=WRITER_B, id="foreign", record="r"))

    vault_share.share(clone, state)

    assert "foreign" not in _ids(bare)
