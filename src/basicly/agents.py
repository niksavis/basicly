"""Agent (subagent) source loading, composition, and validation.

Agents are authored as non-discoverable ``agent.yaml`` sources whose body is
composed from shared building blocks (``*.block.yaml``) filling five ordered
slots. The projector renders each agent to ``.claude/agents/<slug>.md`` only —
the one root Claude Code and VS Code both parse natively — per the
single-source emission policy (basicly-2f4): a second root would double-load
in VS Code, which dedupes only skills.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .schema import ValidationError

CORE_AGENTS_DIR = Path(".basicly/core/agents")
OVERLAY_AGENTS_DIR = Path(".basicly-local/agents")
AGENT_SOURCE_FILE = "agent.yaml"
BLOCK_SOURCE_GLOB = "*.block.yaml"
# Shared blocks live in <root>/blocks/, so the name is reserved: no agent slug
# may claim it.
BLOCKS_DIR_NAME = "blocks"
# The composition skeleton every agent fills, in render order. Validated
# independently by Anthropic's official subagent examples and the community
# corpus best-in-class files (research on basicly-ajq).
SLOT_ORDER = ("role", "startup", "process", "output_contract", "constraints")
DEFAULT_MODEL = "inherit"
# GitHub's cloud agent caps the prompt body at 30,000 characters; enforcing the
# cap keeps every composed body portable to the strictest reader.
MAX_BODY_CHARS = 30000
# A posture that declares the agent read-only must not grant mutating tools.
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
READ_ONLY_MARKER = "read-only"


@dataclass(frozen=True)
class BlockDefinition:
    """A shared building block loaded from an agents root."""

    id: str
    description: str
    body: str
    source: str
    override: bool
    source_path: Path


@dataclass(frozen=True)
class SlotItem:
    """One slot entry: a reference to a shared block or inline markdown."""

    kind: str  # "block" | "text"
    value: str


@dataclass(frozen=True)
class AgentDefinition:
    """A source agent loaded from an agents root."""

    slug: str
    purpose: str
    triggers: str
    returns: str
    posture: str
    tools: tuple[str, ...]
    model: str
    claude: tuple[tuple[str, object], ...]
    slots: tuple[tuple[str, tuple[SlotItem, ...]], ...]
    source: str
    override: bool
    source_path: Path

    def slot(self, name: str) -> tuple[SlotItem, ...]:
        """Return the items of the named slot."""
        return dict(self.slots)[name]


def default_agent_roots(repo_root: Path) -> list[tuple[Path, str]]:
    """The core and overlay agents roots in load order (core first)."""
    return [
        (repo_root / CORE_AGENTS_DIR, "core"),
        (repo_root / OVERLAY_AGENTS_DIR, "user"),
    ]


def _load_mapping(path: Path, kind: str) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValidationError(f"invalid YAML: {exc}", path) from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{kind} source must be a YAML mapping", path)
    return data


def _require_str(value: object, field: str, path: Path) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"missing required field '{field}'", path)
    return value


def discover_blocks(roots: list[tuple[Path, str]]) -> dict[str, BlockDefinition]:
    """Load shared blocks from the given roots; later roots may override by id."""
    blocks: dict[str, BlockDefinition] = {}

    for root, source in roots:
        blocks_dir = root / BLOCKS_DIR_NAME
        if not blocks_dir.is_dir():
            continue
        for path in sorted(blocks_dir.glob(BLOCK_SOURCE_GLOB)):
            data = _load_mapping(path, "block")
            block_id = _require_str(data.get("id"), "id", path).strip()
            expected_name = f"{block_id}.block.yaml"
            if path.name != expected_name:
                raise ValidationError(
                    f"block file must be named '{expected_name}' to match its id", path
                )
            block = BlockDefinition(
                id=block_id,
                description=_require_str(data.get("description"), "description", path).strip(),
                body=_require_str(data.get("body"), "body", path).strip("\n"),
                source=data.get("source", source),
                override=bool(data.get("override", False)),
                source_path=path,
            )
            existing = blocks.get(block_id)
            if existing is not None:
                if existing.source == block.source:
                    raise ValidationError(
                        f"duplicate block id '{block_id}' "
                        f"(first defined in {existing.source_path})",
                        path,
                    )
                if not block.override:
                    raise ValidationError(
                        f"block '{block_id}' shadows a {existing.source} block; "
                        "add 'override: true' to replace it",
                        path,
                    )
            blocks[block_id] = block

    return blocks


def discover_agents(roots: list[tuple[Path, str]]) -> list[AgentDefinition]:
    """Load agents from the given roots; later roots may override by slug."""
    agents: dict[str, AgentDefinition] = {}

    for root, source in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob(f"*/{AGENT_SOURCE_FILE}")):
            slug = path.parent.name
            if slug == BLOCKS_DIR_NAME:
                raise ValidationError(
                    f"'{BLOCKS_DIR_NAME}' is reserved for shared blocks "
                    "and cannot be an agent slug",
                    path,
                )
            agent = _parse_agent(slug, _load_mapping(path, "agent"), path, source)
            existing = agents.get(slug)
            if existing is not None:
                if existing.source == agent.source:
                    raise ValidationError(
                        f"duplicate agent slug '{slug}' (first defined in {existing.source_path})",
                        path,
                    )
                if not agent.override:
                    raise ValidationError(
                        f"agent '{slug}' shadows a {existing.source} agent; "
                        "add 'override: true' to replace it",
                        path,
                    )
            agents[slug] = agent

    return [agents[slug] for slug in sorted(agents)]


def _parse_agent(slug: str, data: dict, path: Path, source: str) -> AgentDefinition:
    name = _require_str(data.get("name"), "name", path).strip()
    if name != slug:
        raise ValidationError(f"agent name '{name}' must match its directory name '{slug}'", path)

    tools = data.get("tools")
    if (
        not isinstance(tools, list)
        or not tools
        or not all(isinstance(tool, str) and tool.strip() for tool in tools)
    ):
        raise ValidationError(
            "tools must be a non-empty list of tool names "
            "(agents never silently inherit every tool)",
            path,
        )

    claude = data.get("claude", {})
    if not isinstance(claude, dict) or not all(isinstance(key, str) for key in claude):
        raise ValidationError("claude must be a mapping of frontmatter passthrough keys", path)

    return AgentDefinition(
        slug=slug,
        purpose=_require_str(data.get("purpose"), "purpose", path).strip(),
        triggers=_require_str(data.get("triggers"), "triggers", path).strip(),
        returns=_require_str(data.get("returns"), "returns", path).strip(),
        posture=_require_str(data.get("posture"), "posture", path).strip(),
        tools=tuple(tool.strip() for tool in tools),
        model=str(data.get("model", DEFAULT_MODEL)).strip() or DEFAULT_MODEL,
        claude=tuple(sorted(claude.items())),
        slots=_parse_slots(data.get("slots"), path),
        source=data.get("source", source),
        override=bool(data.get("override", False)),
        source_path=path,
    )


def _parse_slots(raw: object, path: Path) -> tuple[tuple[str, tuple[SlotItem, ...]], ...]:
    if not isinstance(raw, dict):
        raise ValidationError("slots must be a mapping of the five body slots", path)

    unknown = sorted(set(raw) - set(SLOT_ORDER))
    if unknown:
        raise ValidationError(
            f"unknown slot(s) {', '.join(unknown)}; slots are {', '.join(SLOT_ORDER)}",
            path,
        )

    slots: list[tuple[str, tuple[SlotItem, ...]]] = []
    for slot_name in SLOT_ORDER:
        entries = raw.get(slot_name)
        if not isinstance(entries, list) or not entries:
            raise ValidationError(
                f"slot '{slot_name}' must be a non-empty list of block refs or text items",
                path,
            )
        items: list[SlotItem] = []
        for entry in entries:
            if not isinstance(entry, dict) or len(entry) != 1:
                raise ValidationError(
                    f"slot '{slot_name}' items must set exactly one of 'block' or 'text'",
                    path,
                )
            (key, value), *_ = entry.items()
            if key not in ("block", "text") or not isinstance(value, str) or not value.strip():
                raise ValidationError(
                    f"slot '{slot_name}' items must set exactly one of 'block' or 'text' "
                    "to a non-empty string",
                    path,
                )
            items.append(SlotItem(kind=key, value=value if key == "text" else value.strip()))
        slots.append((slot_name, tuple(items)))

    return tuple(slots)


def compose_description(agent: AgentDefinition) -> str:
    """Join the four description parts; each is authored to end with a period."""
    return " ".join((agent.purpose, agent.triggers, agent.returns, agent.posture))


def unknown_block_refs(agent: AgentDefinition, blocks: dict[str, BlockDefinition]) -> list[str]:
    """Return the block ids the agent references that do not exist."""
    return [
        item.value
        for _, items in agent.slots
        for item in items
        if item.kind == "block" and item.value not in blocks
    ]


def compose_body(agent: AgentDefinition, blocks: dict[str, BlockDefinition]) -> str:
    """Resolve the agent's slots into one markdown body, in slot order."""
    parts: list[str] = []
    for slot_name, items in agent.slots:
        for item in items:
            if item.kind == "text":
                parts.append(item.value.strip("\n"))
                continue
            block = blocks.get(item.value)
            if block is None:
                raise ValidationError(
                    f"agent '{agent.slug}' references unknown block "
                    f"'{item.value}' in slot '{slot_name}'",
                    agent.source_path,
                )
            parts.append(block.body)
    return "\n\n".join(parts)


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def lint_agent_sources(repo_root: Path) -> list[str]:
    """Semantic agent lint over the merged core+overlay set (for catalog-lint)."""
    try:
        roots = default_agent_roots(repo_root)
        blocks = discover_blocks(roots)
        agents = discover_agents(roots)
    except ValidationError as exc:
        return [str(exc)]

    violations: list[str] = []
    for agent in agents:
        rel = _rel(agent.source_path, repo_root)
        missing = unknown_block_refs(agent, blocks)
        for ref in missing:
            violations.append(f"{rel}: references unknown block '{ref}'")
        if missing:
            continue

        body = compose_body(agent, blocks)
        if len(body) > MAX_BODY_CHARS:
            violations.append(
                f"{rel}: composed body is {len(body)} chars; the portable cap is "
                f"{MAX_BODY_CHARS} (GitHub cloud-agent prompt ceiling)"
            )

        if READ_ONLY_MARKER in agent.posture.lower():
            granted = sorted(set(agent.tools) & WRITE_TOOLS)
            if granted:
                violations.append(
                    f"{rel}: posture declares read-only but tools grant {', '.join(granted)}"
                )

    return violations
