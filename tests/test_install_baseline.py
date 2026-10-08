from __future__ import annotations

import io
import os
import subprocess
import sys
import tarfile
from pathlib import Path

from basicly import cli
from basicly.catalog import bundled_catalog_root, iter_catalog_files

REPO_ROOT = Path(__file__).resolve().parent.parent
OLD_RELEASE = "v0.12.2"
CORE = Path(".basicly") / "core"
STATE = Path(".basicly") / "state" / "install.json"
KIT_EVENTS = "kit/tracker/events.py"
DROPPED_SKILL = "skills/tool-git/skill.yaml"
EDITED_FRAGMENT = "fragments/decisions/decision-protocol.fragment.yaml"


def _old_core(consumer: Path) -> Path:

    archive = subprocess.run(  # nosec B603 B607
        ["git", "-C", str(REPO_ROOT), "archive", OLD_RELEASE, CORE.as_posix()],
        capture_output=True,
        check=True,
    ).stdout
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        tar.extractall(consumer, filter="data")
    return consumer / CORE


def _install(consumer: Path, *args: str) -> subprocess.CompletedProcess[str]:

    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "basicly.cli", "install", *args],
        cwd=consumer,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def _catalog(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes() for path in iter_catalog_files(root)
    }


def _every_file(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_old_shipped_files_update_without_force(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    core = _old_core(consumer)
    assert not (consumer / STATE).exists()
    assert (core / DROPPED_SKILL).is_file()
    assert (core / KIT_EVENTS).read_bytes() != (bundled_catalog_root() / KIT_EVENTS).read_bytes()

    result = _install(consumer)

    assert result.returncode == 0, result.stderr
    assert "hand-edited" not in result.stderr
    assert "unknown origin" not in result.stderr
    assert _catalog(core) == _catalog(bundled_catalog_root())
    assert not (core / DROPPED_SKILL).exists()


def test_a_real_hand_edit_is_still_kept_without_force(tmp_path: Path) -> None:
    core = _old_core(tmp_path / "consumer")
    edited = core / EDITED_FRAGMENT
    edited.write_text(edited.read_text(encoding="utf-8") + "# local\n", encoding="utf-8")

    report = cli._sync_catalog(bundled_catalog_root(), core, None, force=False, dry_run=True)

    assert report.skipped_edits == [EDITED_FRAGMENT]
    assert KIT_EVENTS in report.updated
    assert DROPPED_SKILL in report.deleted
    assert report.kept_unknown == []


def test_a_mixed_core_is_refused_before_writing(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    core = _old_core(consumer)
    events = core / KIT_EVENTS
    events.write_text(events.read_text(encoding="utf-8") + "LOCAL_NOTE = 1\n", encoding="utf-8")
    before = _every_file(consumer)

    result = _install(consumer)

    assert result.returncode != 0
    assert "would mix kit modules that cannot load together" in result.stderr
    assert f"kept: {KIT_EVENTS}" in result.stderr
    assert "module 'basicly_tracker_kit_events' has no attribute" in result.stderr
    assert _every_file(consumer) == before


def test_force_replaces_the_hand_edited_kit_module_of_a_mixed_core(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    core = _old_core(consumer)
    events = core / KIT_EVENTS
    events.write_text(events.read_text(encoding="utf-8") + "LOCAL_NOTE = 1\n", encoding="utf-8")

    result = _install(consumer, "--force")

    assert result.returncode == 0, result.stderr
    assert _catalog(core) == _catalog(bundled_catalog_root())
