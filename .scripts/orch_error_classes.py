from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_QUOTED = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")
_PATH = re.compile(r"(?:~|\.{1,2})?/?(?:[\w.@+-]+/)+[\w.@+-]*")
_HEX = re.compile(r"\b[0-9a-f]{6,}\b")
_NUMBER = re.compile(r"\d+")
_EXIT = re.compile(r"^Exit code (\d+)")
_MISSING = re.compile(r"No such file or directory|does not exist|cannot access|is missing")
_HOOK_NAME = re.compile(r"hook:\s+([\w-]+)")
_HOOK_JOB = re.compile(r"^\S*\s*🥊 ([\w./-]+) \(", re.MULTILINE)
_PRE_HOOK = re.compile(r"(PreToolUse|PostToolUse):(\w+) hook error")
_HOOK_SCRIPT = re.compile(r"([\w-]+)\.(?:py|sh|mjs|js)\b")
_JS_ERROR = re.compile(r"^(ReferenceError|TypeError|SyntaxError|RangeError)\b")
_REASON = re.compile(r"Reason: \[([^\]]+)\]")
_PROBES = {
    "git rev-parse",
    "git merge-base",
    "git cat-file",
    "git show-ref",
    "git diff",
    "tmux has-session",
}
_RISKY = re.compile(
    r"\brm\s+-\w*[rf]|git\s+(?:\S+\s+)*(?:push|reset|clean|worktree\s+remove|branch\s+-D)|--delete\b|--force\b"
)
_EXPECTED_ONE = {"rg", "grep", "egrep", "fgrep", "test", "[", "diff", "find", "cmp"}
_SUBCOMMAND = {
    "git",
    "uv",
    "gh",
    "npm",
    "npx",
    "basicly",
    "claude",
    "herdr",
    "tmux",
    "docker",
    "cargo",
}
_TEXT_RULES = (
    ("classifier-no-verdict", re.compile(r"classifier gave no verdict")),
    ("read-before-write", re.compile(r"File has not been read yet")),
    ("edit-no-match", re.compile(r"String to replace not found")),
    ("repl-timeout", re.compile(r"^Timed out after")),
    ("repl-name-taken", re.compile(r"would overwrite a name")),
    ("interrupted", re.compile(r"^\[?(?:Tool call )?[Ii]nterrupted")),
    ("not-in-repl", re.compile(r"is not available in the async REPL")),
    ("repl-start-folder-missing", re.compile(r"the directory the REPL call started in")),
    ("repl-folder-gone", re.compile(r"is missing or not a directory, so the command did not run")),
    (
        "missing-path",
        re.compile(r"No such file or directory|does not exist|cannot access|is missing"),
    ),
)


@dataclass
class Use:
    tool: str
    data: Any
    parent: str | None


@dataclass
class Result:
    tool_use_id: str
    stamp: str | None
    error: bool
    text: str
    decision: dict[str, Any]
    uuid: str | None
    session: str
    project: str


def signature(text: str) -> str:
    text = _QUOTED.sub("Q", text)
    text = _PATH.sub("P", text)
    text = _HEX.sub("H", text)
    return _NUMBER.sub("N", text).strip()[:60]


def command_of(use: Use | None) -> str:
    if use is None or not isinstance(use.data, dict):
        return ""
    for key in ("command", "code", "file_path", "prompt"):
        value = use.data.get(key)
        if isinstance(value, str):
            return value
    return json.dumps(use.data, sort_keys=True)[:200]


def _segments(command: str) -> list[list[str]]:
    try:
        words = shlex.split(command.replace("\n", " ; "), posix=True)
    except ValueError:
        words = command.split()
    segments, current = [], []
    for word in words:
        if word in {"&&", "||", ";", "|"} or word.endswith(";"):
            stripped = word.rstrip(";")
            if stripped and stripped not in {"&&", "||", "|"}:
                current.append(stripped)
            if current:
                segments.append(current)
            current = []
        else:
            current.append(word)
    if current:
        segments.append(current)
    return segments


def _program(words: list[str]) -> str:
    rest = [word for word in words if not re.match(r"^[A-Za-z_]\w*=", word)]
    while rest and rest[0] in {"timeout", "env", "nice", "time", "taskset", "uvx"}:
        rest = rest[1:]
        while rest and (rest[0].startswith("-") or rest[0][:1].isdigit()):
            rest = rest[1:]
    if not rest:
        return ""
    name = Path(rest[0]).name
    if re.fullmatch(r"python[\d.]*", name) and len(rest) > 1 and rest[1].endswith(".py"):
        script = "/".join(Path(rest[1]).parts[-2:])
        verb = rest[2] if len(rest) > 2 and re.fullmatch(r"[a-z][\w-]*", rest[2]) else ""
        return f"{name} {script} {verb}".strip()
    if name in _SUBCOMMAND:
        tail = rest[1:]
        while tail and tail[0].startswith("-"):
            tail = (
                tail[2:]
                if tail[0] in {"-C", "-L", "-S", "--project", "--directory", "--from", "-c"}
                else tail[1:]
            )
        if name == "uv" and tail[:1] == ["run"]:
            inner = tail[1:]
            while inner and inner[0].startswith("-"):
                inner = (
                    inner[2:]
                    if inner[0] in {"--project", "--directory", "--with", "--python", "--from"}
                    else inner[1:]
                )
            return "uv run " + _program(inner) if inner else "uv run"
        return f"{name} {tail[0]}" if tail else name
    return name


def shown_input(kind: str, command: str) -> str:
    lines = [line for line in command.splitlines() if line.strip()]
    if "|classifier|" in kind:
        risky = [line for line in lines if _RISKY.search(line)]
        lines = risky or lines
    return lines[0][:300] if lines else ""


def programs(command: str) -> list[str]:
    return [
        name for name in (_program(words) for words in _segments(command)) if name and name != "cd"
    ]


def _refusal_kind(result: Result, use: Use | None) -> str | None:
    text = result.text
    if _EXIT.match(text):
        return "missing-path" if _MISSING.search(text) else None
    if "classifier gave no verdict" in text:
        return "classifier-no-verdict"
    refused = (
        result.decision.get("decision") == "reject"
        and result.decision.get("reasonType") == "classifier"
    )
    if refused or "denied by the Claude Code auto mode classifier" in text:
        category = result.decision.get("classifierCategory")
        match = _REASON.search(text)
        return f"classifier|{category or (match[1] if match else 'unknown')}"
    hook = _PRE_HOOK.search(text)
    if hook:
        script = _HOOK_SCRIPT.findall(text)
        return f"{hook[1].lower()}|{script[-1] if script else signature(text)}"
    if text.startswith(("Permission denied for", "Permission to use")):
        names = programs(command_of(use))
        return f"permission-denied|{names[0] if names else ''}"
    return next((kind for kind, pattern in _TEXT_RULES if pattern.search(text)), None)


def _output_kind(result: Result, use: Use | None) -> str:
    first = next((line for line in result.text.splitlines() if line.strip()), "")
    js = _JS_ERROR.match(first)
    if js:
        return f"js|{js[1]}"
    exit_code = _EXIT.match(first)
    if exit_code:
        return _exit_kind(int(exit_code[1]), result.text, command_of(use))
    return f"other|{signature(first)}"


def classify(result: Result, use: Use | None) -> str:
    tool = use.tool if use else "?"
    return f"{tool}|{_refusal_kind(result, use) or _output_kind(result, use)}"


def _exit_kind(code: int, text: str, command: str) -> str:
    names = programs(command)
    jobs = _HOOK_JOB.findall(text)
    hook = _HOOK_NAME.search(text)
    if jobs and hook:
        return f"git-hook|{hook[1]}|{','.join(sorted(set(jobs)))}"
    last = names[-1] if names else ""
    if code in {1, 128} and last in _PROBES:
        return f"probe|{last}"
    if re.search(r"^FAILED tests/|\b\d+ failed\b", text, re.MULTILINE):
        return f"test-failure|{names[-1] if names else ''}"
    fatal = re.search(r"^fatal: (.*)$", text, re.MULTILINE)
    if fatal:
        return f"git-fatal|{signature(fatal[1])}"
    if code == 1 and last.split(" ")[0] in _EXPECTED_ONE:
        return f"expected-exit|{last}"
    return f"exit|{last}|{code}"
