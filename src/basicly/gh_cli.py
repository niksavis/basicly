from __future__ import annotations

import shutil
import subprocess  # nosec B404


def run(argv: list[str]) -> str | None:
    gh = shutil.which("gh")
    if gh is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 — resolved gh receives this module's literal argv; no shell  # nosec B603
            [gh, *argv], capture_output=True, text=True, encoding="utf-8", check=False
        )
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None
