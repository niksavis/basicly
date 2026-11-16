from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import owned_store
from tests.test_tracker_mcp import BOOT
from tests.test_tracker_plugin import package

__all__ = ["package"]


def _call(argv: list[str], root: Path, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(
        argv, cwd=root, env=env, capture_output=True, text=True, check=False, timeout=60
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done


def test_machine_freshness_uses_the_supplied_bootstrap_clock(
    package: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    state = tmp_path / "machine-state"
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(state))
    env = os.environ.copy()
    _call(["git", "init", "-q", str(root)], tmp_path, env)
    vault = tmp_path / "vault.git"
    _call(["git", "init", "--bare", "-q", "-b", "main", str(vault)], tmp_path, env)
    command = [sys.executable, "-I", "-S", "-c", BOOT, str(package / "__init__.py")]
    _call([*command, "vault-init", "--vault", str(vault), "--prefix", "demo", "--yes"], root, env)

    report = owned_store.packaged_kit("bootstrap").prepare(root, clock=lambda: 1234.5)

    assert report["ready"] is True
    stamps = list(state.glob("vaults/*/fetched"))
    assert len(stamps) == 1
    assert float(stamps[0].read_text()) == 1234.5


@pytest.mark.parametrize("arrival", ["clone", "pull"])
@pytest.mark.parametrize("interface", ["package", "harness"])
def test_committed_onboarding_establishes_memory_after_clone_or_pull(
    package: Path, tmp_path: Path, arrival: str, interface: str
) -> None:
    seed = tmp_path / "seed"
    seed.mkdir()
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "BASICLY_"))
    }
    env.update(
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
        GIT_CONFIG_GLOBAL=str(tmp_path / "gitconfig"),
        GIT_CONFIG_NOSYSTEM="1",
        GIT_AUTHOR_NAME="fixture",
        GIT_AUTHOR_EMAIL="fixture@example.invalid",
        GIT_COMMITTER_NAME="fixture",
        GIT_COMMITTER_EMAIL="fixture@example.invalid",
        BASICLY_HOLDER="fixture",
        BASICLY_TRACKER_STATE=str(tmp_path / "seed-state"),
    )
    Path(env["HOME"]).mkdir()
    Path(env["GIT_CONFIG_GLOBAL"]).write_text("")
    unrelated = Path(env["HOME"]) / "unrelated-config"
    unrelated.write_text("keep this setting\n")
    _call(["git", "init", "-q", "-b", "main"], seed, env)
    (seed / "README.md").write_text("Shared repository.\n")
    _call(["git", "add", "README.md"], seed, env)
    _call(["git", "commit", "-qm", "Initial repository"], seed, env)
    consumer = tmp_path / "consumer"
    if arrival == "pull":
        _call(["git", "clone", "-q", str(seed), str(consumer)], tmp_path, env)
    vault = tmp_path / "vault.git"
    _call(["git", "init", "--bare", "-q", "-b", "main", str(vault)], tmp_path, env)
    command = [sys.executable, "-I", "-S", "-c", BOOT, str(package / "__init__.py")]
    _call([*command, "vault-init", "--vault", str(vault), "--prefix", "demo", "--yes"], seed, env)
    _call([*command, "init", "--scope", "project", "--sandbox"], seed, env)
    created = _call(
        [
            *command,
            "create",
            ".basicly/ledger",
            "--title",
            "Known persistent memory",
            "--field",
            "issue_type=task",
            "--field",
            "size=small",
            "--description",
            "## Context\nA cloned repository needs its existing tracker history.",
            "--acceptance",
            "When startup runs, existing history shall be readable.",
        ],
        seed,
        env,
    )
    record = json.loads(created.stdout)["record"]
    _call([*command, "sync", "--urgent"], seed, env)
    _call(["git", "add", "."], seed, env)
    _call(["git", "commit", "-qm", "Configure shared tracker\n\nRecord: none"], seed, env)
    if arrival == "clone":
        _call(["git", "clone", "-q", str(seed), str(consumer)], tmp_path, env)
    else:
        _call(["git", "pull", "--ff-only"], consumer, env)
    machine_env = {**env, "BASICLY_TRACKER_STATE": str(tmp_path / "new-machine-state")}
    entry = Path(sys.executable).parent / ("basicly.exe" if os.name == "nt" else "basicly")
    startup = (
        [str(entry), "tracker", "bootstrap"]
        if interface == "harness"
        else [sys.executable, str(consumer / ".basicly/tracker-bootstrap.py")]
    )
    before = _call(["git", "status", "--porcelain"], consumer, machine_env).stdout
    report = json.loads(
        _call(
            startup,
            consumer,
            machine_env,
        ).stdout
    )
    again = json.loads(
        _call(
            startup,
            consumer,
            machine_env,
        ).stdout
    )

    assert report["ready"] is again["ready"] is True
    assert report["records"] == again["records"] == 1
    shown = json.loads(
        _call(
            [
                sys.executable,
                str(consumer / ".basicly/tracker.pyz"),
                "show",
                ".basicly/ledger",
                record,
            ],
            consumer,
            machine_env,
        ).stdout
    )
    assert shown["fields"]["title"] == "Known persistent memory"
    assert _call(["git", "status", "--porcelain"], consumer, machine_env).stdout == before == ""
    assert unrelated.read_text() == "keep this setting\n"
    assert (consumer / ".basicly/ledger").is_file()
    assert (consumer / ".agents/skills/tracker/SKILL.md").is_file()
    assert (consumer / ".claude/skills/tracker/SKILL.md").is_file()
    assert ".basicly/tracker-bootstrap.py" in (consumer / "AGENTS.md").read_text()


@pytest.mark.parametrize("interface", ["package", "harness"])
def test_an_agent_without_persistent_memory_never_starts_its_client(
    package: Path, tmp_path: Path, interface: str
) -> None:
    root = tmp_path / "unconfigured"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True, capture_output=True)
    marker = root / "implementation-started"
    command = (
        [sys.executable, "-I", "-S", "-c", BOOT, str(package / "__init__.py"), "agent"]
        if interface == "package"
        else [
            str(Path(sys.executable).parent / ("basicly.exe" if os.name == "nt" else "basicly")),
            "tracker",
            "agent",
        ]
    )
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(
        BASICLY_TRACKER_STATE=str(tmp_path / "state"),
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
    )
    done = subprocess.run(
        [
            *command,
            "--",
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert done.returncode != 0, done.stdout + done.stderr
    assert "tracker" in done.stderr.lower()
    assert "init" in done.stderr.lower() or "onboard" in done.stderr.lower()
    assert not marker.exists()
