from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import runner
from tests.test_tracker_bootstrap import _call
from tests.test_tracker_mcp import BOOT
from tests.test_tracker_plugin import package

__all__ = ["package"]


@pytest.mark.parametrize("interface", ["package", "harness"])
def test_a_cached_memory_view_cannot_hide_unavailable_vault_access(
    package: Path, tmp_path: Path, interface: str
) -> None:
    root = tmp_path / "repository"
    root.mkdir()
    state = tmp_path / "state"
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("GIT_", "BASICLY_"))
    }
    env.update(
        BASICLY_TRACKER_STATE=str(state),
        HOME=str(tmp_path / "home"),
        USERPROFILE=str(tmp_path / "home"),
    )
    _call(["git", "init", "-q", str(root)], tmp_path, env)
    vault = tmp_path / "vault.git"
    _call(["git", "init", "--bare", "-q", "-b", "main", str(vault)], tmp_path, env)
    package_command = [sys.executable, "-I", "-S", "-c", BOOT, str(package / "__init__.py")]
    _call(
        [*package_command, "vault-init", "--vault", str(vault), "--prefix", "demo", "--yes"],
        root,
        env,
    )
    command = (
        [*package_command, "agent"]
        if interface == "package"
        else [
            str(Path(sys.executable).parent / ("basicly.exe" if os.name == "nt" else "basicly")),
            "tracker",
            "agent",
        ]
    )
    ready = _call(
        [
            *command,
            "--",
            sys.executable,
            "-c",
            "import os,json; print(json.dumps({'memory':os.environ['BASICLY_TRACKER_MEMORY']}))",
        ],
        root,
        env,
    )
    assert Path(json.loads(ready.stdout)["memory"]).is_dir()
    clone = next(state.glob("vaults/*/clone"))
    _call(["git", "config", "remote.origin.uploadpack", "false"], clone, env)
    marker = root / "implementation-started"
    before = {
        path: (root / path).read_bytes() for path in (".basicly/ledger", ".basicly-tracker.toml")
    }
    refused = subprocess.run(
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

    assert refused.returncode != 0
    assert "credentials" in refused.stderr.lower() or "vault access" in refused.stderr.lower()
    assert not marker.exists()
    assert all((root / path).read_bytes() == content for path, content in before.items())


def test_a_managed_worker_refuses_implementation_without_memory(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    marker = tmp_path / "worker-started"
    spec = runner.RunnerSpec(
        "worker",
        runner.HEADLESS,
        (
            sys.executable,
            "-c",
            "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
            str(marker),
            "{prompt}",
        ),
    )

    with pytest.raises(ValueError, match="persistent tracker memory"):
        runner.run(spec, "Implement the change", tmp_path)

    assert not marker.exists()
    assert runner.run(spec, "Preview only", tmp_path, dry_run=True).executed is False


def test_session_orientation_refuses_a_repository_with_no_memory(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, capture_output=True)
    entry = Path(sys.executable).parent / ("basicly.exe" if os.name == "nt" else "basicly")
    done = subprocess.run(
        [str(entry), "session", "start"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )

    assert done.returncode != 0
    assert "tracker" in done.stdout.lower() + done.stderr.lower()
    assert "onboarding" in done.stdout.lower() + done.stderr.lower()
