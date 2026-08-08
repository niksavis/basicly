"""Count which terminal tools and skills the agent actually invokes (PostToolUse hook).

Fired by Claude Code (``PostToolUse``, matcher ``Bash|Skill``) and GitHub
Copilot (``postToolUse``) after a tool call. Reads the hook JSON from stdin;
for a shell call it extracts the head token of every pipeline segment in the
executed command, and for a Claude ``Skill`` call it records the skill as a
``skill:<name>`` entry. Both increment per-entry counters in
``.basicly/usage/tool-usage.json`` — real data for culling idle tools/skills
from the catalog.

What a shell command *ran* is ``shell_tokens``'s answer, not this module's: the
boundary is recording against parsing. Everything here reads a payload, decides what
is worth counting and writes it down; nothing here looks at shell syntax.

Telemetry, never a gate: every path exits 0, the usage dir ignores itself
(``.basicly/usage/.gitignore``), writes are atomic, and a corrupt counter file
restarts empty instead of failing the agent's tool call.
"""

from __future__ import annotations

import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

# The sibling parser, imported the way `kit-boundary.py` imports `check_runner`: a hook
# is run by path under whatever interpreter the host provides, and a test loads it
# through `spec_from_file_location`, so neither puts this directory on `sys.path`.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from shell_tokens import (
    WRAPPER_TOKENS,
    segment_tokens,
    skip_wrapper_args,
    split_pipeline_segments,
    strip_heredocs,
    tools_in_command,
)

USAGE_DIR = Path(".basicly/usage")
USAGE_FILE = USAGE_DIR / "tool-usage.json"

# The tracker-surface ledger's spool (basicly-vkh0.1). Phase 6 freezes the
# replacement's scope from a *measured* surface list, and the engine half of that
# measurement is recorded in src/basicly/tracker_usage.py. This hook records the
# other half: a br/bv call typed by an agent or a human in a shell, which the
# engine seam never sees. Same file and same record shape, distinguished by
# `site`, so one reader answers both.
#
# Duplicated here rather than imported because a hook runs as a standalone script
# under whatever interpreter the host provides, with no guarantee that this
# repo's package is importable — the format is the contract, and the ledger's
# reader discards anything malformed.
TRACKER_SPOOL = USAGE_DIR / "tracker-usage.jsonl"
# Recording is opt-in by the presence of this committed directory, so a consumer
# repo is never written to uninvited.
TRACKER_LEDGER_DIR = Path(".basicly/ledger")
TRACKER_BINARIES = {"br", "bv"}
# Kept in step with tracker_usage.GROUP_SUBCOMMANDS: these take a second word that
# names a distinct operation, so the pair is one surface. A parity test compares the
# two sets, because "kept in step" as a comment had already drifted — this set
# was missing six real groups (`audit`, `doctor`, `epic`, `history`, `label`,
# `query`) and carried `catalog`, which is a basicly command br has never had.
TWO_WORD_SUBCOMMANDS = {
    "audit",
    "comments",
    "config",
    "coordination",
    "dep",
    "doctor",
    "epic",
    "gate",
    "history",
    "label",
    "query",
    "robot-docs",
}
# Mirrors tracker_usage._SURFACE_WORD. A positional that cannot be a br command
# name is shell text that survived tokenisation (`2>&1`, an unexpanded `$g`), and
# this hook is the half that sees raw shell, so it is where they entered the
# committed ledger.
SURFACE_WORD = re.compile(r"^[a-z][a-z0-9-]*$")

# Tool names that carry a shell command, per platform (Claude: Bash; Copilot:
# bash/shell). Anything else (Edit, view, ...) is not ours to count.
SHELL_TOOLS = {"bash", "shell"}


def _command_from_payload(payload: dict) -> str | None:
    """Return the executed shell command from a Claude or Copilot payload."""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if str(tool).lower() not in SHELL_TOOLS:
        return None
    args = payload.get("tool_input") or payload.get("toolArgs") or {}
    if isinstance(args, dict):
        command = args.get("command")
        return command if isinstance(command, str) else None
    return args if isinstance(args, str) else None


def _skill_from_payload(payload: dict) -> str | None:
    """Return the invoked skill name from a Claude ``Skill`` tool payload."""
    tool = payload.get("tool_name") or payload.get("toolName") or ""
    if str(tool).lower() != "skill":
        return None
    args = payload.get("tool_input") or payload.get("toolArgs") or {}
    if isinstance(args, dict):
        skill = args.get("skill")
        if isinstance(skill, str) and skill:
            return skill
    return None


def tracker_invocations(command: str) -> list[tuple[str, list[str]]]:
    """Every ``br``/``bv`` call in *command*, as ``(binary, args)``.

    Reuses the pipeline splitter, so a tracker call later in a pipeline or after
    ``&&`` is seen, and unwraps the same wrappers ``tools_in_command`` does
    (``uv run br ...``).
    """
    found: list[tuple[str, list[str]]] = []
    for segment in split_pipeline_segments(strip_heredocs(command)):
        tokens = segment_tokens(segment)
        while tokens:
            head = tokens[0]
            if "=" in head and not head.startswith("-"):
                tokens.pop(0)  # VAR=val prefix
                continue
            if Path(head).name in WRAPPER_TOKENS:
                tokens = skip_wrapper_args(tokens[1:])
                continue
            break
        if not tokens:
            continue
        name = Path(tokens[0]).name
        if name in TRACKER_BINARIES:
            found.append((name, tokens[1:]))
    return found


def _split_invocation(args: list[str]) -> tuple[str, list[str]]:
    """The subcommand and sorted flag names — mirrors tracker_usage.split_invocation."""
    words = [arg for arg in args if not arg.startswith("-") and SURFACE_WORD.match(arg)]
    flags = sorted({arg.split("=", 1)[0] for arg in args if arg.startswith("-")})
    if not words:
        return (flags[0] if flags else "", flags)
    subcommand = words[0]
    if len(words) > 1 and subcommand in TWO_WORD_SUBCOMMANDS:
        subcommand = f"{subcommand} {words[1]}"
    return subcommand, flags


def ledger_root(repo_root: Path) -> Path:
    """The checkout owning the ledger, following br's git-ignored ``.beads/redirect``.

    Duplicated from ``tracker_usage.ledger_root`` for the same reason the rest of
    this hook is: it runs as a standalone script under whatever interpreter the host
    provides, with no guarantee the package is importable. A parity test compares the
    two.
    """
    try:
        redirect = repo_root / ".beads" / "redirect"
        if redirect.is_file():
            target = Path(redirect.read_text(encoding="utf-8").strip())
            if target.is_dir() and target.name == ".beads":
                return target.parent
    except OSError:
        return repo_root
    return repo_root


def record_tracker(invocations: list[tuple[str, list[str]]], repo_root: Path) -> None:
    """Append interactive tracker calls to the ledger spool; never raises.

    No duration: PostToolUse fires after the fact and the payload carries no
    timing, so latency per surface is answerable from the engine half only. The
    field is omitted rather than written as zero — a zero would silently drag
    down the mean for any surface an interactive session also uses.
    """
    if not invocations:
        return
    # One ledger per repo, never one per worktree: an agent typing `br` inside a
    # lane worktree would otherwise spool into a directory the loop deletes at
    # teardown, discarding the observation (basicly-vkh0.8). Mirrors
    # tracker_usage.ledger_root.
    root = ledger_root(repo_root)
    # Opt-in by the presence of the committed ledger directory, matching
    # tracker_usage.is_enabled. Without it this hook would create .basicly/usage/
    # in any consumer repo that happens to install us, which is an uninvited
    # write into somebody else's tree.
    if not (root / TRACKER_LEDGER_DIR).is_dir():
        return
    spool = root / TRACKER_SPOOL
    spool.parent.mkdir(parents=True, exist_ok=True)
    gitignore = spool.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    with spool.open("a", encoding="utf-8") as handle:
        for binary, args in invocations:
            subcommand, flags = _split_invocation(args)
            if not subcommand:
                continue
            entry = {
                "binary": binary,
                "flags": flags,
                "ok": True,
                "site": "interactive",
                "subcommand": subcommand,
            }
            handle.write(json.dumps(entry, separators=(",", ":"), sort_keys=True) + "\n")


def record(tools: list[str], repo_root: Path) -> None:
    """Increment counters atomically; a corrupt file restarts empty."""
    if not tools:
        return
    usage_dir = repo_root / USAGE_DIR
    usage_dir.mkdir(parents=True, exist_ok=True)
    gitignore = usage_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    usage_file = repo_root / USAGE_FILE
    try:
        stats = json.loads(usage_file.read_text(encoding="utf-8"))
        if not isinstance(stats, dict):
            stats = {}
    except OSError, json.JSONDecodeError:
        stats = {}

    today = datetime.now(UTC).date().isoformat()
    for tool in tools:
        entry = stats.get(tool)
        count = entry.get("count", 0) if isinstance(entry, dict) else 0
        stats[tool] = {"count": count + 1, "last_used": today}

    tmp = usage_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(usage_file)


def main() -> int:
    """Count the payload's tools; telemetry never fails the agent's tool call."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        command = _command_from_payload(payload)
        if command:
            record(tools_in_command(command), Path.cwd())
            record_tracker(tracker_invocations(command), Path.cwd())
        skill = _skill_from_payload(payload)
        if skill:
            record([f"skill:{skill}"], Path.cwd())
    # A raise here fails a tool call that already succeeded. Narrowing was rejected: the
    # body spans stdin decoding, JSON parsing and two file writes. It reports rather than
    # swallowing — a silent failure is a ledger that quietly stops counting.
    except Exception as exc:  # noqa: BLE001 — hook boundary, reported below
        print(f"tool-usage: telemetry skipped ({type(exc).__name__})", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
