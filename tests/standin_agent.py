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

COMMIT = "commit"
NEEDS_INPUT = "needs-input"
FAIL = "fail"
IDLE = "idle"
MODES = (COMMIT, NEEDS_INPUT, FAIL, IDLE)

_ISSUE_RE = re.compile(r"tracked issue (\S+)\.\s")
_SENTINEL_RE = re.compile(r"\swrite (\S+) as ")

FAIL_MESSAGE = "stand-in agent: this dispatch cannot run"
FAIL_CODE = 3
NEEDS_FACT = "which tracker prefix the fixture bead belongs to"
NEEDS_DETAIL = "read the prompt and the bead; neither names one"

DEFAULT_OCCUPANCY = 1_200
OUTPUT_TOKENS = 200


def _parse_argv(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="standin-agent")
    parser.add_argument("-p", "--prompt", required=True)
    parser.add_argument("--output-format", choices=("text", "json", "stream-json"), default="text")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--forward-subagent-text", action="store_true")
    return parser.parse_args(argv)


def _mode(issue_id: str) -> str:
    raw = os.environ.get(MODES_ENV)
    modes = json.loads(raw) if raw else {}
    return str(modes.get(issue_id) or modes.get(DEFAULT_MODE_KEY) or COMMIT)


def _from_prompt(pattern: re.Pattern[str], prompt: str, what: str) -> str:
    match = pattern.search(prompt)
    if match is None:
        sys.stderr.write(f"stand-in agent: the dispatch prompt names no {what}\n")
        raise SystemExit(FAIL_CODE)
    return match.group(1)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(  # nosec B603 B607 — fixed argv, no shell, and git is the tool
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _commit(cwd: Path, issue_id: str) -> None:
    name = f"{issue_id}.txt"
    with (cwd / name).open("a", encoding="utf-8") as handle:
        handle.write(f"work for {issue_id}\n")
    _git(cwd, "add", "--", name)
    _git(cwd, "commit", "-m", f"feat: stand-in work ({issue_id})")


def _write_sentinel(cwd: Path, relative: str) -> None:
    path = cwd / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"fact": NEEDS_FACT, "detail": NEEDS_DETAIL}), encoding="utf-8")


def _usage_stream(issue_id: str) -> str:
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
