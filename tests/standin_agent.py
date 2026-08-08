"""A stand-in coding-agent CLI: a real child process for the runner seam.

Not a test module — an executable fixture. ``tests/test_integration_loop.py``
names it on a ``[[runner.agents]]`` command line, so the loop's dispatch reaches
it through the very ``subprocess.Popen`` in :func:`basicly.runner.run` that a
real ``claude``/``codex``/``copilot`` invocation goes through, rather than around
it (basicly-jr0l.43). That module's docstring states what the arrangement does
and does not prove.

It reads the two facts a dispatched agent has to read out of its prompt — which
bead it is working, and where to write the needs-input sentinel — rather than
being handed them on its argv, so a prompt that stopped carrying either fails
here loudly instead of passing quietly. It writes what an agent writes: a commit
on the worktree's branch, or the sentinel. And it reports tokens in claude's
``stream-json`` envelope, so the dispatch is metered by the same extractor a real
one is.

``STANDIN_AGENT_MODES`` selects the behaviour per bead: a JSON object mapping an
issue id (or ``"default"``) to one of :data:`MODES`. The environment is the seam
because ``runner.run`` hands the dispatched child the parent's environment.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess  # nosec B404 — this fixture's whole job is to be a real process
import sys
from pathlib import Path

MODES_ENV = "STANDIN_AGENT_MODES"
OCCUPANCY_ENV = "STANDIN_AGENT_OCCUPANCY"
DEFAULT_MODE_KEY = "default"

# What this dispatch does with the bead it was handed.
COMMIT = "commit"  # implement and commit on the worktree's branch
NEEDS_INPUT = "needs-input"  # write the sentinel and stop without committing
FAIL = "fail"  # exit non-zero, as a crashed or refused dispatch does
IDLE = "idle"  # finish cleanly having committed nothing
MODES = (COMMIT, NEEDS_INPUT, FAIL, IDLE)

# The dispatch prompt's opening sentence names the bead and its closing
# instruction names the sentinel path. The id match is greedy up to the sentence
# period, so a dotted sub-task id (``fx-2.1``) survives it.
_ISSUE_RE = re.compile(r"tracked issue (\S+)\.\s")
_SENTINEL_RE = re.compile(r"\swrite (\S+) as ")

# Asserted verbatim by the tests, so they are named here rather than duplicated
# as literals on both sides.
FAIL_MESSAGE = "stand-in agent: this dispatch cannot run"
FAIL_CODE = 3
NEEDS_FACT = "which tracker prefix the fixture bead belongs to"
NEEDS_DETAIL = "read the prompt and the bead; neither names one"

# The window occupancy this dispatch reports, and the share of it attributed to
# output. Both are data the test may override; nothing here measures anything.
DEFAULT_OCCUPANCY = 1_200
OUTPUT_TOKENS = 200


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    """Parse the agent CLI's argv, exiting 2 on a flag or value it does not accept."""
    parser = argparse.ArgumentParser(prog="standin-agent")
    parser.add_argument("-p", "--prompt", required=True)
    parser.add_argument("--output-format", choices=("text", "json", "stream-json"), default="text")
    parser.add_argument("--verbose", action="store_true")
    # Accepted because the real CLI accepts it (verified on Claude Code 2.1.226,
    # `--forward-subagent-text`). This parser exists to refuse a flag the agent CLI
    # would refuse, so a flag the harness starts passing has to be added here
    # deliberately rather than tolerated by a permissive stand-in (basicly-u2hl.7).
    parser.add_argument("--forward-subagent-text", action="store_true")
    return parser.parse_args(argv)


def _mode(issue_id: str) -> str:
    """The behaviour configured for *issue_id*; a plain commit when none is."""
    raw = os.environ.get(MODES_ENV)
    modes = json.loads(raw) if raw else {}
    return str(modes.get(issue_id) or modes.get(DEFAULT_MODE_KEY) or COMMIT)


def _from_prompt(pattern: re.Pattern[str], prompt: str, what: str) -> str:
    """The single capture *pattern* takes from *prompt*, or a loud refusal to guess."""
    match = pattern.search(prompt)
    if match is None:
        sys.stderr.write(f"stand-in agent: the dispatch prompt names no {what}\n")
        raise SystemExit(FAIL_CODE)
    return match.group(1)


def _git(cwd: Path, *args: str) -> None:
    """Run git in the worktree, failing this dispatch if it fails."""
    subprocess.run(  # nosec B603 B607 — fixed argv, no shell, and git is the tool
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _commit(cwd: Path, issue_id: str) -> None:
    """Append this dispatch's work to the bead's file and commit it on the branch."""
    name = f"{issue_id}.txt"
    # Appended rather than overwritten: a re-dispatched agent does more work, and
    # a second run with nothing staged would abort the commit instead of making one.
    with (cwd / name).open("a", encoding="utf-8") as handle:
        handle.write(f"work for {issue_id}\n")
    _git(cwd, "add", "--", name)
    _git(cwd, "commit", "-m", f"feat: stand-in work ({issue_id})")


def _write_sentinel(cwd: Path, relative: str) -> None:
    """Write the block-don't-guess sentinel at the path the prompt named."""
    path = cwd / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fact": NEEDS_FACT, "detail": NEEDS_DETAIL}), encoding="utf-8")


def _usage_stream(issue_id: str) -> str:
    """This dispatch's token report, in claude's ``stream-json`` envelope."""
    occupancy = int(os.environ.get(OCCUPANCY_ENV) or DEFAULT_OCCUPANCY)
    turn = {"input_tokens": occupancy - OUTPUT_TOKENS, "output_tokens": OUTPUT_TOKENS}
    events = (
        {"type": "system", "subtype": "init"},
        {"type": "assistant", "message": {"model": "standin-1", "usage": turn}},
        {
            "type": "result",
            "subtype": "success",
            "result": f"worked {issue_id}",
            "total_cost_usd": 0.0,
            "usage": {**turn, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
        },
    )
    return "".join(json.dumps(event) + "\n" for event in events)


def main(argv: list[str] | None = None) -> int:
    """Do one dispatch's worth of work in the current directory, then report usage."""
    args = _parse_argv(sys.argv[1:] if argv is None else argv)
    cwd = Path.cwd()
    issue_id = _from_prompt(_ISSUE_RE, args.prompt, "tracked issue")
    mode = _mode(issue_id)
    if mode not in MODES:
        sys.stderr.write(f"stand-in agent: unknown mode {mode!r}; known: {list(MODES)}\n")
        return FAIL_CODE
    if mode == FAIL:
        sys.stderr.write(FAIL_MESSAGE + "\n")
        return FAIL_CODE
    if mode == NEEDS_INPUT:
        _write_sentinel(cwd, _from_prompt(_SENTINEL_RE, args.prompt, "needs-input sentinel path"))
    elif mode == COMMIT:
        _commit(cwd, issue_id)
    if args.output_format == "stream-json":
        sys.stdout.write(_usage_stream(issue_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
