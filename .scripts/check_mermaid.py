"""Fail when a committed mermaid block is one the hosting renderer refuses to draw.

`architecture.md` carries most of this repository's diagrams and the README carries one,
and nothing checked any of them (basicly-yy82zy, backlog B3): a block with an error renders
as a red box on the hosting site, and every gate here was blind to it. One revision named a
`sequenceDiagram` participant `Loop`, which collides with mermaid's `loop` keyword.

**The criterion is renders, never parses.** `mermaid.parse` stops after the grammar; it never
runs the diagram's own `draw`, which is where a well-formed block still fails. Measured here
against 11.16.1, three blocks parse clean and refuse to render: a subgraph whose id repeats a
node id, a `gantt` task with an unparseable date, and a `stateDiagram-v2` note attached to a
state that does not exist. A gate written to `parse` passes all three.

**The renderer is pinned to what the hosting surface serves**, because a check pinned to the
wrong renderer is a gate that agrees with itself. `.github/workflows/pages.yml` publishes
`site/` alone and `site/` holds no markdown, so Pages renders none of these blocks: the
surface a reader sees is github.com's own markdown view, which draws mermaid in an iframe
from `viewscreen.githubusercontent.com/markdown/mermaid`. That bundle stamps `11.16.1` into
its `renderer.draw` call, and `package.json` pins the same version. The report prints both,
and a drift between them fails — a line printed by a passing gate is a line nobody reads.

Run over every tracked document, or over named ones::

    uv run python .scripts/check_mermaid.py
    uv run python .scripts/check_mermaid.py docs/architecture/architecture.md
"""

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
# The surface a reader actually looks at, and the mermaid build it served when this was
# established. Re-establish both together: fetch the bundle named by
# `viewscreen.githubusercontent.com/markdown/mermaid` and read the version literal its
# `renderer.draw` call carries, then move the `mermaid` pin in `package.json` to match.
HOSTING_SURFACE = "github.com markdown view (viewscreen.githubusercontent.com/markdown/mermaid)"
HOSTING_VERSION = "11.16.1"
HOSTING_ESTABLISHED = "2026-08-21"

_FENCE = re.compile(r"^(?P<indent>\s*)(?P<ticks>```+|~~~+)\s*(?P<info>\S*)\s*$")
_MERMAID = "mermaid"


class RendererError(RuntimeError):
    """The renderer could not be run, so nothing was checked."""


@dataclass(frozen=True)
class Block:
    """One fenced mermaid block, at the line its opening fence sits on."""

    doc: str
    line: int
    text: str


def tracked_docs(root: Path) -> tuple[Path, ...]:
    """Every tracked markdown file, from git rather than a glob.

    A glob would walk `node_modules`, which this check installs into and which holds
    hundreds of vendored documents none of this repository's authors wrote.
    """
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
    """The mermaid blocks in one document.

    Every fence is tracked, not only the mermaid ones, so a mermaid fence quoted inside a
    longer outer fence stays quoted. Tracking mermaid alone read the inner fence of
    ``````` ```` / ```mermaid ``````` as a diagram and then took the outer close as its own.
    """
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
    """Every mermaid block in *docs*, in document then line order.

    A document named on the command line need not sit under *root*, so the label falls
    back to the path as given rather than raising on a path outside the tree.

    Raises:
        RendererError: Nothing was found. An empty population is this step failing, not
            the tree passing — the repository's diagrams are why the gate exists, so zero
            of them means the fence scan stopped matching.
    """
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
    """Draw every block, as (the mermaid version that drew them, failures by index).

    *mode* is always ``render`` here. ``parse`` is the weaker instrument this gate exists
    to reject, and only the tests ask for it, to keep that comparison a measurement.

    Raises:
        RendererError: node or the mermaid install is missing, or the renderer crashed
            before reaching the blocks. Never a skip — a skip reads as a pass, and this
            gate exists because a reader sees the red box a passing gate did not.
    """
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
    """The failing blocks, each named by file, line, renderer version and message.

    The whole message, not its first line: a mermaid parse error puts the offending
    token and its caret on the lines *after* the word "Parse error", so a one-line
    report names the failure without saying what in the diagram caused it.
    """
    lines: list[str] = []
    for index in sorted(failures):
        block = blocks[index]
        lines.append(f"{block.doc}:{block.line}: mermaid {version} refused this block")
        detail = failures[index].strip().splitlines() or ["no message"]
        lines += [f"    {part}" for part in detail]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point: report every diagram a reader would see as a red error box."""
    parser = argparse.ArgumentParser(description=__doc__)
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
