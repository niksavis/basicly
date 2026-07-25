"""Shared pytest fixtures.

One job so far: make the suite's view of which agent CLIs exist a property of the
test rather than of the machine running it (basicly-kjc5.55).

A developer box has ``claude``/``codex``/``copilot`` on PATH and CI has none, so
``select_runner("auto")`` resolved to a headless adapter in one place and to the
manual handoff in the other — and nothing said so. That asymmetry hid
basicly-kjc5.53, a telemetry crash on the handoff path reachable from a real
landing: the suite was green locally, red on CI, and the difference was only found
by someone re-running it with a hand-stripped PATH. An incantation one machine
ever ran is not a check.

So the absent case is pinned as the default, because it is the stricter of the two
and the one CI exercises: every machine now agrees with CI without anybody
remembering a PATH prefix. Coverage of the CLI-present branch does not depend on
ambient PATH either way — ``test_runner.py`` drives both resolutions through
``is_available``/``select_runner``'s injected ``which``, which is where a unit test
should express it.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

# The headless adapters `auto` detects. Kept here rather than imported from
# basicly.runner on purpose: if a rename ever desynchronizes the two, the suite
# should start seeing an ambient CLI and fail loudly, not silently stop pinning.
AGENT_BINARIES = frozenset({"claude", "codex", "copilot"})


@pytest.fixture(autouse=True)
def _hide_ambient_agent_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any agent CLI the host has on PATH unresolvable for the whole suite.

    Patches ``shutil.which`` itself, since both runner call sites default to it.
    Every other binary — git, uv, pre-commit, br — still resolves normally, so
    this changes exactly three answers and nothing else.
    """
    real_which = shutil.which

    def which(cmd: str, mode: int = os.F_OK | os.X_OK, path: str | None = None) -> str | None:
        if Path(cmd).name.removesuffix(".exe").lower() in AGENT_BINARIES:
            return None
        return real_which(cmd, mode, path)

    monkeypatch.setattr(shutil, "which", which)
