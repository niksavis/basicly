from __future__ import annotations

import argparse
import json
import re
import subprocess  # nosec B404
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_LABEL = "mermaid"
NODE = "node"
RENDERER_SCRIPT = ".scripts/render_mermaid.mjs"
HOSTING_SURFACE = "github.com markdown view (viewscreen.githubusercontent.com/markdown/mermaid)"
HOSTING_VERSION = "11.16.1"
HOSTING_ESTABLISHED = "2026-08-21"

_FENCE = re.compile(r"^(?P<indent>\s*)(?P<ticks>```+|~~~+)\s*(?P<info>\S*)\s*$")
_MERMAID = "mermaid"


class RendererError(RuntimeError):
    pass


@dataclass(frozen=True)
class Block:
    doc: str
    line: int
    text: str


def tracked_docs(root: Path) -> tuple[Path, ...]:

    try:
        listed = subprocess.run(  # nosec B603 B607
            ["git", "-C", str(root), "ls-files", "-z", "*.md"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RendererError(f"could not list tracked documents: {exc}") from exc
    return tuple(root / name for name in listed.stdout.split("\0") if name)


def blocks_in(doc: Path, label: str) -> list[Block]:

    found: list[Block] = []
    ticks = ""
    start = 0
    diagram = False
    body: list[str] = []
    for number, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
        match = _FENCE.match(line)
        if not ticks:
            if match:
                ticks, start, diagram, body = match["ticks"], number, match["info"] == _MERMAID, []
            continue
        if match and match["ticks"].startswith(ticks) and not match["info"]:
            if diagram:
                found.append(Block(label, start, "\n".join(body)))
            ticks = ""
        else:
            body.append(line)
    return found


def collect(root: Path, docs: tuple[Path, ...]) -> tuple[Block, ...]:

    labelled = []
    for doc in docs:
        if not doc.is_file():
            continue
        inside = root in doc.parents
        labelled.append((doc, doc.relative_to(root).as_posix() if inside else doc.as_posix()))
    found = tuple(block for doc, label in labelled for block in blocks_in(doc, label))
    if not found:
        raise RendererError(f"no mermaid block found in {len(docs)} document(s)")
    return found


def render(
    root: Path, blocks: tuple[Block, ...], mode: str = "render"
) -> tuple[str, dict[int, str]]:

    script = root / RENDERER_SCRIPT
    if not script.is_file():
        raise RendererError(f"{RENDERER_SCRIPT} is missing")
    listed = [{"id": index, "text": block.text} for index, block in enumerate(blocks)]
    payload = json.dumps({"blocks": listed, "mode": mode})
    try:
        done = subprocess.run(  # nosec B603
            [NODE, str(script)],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            cwd=root,
        )
    except OSError as exc:
        raise RendererError(f"could not run {NODE}: {exc}; run `npm install`") from exc
    if done.returncode != 0:
        detail = (done.stderr or done.stdout).strip().splitlines()
        raise RendererError(
            f"the renderer exited {done.returncode}: "
            f"{detail[-1] if detail else 'no output'}; run `npm install`"
        )
    try:
        report = json.loads(done.stdout)
        version = str(report["version"])
        failures = {int(r["id"]): str(r["error"]) for r in report["results"] if r["error"]}
    except (ValueError, KeyError, TypeError) as exc:
        raise RendererError(f"the renderer wrote no usable report: {exc}") from exc
    return version, failures


def report(blocks: tuple[Block, ...], failures: dict[int, str], version: str) -> str:

    lines: list[str] = []
    for index in sorted(failures):
        block = blocks[index]
        lines.append(f"{block.doc}:{block.line}: mermaid {version} refused this block")
        detail = failures[index].strip().splitlines() or ["no message"]
        lines += [f"    {part}" for part in detail]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail when a committed mermaid block is one the renderer refuses to draw."
    )
    parser.add_argument("doc", nargs="*", help="Only check these documents")
    args = parser.parse_args(argv)
    try:
        docs = (
            tuple(Path(name).resolve() for name in args.doc)
            if args.doc
            else tracked_docs(REPO_ROOT)
        )
        blocks = collect(REPO_ROOT, docs)
        version, failures = render(REPO_ROOT, blocks)
    except RendererError as exc:
        print(f"[{_LABEL}] {exc}", file=sys.stderr)
        return 2
    files = len({block.doc for block in blocks})
    provenance = (
        f"rendered by mermaid {version}; {HOSTING_SURFACE} served "
        f"mermaid {HOSTING_VERSION} when established {HOSTING_ESTABLISHED}"
    )
    summary = f"{len(blocks)} block(s) in {files} document(s), {provenance}"
    if version != HOSTING_VERSION:
        print(
            f"[{_LABEL}] {summary}; the pinned renderer no longer matches the hosting one — "
            f"re-establish what the surface serves and move both together",
            file=sys.stderr,
        )
        return 2
    if failures:
        print(f"[{_LABEL}] {summary}; {len(failures)} refused")
        print(report(blocks, failures, version))
        return 1
    print(f"[{_LABEL}] {summary}; all render")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
