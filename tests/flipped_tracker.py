"""A checkout on the owned rung, with ``br`` absent and any spawn made fatal.

The instrument the flip's criterion needs, shared by the callers `basicly-wpc8.1`
retired: "no br was spawned" is only evidence when a spawn *fails the test*, because a
call site that silently degraded to writing nothing would satisfy the weaker assertion
— which is exactly the failure mode a write seam can have. Generalised from
``tests/test_br_seam.py``'s ``no_br`` fixture, which proved the same thing for markers.

Everything is seeded through the kit rather than through ``br create``: a repo that had
to spawn br to acquire its own records could not be the subject of a test about br
being absent.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

import pytest

from basicly import br, config

KIT_SOURCE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "tracker"


def flipped_repo(tmp_path: Path) -> Path:
    """A checkout with the kit installed, an empty ledger, and ``[tracker] mode = owned``."""
    kit_dir = tmp_path / br.KIT_TRACKER_DIR
    kit_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, kit_dir / source.name)
    (tmp_path / br.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(
        f'[tracker]\nmode = "{br.MODE_OWNED}"\n', encoding="utf-8"
    )
    # Read back through the reader `basicly.config` installs into the seam, so a caller
    # that gets a repo from here knows the rung resolved rather than assuming it: an
    # unresolved mode is refused at the seam, and the refusal reads nothing like a flip
    # that did not happen.
    assert config.load_tracker_mode(tmp_path) == br.MODE_OWNED
    return tmp_path


def seed(repo: Path, record: str, **fields: str) -> None:
    """Put *record* in *repo*'s ledger as an open bead carrying *fields*."""
    kit = br.kit(repo)
    drafts = [kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})]
    drafts += [
        kit.events.Draft(record, kit.events.KIND_FIELD, {"name": name, "value": value})
        for name, value in fields.items()
    ]
    kit.events.append(br.ledger_dir(repo), drafts)


def refuse_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take br off PATH, and make any spawn the test's failure rather than a fallback."""
    monkeypatch.setattr(br, "which", lambda: None)

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(br.subprocess, "run", refuse)


def ledger_events(repo: Path) -> list[Any]:
    """Every event in *repo*'s owned ledger, in file order."""
    kit = br.kit(repo)
    return kit.read_ledger(br.ledger_dir(repo))
