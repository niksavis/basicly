from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
NAME = "basicly-tracker"
SCRIPT = 'python3 "<skill-directory>/scripts/tracker.pyz"'


def guidance(source: str) -> str:
    body = source.replace("name: work-tracker", f"name: {NAME}")
    body = body.replace("python3 .basicly/kit/tracker/cli.py", SCRIPT)
    body = body.replace(
        "The optional board kit serves a live page and an HTTP API\n"
        "where a person reads, creates and edits records: see the `tracker-board` skill.",
        f"The bundled `{SCRIPT} serve .basicly/ledger` serves the live page and HTTP API "
        "where a person reads, creates and edits records.",
    )
    body = body.replace(
        "`.basicly/kit/tracker/REFERENCE.md` lists every command with one example.",
        f"`{SCRIPT} --help` lists every command; `REFERENCE.md` beside this skill has examples.",
    )
    location = (
        "\nResolve `<skill-directory>` to the absolute directory containing this SKILL.md. "
        "Run commands from the repository root, with its `.basicly/ledger/`. "
        "The bundled script needs Python 3.9 or later and no engine or third-party package.\n\n"
        "For a new repository, run the bundled script with "
        "`init --sandbox --with-instructions` to install "
        "the ledger, repository instructions and Git claim gate. Do not run init in a "
        "repository already managed by basicly: use its existing ledger and instructions. "
        "In that repository, use `basicly tracker write --` for writes and "
        "`basicly tracker` for reads, following its work-tracker skill.\n\n"
        f"For the human UI, run `{SCRIPT} serve .basicly/ledger`; "
        "keep the server running and give the person the URL it prints.\n"
    )
    front, separator, rest = body.partition("\n---\n")
    if not separator:
        raise ValueError("tracker guidance is missing its closing frontmatter delimiter")
    return front + separator + location + rest


def export(
    package: Path,
    destination: Path,
    version: str,
    bundle: Callable[[Path, Path], Path],
) -> Path:
    kit = package / "kit"
    required = (kit / "GUIDANCE.md", kit / "REFERENCE.md", package / "board" / "server.py")
    for path in required:
        if not path.is_file():
            raise ValueError(f"plugin export needs the built tracker package; missing {path.name}")
    skill = guidance(required[0].read_text(encoding="utf-8"))
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        destination.mkdir()
    except FileExistsError:
        raise ValueError(
            "plugin destination already exists; choose a new --out directory"
        ) from None
    identity = {
        "name": NAME,
        "version": version,
        "description": "Track shared work with a human UI, an agent CLI and story readiness gates.",
        "author": {"name": "basicly"},
    }
    manifest = {"$schema": SCHEMA, **identity}
    (destination / "plugin.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
    )
    compatibility = destination / ".claude-plugin"
    compatibility.mkdir()
    (compatibility / "plugin.json").write_text(
        json.dumps(identity, indent=2) + "\n", encoding="utf-8"
    )
    target = destination / "skills" / NAME
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(skill, encoding="utf-8")
    (target / "REFERENCE.md").write_text(
        required[1]
        .read_text(encoding="utf-8")
        .replace("python3 .basicly/kit/tracker/cli.py", SCRIPT),
        encoding="utf-8",
    )
    bundle(package, target / "scripts" / "tracker.pyz")
    return destination
