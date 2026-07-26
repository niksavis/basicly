"""The single seam to the ``br`` (beads) tracker CLI.

Every harness module used to carry its own private ``_run_br`` copy plus a
``shutil.which("br")`` probe — eight call sites to audit whenever br's CLI
or JSON output changes. This module is now the only place that spawns br:
one invocation contract, one absence message, and a one-time version probe
that warns when the installed br is older than the floor the harness was
built against.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess  # nosec B404
import sys
from collections.abc import Mapping
from pathlib import Path

from basicly import redact

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


# --- Export scrubbing (basicly-vkh0.5) --------------------------------------

# br stamps every record it writes with the absolute canonical path of the
# workspace that produced it. The export is committed and this repo is
# distributed, so the field publishes each contributor's home directory layout
# to every consumer clone — a breach of the hard constraint that no
# machine-specific path or username is ever committed, and a portability defect
# in its own right (a path that means something on one machine is a wrong answer
# on another). br documents the field as optional: "older databases and
# hand-edited JSONL records without this field are valid" (`br schema issue`),
# and nothing in the harness reads it.
#
# Stripping it here rather than asking br not to emit it is deliberate: br has no
# config knob for the field, and an upstream defect is requirements input for our
# own replacement, never something we patch outside this repo. The requirement is
# recorded for the replacement in docs/design/work-tracker.md — a record is
# path-free, and provenance is the repo identity rather than a filesystem
# location.
MACHINE_PATH_FIELD = "source_repo_path"

# A path can also reach the export as ordinary text — pasted into a description
# or a comment. That half cannot be fixed by asking br to drop a field, and it
# cannot be edited away either: `br comments` offers only `add` and `list`, so a
# path already recorded in a comment has no removal path through the documented
# CLI (another requirement carried to basicly-vkh0.6 — the replacement owes a
# redaction path for recorded text).
#
# The export is therefore the layer where this is fixed. The local database is
# git-ignored and keeps full fidelity; only the published artifact is redacted,
# so nobody working in the repo loses the original.


def _dump_record(record: dict[str, object]) -> str:
    """Serialize *record* the way br writes the export.

    Compact separators with UTF-8 left unescaped: every untouched record
    round-trips byte-identically under these, so a scrub's diff is exactly the
    fields it changed and nothing else.
    """
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False)


def _redact_paths(value: object) -> object:
    """Recursively redact machine-specific paths in every string inside *value*.

    Comments and dependency rows are nested under the record, so a leak is not
    confined to a top-level field.
    """
    if isinstance(value, str):
        return redact.redact_machine_paths(value)
    if isinstance(value, dict):
        return {key: _redact_paths(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_paths(item) for item in value]
    return value


def scrub_export(repo_root: Path) -> int:
    """Strip machine-specific paths from the tracker export; return records changed.

    Two shapes, one pass: br's :data:`MACHINE_PATH_FIELD` is removed outright, and
    any path left in text is redacted to a placeholder.

    A no-op (returning 0, leaving the file untouched) when the export is absent or
    already clean, and a line that will not parse is passed through verbatim —
    this runs on the commit path, so it must never be the reason tracker state
    fails to land. The companion ``tracker-path-scan`` hook is the gate that makes
    a leak visible; this is only the repair.
    """
    export = repo_root / ".beads" / "issues.jsonl"
    try:
        raw = export.read_text(encoding="utf-8")
    except OSError:
        return 0
    scrubbed: list[str] = []
    changed = 0
    for line in raw.splitlines():
        if not line.strip():
            scrubbed.append(line)
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            scrubbed.append(line)
            continue
        if not isinstance(record, dict):
            scrubbed.append(line)
            continue
        record.pop(MACHINE_PATH_FIELD, None)
        rendered = _dump_record({key: _redact_paths(item) for key, item in record.items()})
        scrubbed.append(rendered)
        if rendered != line:
            changed += 1
    if not changed:
        return 0
    trailer = "\n" if raw.endswith("\n") else ""
    export.write_text("\n".join(scrubbed) + trailer, encoding="utf-8")
    return changed


# --- Reading the committed export (basicly-kjc5.50) --------------------------


def beads_dir(repo_root: Path) -> Path:
    """The active beads directory, following br's git-ignored ``redirect`` file.

    A harness worktree shares the base checkout's tracker through ``redirect``,
    so the redirected directory — not the worktree's own checked-out copy — is
    where br flushes and where the freshest export lives.
    """
    beads = Path(repo_root) / ".beads"
    redirect = beads / "redirect"
    if redirect.is_file():
        try:
            target = Path(redirect.read_text(encoding="utf-8").strip())
        except OSError:
            return beads
        if target.is_dir():
            return target
    return beads


def export_records(repo_root: Path) -> list[dict]:
    """Every issue record in the committed JSONL export, in file order.

    The export is the *shared* tracker: git is its transport, so it is what a
    fresh clone has and the one source that answers for beads this machine never
    ran (D11). It carries each record's comments, which makes the harness marker
    families readable without a single br invocation — the bulk read `br list`
    cannot serve (it caps results and drops closed records).

    Empty when there is no export or it cannot be read; an unparsable line is
    skipped rather than fatal, matching :func:`scrub_export` — every consumer here
    is evidence or telemetry, never a gate.
    """
    export = beads_dir(repo_root) / "issues.jsonl"
    try:
        raw = export.read_text(encoding="utf-8")
    except OSError:
        return []
    records: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("id"), str):
            records.append(record)
    return records


def export_comment_texts(record: Mapping[str, object]) -> list[str]:
    """The comment bodies on one exported record, in export order."""
    comments = record.get("comments")
    if not isinstance(comments, list):
        return []
    return [
        str(comment["text"])
        for comment in comments
        if isinstance(comment, Mapping) and isinstance(comment.get("text"), str)
    ]
