"""Shared source factories for the agent suites (basicly-u2hl.52).

Split out when the module-size ratchet refused ``test_agents.py``. The same
shape ``model_map_helpers`` already has: two suites need one set of factories,
so the factories get a module rather than a copy each.
"""

from __future__ import annotations

from pathlib import Path

from basicly.agents import (
    SLOT_ORDER,
)


def _write_block(root: Path, block_id: str, body: str = "Block body.", **extra: object) -> None:
    lines = [
        "schema_version: 1",
        f"id: {block_id}",
        f"description: the {block_id} block",
    ]
    for key, value in extra.items():
        lines.append(f"{key}: {value}")
    lines.append("body: |")
    lines.extend(f"  {line}" for line in body.split("\n"))
    path = root / "blocks" / f"{block_id}.block.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _agent_yaml(
    slug: str,
    *,
    tools: str = "[Read, Grep, Glob]",
    posture: str = "Read-only.",
    slots: str | None = None,
    extra: str = "",
) -> str:
    if slots is None:
        slots = "\n".join(
            f"  {name}:\n    - text: |\n        The {name} slot." for name in SLOT_ORDER
        )
    return (
        f"schema_version: 1\n"
        f"name: {slug}\n"
        f"purpose: Reviews things.\n"
        f"triggers: Use proactively after changes.\n"
        f"returns: Returns prioritized findings.\n"
        f"posture: {posture}\n"
        f"tools: {tools}\n"
        f"{extra}"
        f"slots:\n{slots}\n"
    )


def _write_agent(root: Path, slug: str, content: str | None = None) -> None:
    path = root / slug / "agent.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if content is not None else _agent_yaml(slug), encoding="utf-8")


def _roots(tmp_path: Path) -> list[tuple[Path, str]]:
    return [(tmp_path / "core", "core"), (tmp_path / "user", "user")]
