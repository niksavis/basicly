from __future__ import annotations

import argparse
import re
import shlex
import subprocess  # nosec B404
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

TUTORIAL = Path("docs/tutorial/first-loop.md")
FENCE = re.compile(r"^```(\w*)\s*$")
PINNED = re.compile(r"https://github\.com/\S+/basicly-[\d.]+-py3-none-any\.whl")
PLACEHOLDER = re.compile(r"<[a-z][a-z -]*>")
CREATE_FILE = re.compile(r"create `([^`]+)` with:\s*$")
CREATED = re.compile(r"^created: (\S+)$", re.MULTILINE)
SHA = re.compile(r"(?=[0-9a-f]*\d)[0-9a-f]{7,40}")
SECONDS = re.compile(r"\d+(?:\.\d+)?s\b")
TIMINGS = re.compile(r"\((?:\w+ \d+(?:\.\d+)?s(?:, )?)+\)")
REPO = "myrepo"


@dataclass
class Block:
    kind: str
    body: str
    line: int
    intro: str


def blocks(text: str) -> list[Block]:
    found: list[Block] = []
    lines = text.splitlines()
    index = 0
    while index < len(lines):
        opened = FENCE.match(lines[index])
        if not opened or not opened.group(1):
            index += 1
            continue
        start = index
        index += 1
        body = []
        while index < len(lines) and lines[index].strip() != "```":
            body.append(lines[index])
            index += 1
        intro = next((one for one in reversed(lines[:start]) if one.strip()), "")
        found.append(Block(opened.group(1), "\n".join(body), start + 1, intro))
        index += 1
    return found


def pattern(expected: str) -> re.Pattern[str]:
    alternatives = (
        r"\.\.\.",
        r"/path/to/\S*?(?=\s|$)",
        TIMINGS.pattern,
        SHA.pattern,
        SECONDS.pattern,
    )
    parts = re.split("(" + "|".join(alternatives) + ")", expected)
    regex = ""
    for part in parts:
        if not part:
            continue
        if part == "..." or part.startswith("/path/to/") or TIMINGS.fullmatch(part):
            regex += ".*"
        elif SHA.fullmatch(part):
            regex += "[0-9a-f]{7,40}"
        elif SECONDS.fullmatch(part):
            regex += r"\d+(?:\.\d+)?s"
        else:
            regex += re.escape(part)
    return re.compile(r"^\s*" + regex + r"\s*$")


def mismatch(expected: str, actual: str) -> str | None:
    lines = [line.rstrip() for line in actual.splitlines()]
    for wanted in expected.splitlines():
        if not wanted.strip() or wanted.strip() == "...":
            continue
        matcher = pattern(wanted.rstrip())
        if not any(matcher.match(line) for line in lines):
            return wanted
    return None


def run(script: str, cwd: Path, wheel: Path) -> tuple[int, str, Path]:
    marker = cwd.parent / ".walk-cwd"
    prelude = f'basicly() {{ uvx --from {shlex.quote(str(wheel))} basicly "$@"; }}\n'
    shell = prelude + script + f"\nstatus=$?\npwd > {shlex.quote(str(marker))}\nexit $status\n"
    result = subprocess.run(  # nosec B603 B607
        ["bash", "-c", shell], cwd=cwd, capture_output=True, text=True, check=False
    )
    after = Path(marker.read_text(encoding="utf-8").strip()) if marker.is_file() else cwd
    return result.returncode, result.stdout + result.stderr, after


def prepare(root: Path) -> Path:
    repo = root / REPO
    repo.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.name", "Tutorial Walk"],
        ["git", "config", "user.email", "walk@example.invalid"],
        ["git", "commit", "-q", "--allow-empty", "-m", "chore: start"],
    ):
        subprocess.run(argv, cwd=repo, check=True)  # nosec B603 B607
    return repo


def walk(tutorial: Path, wheel: Path, root: Path) -> list[str]:
    found = blocks(tutorial.read_text(encoding="utf-8"))
    cwd = prepare(root)
    names: dict[str, str] = {}
    failures: list[str] = []
    for position, block in enumerate(found):
        body = block.body
        for old, new in names.items():
            body = body.replace(old, new)
        target = CREATE_FILE.search(block.intro)
        if block.kind == "json" and target:
            (cwd / target.group(1)).write_text(body + "\n", encoding="utf-8")
            continue
        if block.kind != "sh":
            continue
        body = PINNED.sub(lambda _match: shlex.quote(str(wheel)), body)
        if PLACEHOLDER.search(body):
            continue
        code, output, cwd = run(body, cwd, wheel)
        following = found[position + 1] if position + 1 < len(found) else None
        expected = following.body if following and following.kind == "text" else None
        if expected is not None:
            for old, new in names.items():
                expected = expected.replace(old, new)
            for documented, actual in zip(
                CREATED.findall(expected), CREATED.findall(output), strict=False
            ):
                names[documented] = actual
                expected = expected.replace(documented, actual)
        where = f"{tutorial}:{block.line}"
        if code != 0 and expected is None:
            failures.append(f"{where}: exited {code}\n{output}")
            break
        if expected is not None and (missing := mismatch(expected, output)) is not None:
            failures.append(f"{where}: output lacks the documented line {missing!r}\n{output}")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="run the tutorial against a built wheel")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--tutorial", type=Path, default=TUTORIAL)
    args = parser.parse_args(argv)
    with tempfile.TemporaryDirectory(prefix="tutorial-walk-") as directory:
        failures = walk(args.tutorial.resolve(), args.wheel.resolve(), Path(directory))
    for failure in failures:
        print(failure, file=sys.stderr)
    print(f"tutorial walk: {len(failures)} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
