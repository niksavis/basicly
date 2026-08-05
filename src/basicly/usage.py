"""Execution telemetry: the counters, and the report that joins them to the catalog.

The ``tool-usage`` PostToolUse hook accumulates per-entry counters in
``.basicly/usage/tool-usage.json`` (plain tool names for shell pipeline
heads, ``skill:<name>`` entries for Skill invocations). This module joins
those counters against the skill catalog so the data can answer the question
it was collected for: which shipped tools and skills are actually used, and
which are candidates for culling.

A recorded head only reaches the tool table if it resolves to a real command
(PATH, a repo-local bin dir, or the catalog's own shell examples). Everything
else is a parser miss and is reported as one, because on the tools half the
noise cuts both ways: it invents tools nobody ran, and it hides a real command
that some earlier recorder shredded into fragments (basicly-3ymj).

It also owns the second counter file, ``.basicly/usage/verify-checks.json``:
one entry per ``[[verify.checks]]`` check the verify engine has run and
watched pass. That one is written here rather than by a hook because a check
is never typed at a shell — it exists only as a declaration, so the engine
running it is the only event that can witness it (basicly-3yi3).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

USAGE_FILE = Path(".basicly/usage/tool-usage.json")
VERIFY_CHECKS_FILE = Path(".basicly/usage/verify-checks.json")
SKILL_PREFIX = "skill:"
# Namespace for a verify check inside the union :func:`basicly.release.recorded_executions`
# builds. A check's name is unique per declaration but shares that map with tool names,
# and unprefixed a `pytest` someone typed would answer for the check called `pytest` —
# the wrapper-witness defect in a new place.
VERIFY_CHECK_PREFIX = "verify-check:"

# Executables a repo ships in its own tree rather than on PATH: `markdownlint-cli2`
# is only ever reached through `npx`, and a `uv run pytest` resolves out of the
# project venv, so PATH alone would file 168 real markdownlint runs as parser noise.
# `shutil.which(name, path=...)` applies PATHEXT too, so the Windows `.cmd` shim in
# `node_modules/.bin` resolves by the same call.
LOCAL_BIN_DIRS = (
    Path("node_modules/.bin"),
    Path(".venv/bin"),
    Path(".venv/Scripts"),
)

# ```bash fences in catalog instructions. The commands the catalog teaches are the
# names this checkout can vouch for without the binary being installed on it — a
# machine with no `xh` must still see a recorded `xh` as the tool it is, or the
# report answers the culling question with the answer it was asked to find.
_FENCE = re.compile(r"^\s*```([A-Za-z0-9_+-]*)\s*$")
_SHELL_FENCE_LANGUAGES = frozenset({"bash", "console", "sh", "shell", "zsh"})
# A command name as the `tool-usage` hook records it: a basename, no separators.
_COMMAND_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


@dataclass(frozen=True)
class UsageEntry:
    """One counter: a tool or skill name with its count and last-used date."""

    name: str
    count: int
    last_used: str


@dataclass(frozen=True)
class UsageReport:
    """Tool and skill usage joined against the catalog's skill slugs."""

    tools: tuple[UsageEntry, ...]
    skills: tuple[UsageEntry, ...]
    never_used_skills: tuple[str, ...]
    # Recorded heads that name no command this checkout can resolve: heredoc
    # terminators (`EOF`, `PYEOF`), Python keywords out of a heredoc body (`def`,
    # `assert`, `return`), flag fragments (`-d`) and worktree basenames — all of them
    # written by earlier versions of the recorder, all of them still in the counter
    # file because it accumulates by design and is never reset (basicly-3ymj). Kept
    # as a named bucket rather than dropped: a head landing here is the parser
    # reporting a miss, and silently discarding it would hide the next one.
    unresolved: tuple[UsageEntry, ...]


def _load_counters(path: Path) -> dict[str, dict] | None:
    """The raw counter map at *path*, or None when it is absent or unreadable."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def load_usage(repo_root: Path) -> dict[str, dict] | None:
    """The raw counter map, or None when no usage file exists (hook inactive)."""
    return _load_counters(repo_root / USAGE_FILE)


def load_verify_checks(repo_root: Path) -> dict[str, dict] | None:
    """Per-check execution counters, or None when the engine has never run here.

    Keyed by the check's ``name`` — the declaration itself, which nothing but the
    engine running that declaration can increment. None and an empty map are the same
    thing to this reader and deliberately kept apart for the caller: the release gate
    fails closed on *no ledger at all*, and must not read an absent file as a pass.
    """
    return _load_counters(repo_root / VERIFY_CHECKS_FILE)


def record_verify_check(repo_root: Path, name: str) -> None:
    """Count one engine execution of the verify check *name*; never raises.

    Telemetry, never a gate — the caller is mid-verdict, so every failure path here is
    swallowed rather than turned into a failed check. The usage directory self-ignores
    (``.gitignore`` holding ``*``, written on first use exactly as the ``tool-usage``
    hook writes it) because the release this record unblocks also refuses a dirty tree.

    Writes are atomic through a pid-scoped temporary file: two verify runs in one
    checkout would otherwise interleave a truncated write with the other's rename, and
    a lost count here is a false refusal at tag time — the very defect this records
    against. A corrupt or non-integer counter restarts at zero rather than propagating.
    """
    path = repo_root / VERIFY_CHECKS_FILE
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        gitignore = path.parent / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("*\n", encoding="utf-8")
        counters = _load_counters(path) or {}
        entry = counters.get(name)
        count = entry.get("count") if isinstance(entry, dict) else None
        if not isinstance(count, int) or isinstance(count, bool):
            count = 0
        counters[name] = {
            "count": count + 1,
            "last_used": datetime.now(UTC).date().isoformat(),
        }
        tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(counters, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError:
        return


def catalog_commands(instruction_texts: Iterable[str]) -> frozenset[str]:
    """Command names taught by the shell fences in *instruction_texts*.

    Takes the texts rather than the skills so this module stays in the engine's
    bottom tier (``.importlinter``): the catalog loader sits above it, and the
    caller that already has both is the one that joins them.

    Only a fence tagged with a shell language counts. An untagged fence usually
    holds sample *output*, whose first words are prose — the very thing this set
    exists to keep out of the tool table.
    """
    names: set[str] = set()
    for text in instruction_texts:
        language: str | None = None
        for line in text.splitlines():
            fence = _FENCE.match(line)
            if fence:
                language = None if language is not None else fence.group(1).lower()
                continue
            if language not in _SHELL_FENCE_LANGUAGES:
                continue
            stripped = line.strip().removeprefix("$ ")
            if not stripped or stripped.startswith("#"):
                continue
            head = Path(stripped.split()[0]).name
            if _COMMAND_NAME.match(head):
                names.add(head)
    return frozenset(names)


def _local_bin_path(repo_root: Path) -> str | None:
    """PATH-shaped join of the repo-local executable dirs that exist, or None."""
    present = [repo_root / directory for directory in LOCAL_BIN_DIRS]
    return os.pathsep.join(str(d) for d in present if d.is_dir()) or None


def _resolves(name: str, catalog: Collection[str], local_bin: str | None) -> bool:
    """Whether *name* is a command this checkout can point at something real for."""
    if not name:
        return False
    if name in catalog:
        return True
    if shutil.which(name) is not None:
        return True
    return local_bin is not None and shutil.which(name, path=local_bin) is not None


def build_report(
    repo_root: Path,
    catalog_skill_slugs: list[str],
    commands: Collection[str] = (),
) -> UsageReport | None:
    """Join the counters against the catalog; None when no data was recorded.

    Recorded heads are split rather than filtered: a head that resolves to a command
    is a tool, and one that resolves to nothing goes to ``unresolved``. Classifying
    here rather than in the ``tool-usage`` hook is deliberate — the recorder observes
    what was typed and the reader judges it, so a future parser miss shows up in the
    bucket instead of being dropped at the point where nobody would see it.

    Read-only, and the counter file it reads is never rewritten: the historical rows
    are the only fixture the parser has (basicly-3ymj).
    """
    raw = load_usage(repo_root)
    if raw is None:
        return None

    local_bin = _local_bin_path(repo_root)
    tools: list[UsageEntry] = []
    skills: list[UsageEntry] = []
    unresolved: list[UsageEntry] = []
    for name, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        count = entry.get("count")
        if not isinstance(count, int):
            continue
        record = UsageEntry(name, count, str(entry.get("last_used", "")))
        if name.startswith(SKILL_PREFIX):
            skills.append(UsageEntry(name[len(SKILL_PREFIX) :], count, record.last_used))
        elif _resolves(name, commands, local_bin):
            tools.append(record)
        else:
            unresolved.append(record)

    used_skill_names = {entry.name for entry in skills}
    never_used = tuple(slug for slug in sorted(catalog_skill_slugs) if slug not in used_skill_names)
    by_count = lambda entry: (-entry.count, entry.name)  # noqa: E731
    return UsageReport(
        tools=tuple(sorted(tools, key=by_count)),
        skills=tuple(sorted(skills, key=by_count)),
        never_used_skills=never_used,
        unresolved=tuple(sorted(unresolved, key=by_count)),
    )
