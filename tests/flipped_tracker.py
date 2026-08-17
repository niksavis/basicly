"""A checkout with a real ledger, and any spawn made fatal.

The instrument the flip's criterion needs, shared by the callers `basicly-wpc8.1`
retired: "nothing was spawned" is only evidence when a spawn *fails the test*, because a
call site that silently degraded to writing nothing would satisfy the weaker assertion
— which is exactly the failure mode a write seam can have.

Everything is seeded through the kit rather than through ``br create``: a repo that had
to spawn br to acquire its own records could not be the subject of a test about tracker
being absent.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

import pytest

from basicly import config, tracker

KIT_SOURCE = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "tracker"


def flipped_repo(tmp_path: Path) -> Path:
    """A checkout with the kit installed, an empty ledger, and ``[tracker] mode = owned``.

    The ignore rule is not decoration: importing the kit writes ``__pycache__`` beside it,
    and a fixture repo under git reads that as uncommitted work — which holds every
    landing (`merge.commit_tracker_state`). The real repository ignores it the same way.
    """
    kit_dir = tmp_path / tracker.KIT_TRACKER_DIR
    kit_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, kit_dir / source.name)
    ignore = tmp_path / ".gitignore"
    existing = ignore.read_text(encoding="utf-8") if ignore.is_file() else ""
    if "__pycache__" not in existing:
        ignore.write_text(f"{existing}__pycache__/\n", encoding="utf-8")
    (tmp_path / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    # An existing config is left alone: `owned` is the default and the only mode, so a
    # caller that already declared its verify checks and policy keeps them. Writing one
    # unconditionally clobbered a fixture's whole harness config, and the loop then ran
    # with no gates at all.
    config_file = tmp_path / "basicly.toml"
    if not config_file.is_file():
        config_file.write_text(f'[tracker]\nmode = "{tracker.MODE_OWNED}"\n', encoding="utf-8")
    # Read back through the reader `basicly.config` installs into the seam, so a caller
    # that gets a repo from here knows the rung resolved rather than assuming it: an
    # unresolved mode is refused at the seam, and the refusal reads nothing like a flip
    # that did not happen.
    assert config.load_tracker_mode(tmp_path) == tracker.MODE_OWNED
    return tmp_path


def seed(repo: Path, record: str, **fields: str) -> None:
    """Put *record* in *repo*'s ledger as an open bead carrying *fields*."""
    kit = tracker.kit(repo)
    drafts = [kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})]
    drafts += [
        kit.events.Draft(record, kit.events.KIND_FIELD, {"name": name, "value": value})
        for name, value in fields.items()
    ]
    kit.events.append(tracker.ledger_dir(repo), drafts)


def refuse_spawn(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any spawn the test's failure rather than a fallback."""

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


def ledger_events(repo: Path) -> list[Any]:
    """Every event in *repo*'s owned ledger, in file order."""
    kit = tracker.kit(repo)
    return kit.read_ledger(tracker.ledger_dir(repo))


def seed_records(repo: Path, records: Iterable[Mapping[str, Any]]) -> None:
    """Seed *repo*'s ledger from export-shaped dicts: one record per mapping.

    The shape a fixture used to write as JSONL lines, kept because it is what a test is
    *about* — an id, a status, some fields, some comments, some edges. Written through the
    kit rather than by hand, because the fold is what every reader sees: a log the fold
    rejects would describe a tracker that cannot exist.

    ``dependencies`` becomes edge events rather than a field, which is where the fold
    holds them; anything else becomes a ``field`` event.
    """
    kit = tracker.kit(flipped_repo(repo))
    drafts: list[Any] = []
    for record in records:
        issue = str(record["id"])
        drafts.append(
            kit.events.Draft(
                issue, kit.events.KIND_STATUS, {"status": str(record.get("status", "open"))}
            )
        )
        drafts += [
            kit.events.Draft(issue, kit.events.KIND_COMMENT, {"text": str(row["text"])})
            for row in record.get("comments", ())
            if isinstance(row, dict) and isinstance(row.get("text"), str)
        ]
        drafts += [
            kit.events.Draft(issue, kit.events.KIND_FIELD, {"name": name, "value": str(value)})
            for name, value in record.items()
            if name not in {"id", "status", "comments", "dependencies"}
        ]
        drafts += [
            kit.events.Draft(
                issue,
                kit.migrate.KIND_EDGE,
                {
                    kit.migrate.EDGE_FROM: issue,
                    kit.migrate.EDGE_TO: str(edge.get("depends_on_id") or edge["id"]),
                    kit.migrate.EDGE_TYPE: str(edge.get("type") or edge["dependency_type"]),
                },
            )
            for edge in record.get("dependencies", ())
        ]
    kit.events.append(tracker.ledger_dir(repo), drafts)
