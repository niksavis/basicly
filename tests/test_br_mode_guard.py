"""The two things the seam decides before it spawns br: the mode, and translatability.

Both of the dual write's mirror defects were one ordering mistake — a check that ran
after br had taken the write, which cannot refuse anything. So every test here asserts
against **the spawn**, and the stand-in br fails the test if it is ever called. That is
the discriminator: a test asserting only that the call raised would pass just as well
against the defect, because the defect raised too.

Separate from `test_br_seam.py`, which asserts what the seam *does* with a mode already
resolved, and which is at its frozen `module-size` baseline.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import br, config, owned_store

KIT_SOURCE = Path(__file__).resolve().parent.parent / br.KIT_TRACKER_DIR


def _repo(tmp_path: Path, mode: str) -> Path:
    """A checkout with the kit installed and ``[tracker] mode`` declared."""
    (tmp_path / br.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / br.KIT_TRACKER_DIR / source.name)
    (tmp_path / br.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    return tmp_path


class _NeverSpawns:
    """A br whose only assertion is that nothing reached it."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "{}", "")


@pytest.fixture
def never_spawns(monkeypatch: pytest.MonkeyPatch) -> _NeverSpawns:
    """A br on PATH that records what reached it and answers nothing useful."""
    stand_in = _NeverSpawns()
    monkeypatch.setattr(br, "which", lambda: "/usr/bin/br")
    monkeypatch.setattr(br, "_probed_paths", {"/usr/bin/br"})
    monkeypatch.setattr(br.subprocess, "run", stand_in)
    return stand_in


# --- the mode has to be known before the write ---------------------------------


def test_a_write_with_an_unregistered_mode_reader_is_refused(
    tmp_path: Path, never_spawns: _NeverSpawns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unknown is refused, and refused before br is spawned.

    The repo declares `dual`, so the mirror is owed one event. Under the defect
    `tracker_mode` answered `external` for an unregistered reader, `_mirror_write`
    returned early, and br kept the write alone — ten of them, on the day dual write
    went live (`basicly-e2mz.23`).

    `monkeypatch` restores the reader, so the process-global holder cannot leak an
    empty state into a later test.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    monkeypatch.setattr(owned_store, "_mode_reader", [])

    with pytest.raises(br.TrackerModeUnknownError, match="not installed"):
        br.run_br(repo, ["close", "basicly-a"])

    assert never_spawns.calls == []


def test_a_registered_mode_reader_reaches_the_spawn(
    tmp_path: Path, never_spawns: _NeverSpawns
) -> None:
    """The positive control: the same call, with the reader installed, runs.

    Without it the test above passes against a seam that refuses everything, which is
    the failure it would be least likely to notice.
    """
    repo = _repo(tmp_path, br.MODE_DUAL)
    assert config.load_tracker_mode(repo) == br.MODE_DUAL

    br.run_br(repo, ["close", "basicly-a"])

    assert [argv[1] for argv in never_spawns.calls] == ["close"]


# --- an untranslatable write is refused before the spawn ------------------------


@pytest.mark.parametrize(
    ("argv", "because"),
    [
        (["create", "a bead", "-t", "task"], "--json"),
        (["update", "basicly-a", "--nosuchflag", "x"], "no owned-ledger equivalent"),
        (["dep", "add", "basicly-a", "basicly-b"], "no edge type"),
        (["retitle", "basicly-a", "new"], "no owned-ledger translation"),
    ],
)
def test_an_untranslatable_write_is_refused_before_spawn(
    tmp_path: Path, never_spawns: _NeverSpawns, argv: list[str], because: str
) -> None:
    """No argv the mirror could not have recorded ever reaches br.

    Each row is a real argv br itself accepts. Under the defect every one of them
    landed on br and *then* raised, so the command failed with the two stores already
    apart — the guard producing the state it exists to prevent (`basicly-e2mz.24`).
    """
    repo = _repo(tmp_path, br.MODE_DUAL)

    with pytest.raises(br.TrackerDivergenceError, match=because):
        br.run_br(repo, argv)

    assert never_spawns.calls == []


def test_external_mode_refuses_nothing_before_the_spawn(
    tmp_path: Path, never_spawns: _NeverSpawns
) -> None:
    """The pre-cutover control: with no mirror owed, translatability is not the seam's.

    A consumer on `external` writes through br exactly as before, including argvs this
    engine has no translation for — refusing those would break a repo that never opted
    into the owned ledger.
    """
    repo = _repo(tmp_path, br.MODE_EXTERNAL)

    br.run_br(repo, ["retitle", "basicly-a", "new"])

    assert [argv[1] for argv in never_spawns.calls] == ["retitle"]
