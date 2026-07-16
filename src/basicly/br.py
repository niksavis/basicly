"""The single seam to the ``br`` (beads) tracker CLI.

Every harness module used to carry its own private ``_run_br`` copy plus a
``shutil.which("br")`` probe — eight call sites to audit whenever br's CLI
or JSON output changes. This module is now the only place that spawns br:
one invocation contract, one absence message, and a one-time version probe
that warns when the installed br is older than the floor the harness was
built against.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path

# The oldest br this harness is exercised against (see `br --version`).
# The probe warns below this floor; it never blocks — br's core commands
# are stable and a hard failure would strand every loop.
MIN_VERSION = (0, 2)

_probed_paths: set[str] = set()


def which() -> str | None:
    """Path to the br executable, or None when not installed."""
    return shutil.which("br")


def _probe_version(br_path: str) -> None:
    """Warn once per process when the installed br is older than the floor."""
    if br_path in _probed_paths:
        return
    _probed_paths.add(br_path)
    try:
        proc = subprocess.run(  # nosec B603
            [br_path, "--version"], capture_output=True, text=True, check=False, timeout=10
        )
    except OSError, subprocess.TimeoutExpired:
        return
    match = re.search(r"(\d+)\.(\d+)", proc.stdout or "")
    if match is None:
        return
    version = (int(match.group(1)), int(match.group(2)))
    if version < MIN_VERSION:
        floor = ".".join(str(part) for part in MIN_VERSION)
        print(
            f"Warning: br {match.group(0)} is older than the harness floor "
            f"({floor}); upgrade br if tracker commands misbehave.",
            file=sys.stderr,
        )


def run_br(
    repo_root: Path, args: list[str], *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    """Run a br subcommand; raises when br is absent — the harness needs the tracker."""
    br_path = which()
    if not br_path:
        raise RuntimeError("br is not on PATH; the harness requires the beads tracker")
    _probe_version(br_path)
    proc = subprocess.run(  # nosec B603
        [br_path, *args], cwd=repo_root, capture_output=True, text=True, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(f"br {' '.join(args)} failed: {(proc.stderr or proc.stdout).strip()}")
    return proc


def try_run_br(repo_root: Path, args: list[str]) -> subprocess.CompletedProcess[str] | None:
    """Run a br subcommand; None when br is absent (soft call sites)."""
    br_path = which()
    if not br_path:
        return None
    _probe_version(br_path)
    return subprocess.run(  # nosec B603
        [br_path, *args], cwd=repo_root, capture_output=True, text=True, check=False
    )
