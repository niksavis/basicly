"""Agent (subagent) source loading, composition, and validation.

Agents are authored as non-discoverable ``agent.yaml`` sources whose body is
composed from shared building blocks (``*.block.yaml``) filling five ordered
slots. The projector renders each agent into every root in
``AGENTS_OUTPUT_ROOTS`` — ``.claude/agents/<slug>.md`` for the Claude family and
``.github/agents/<slug>.agent.md`` for the GitHub Copilot family. Portability is
still kept in the *content* (the portable frontmatter core and the 30,000-char
body cap); the second root exists because the copilot cloud agent reads only its
own root, not because the content differs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from .projection import SyncResult, sync_file
from .schema import MODEL_TIERS, ValidationError, technology_selected, validate_technologies

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
# The deprecated per-agent model key, superseded by the portable `tier` (see
# schema.MODEL_TIERS). It stays a *known* property in agent.schema.json rather
# than being deleted: that schema sets additionalProperties: false, so deleting
# it would fail a legacy source with a bare "Additional properties are not
# allowed ('model' was unexpected)" that names no replacement, and
# catalog_lint._missing_required can only suppress a `required` error. Keeping
# the property lets lint_agent_sources own the actionable diagnostic instead.
DEPRECATED_MODEL_KEY = "model"
# GitHub's cloud agent caps the prompt body at 30,000 characters; enforcing the
# cap keeps every composed body portable to the strictest reader.
MAX_BODY_CHARS = 30000
# GitHub's published tool-alias table for copilot custom agents, pinned here as
# reviewed data (reviewed 2026-07-31 against
# docs.github.com/en/copilot/reference/custom-agents-configuration, basicly-8sxf).
# The key fact: copilot accepts Claude Code's own PascalCase tool names as
# first-class aliases and matches them case-insensitively, so projecting to the
# copilot root needs *no* translation layer — the names we already declare
# resolve on both families. Both a comma-separated string and a YAML array are
# accepted; an unset `tools` defaults to all tools, which is why this module
# refuses a source without an explicit allowlist.
#
# The table is pinned rather than assumed because copilot drops an unrecognised
# entry *silently* — measured on the copilot CLI, whose `--log-level debug` logs
# the granted tool schemas — where Claude Code refuses to launch and names the
# unresolved entries. `resolve_copilot_tool` plus the lint rule in
# `lint_agent_sources` restore the loud failure at authoring time. GitHub
# publishes no enumerated tool list, so this alias table is the entire
# vocabulary we can check a declared name against.
#
# Do NOT merge this with copilot's other tool vocabularies: VS Code chat uses
# `#`-prefixed namespaced names and tool *sets* (`#read/readFile`), and the
# copilot CLI's internal names are different again (view, grep, glob, bash,
# create, edit, skill, sql). They are separate vocabularies for separate
# surfaces; only this one is the config format for an agent file.
COPILOT_TOOL_ALIASES: dict[str, frozenset[str]] = {
    "execute": frozenset({"shell", "Bash", "powershell"}),
    "read": frozenset({"Read", "NotebookRead"}),
    "edit": frozenset({"Edit", "MultiEdit", "Write", "NotebookEdit"}),
    "search": frozenset({"Grep", "Glob"}),
    "agent": frozenset({"custom-agent", "Task"}),
    "web": frozenset({"WebSearch", "WebFetch"}),
    "todo": frozenset({"TodoWrite"}),
}
# Folded alias -> primary, so a declared name resolves in one lookup. A primary
# is its own alias: `tools: [read]` is as valid as `tools: [Read]`.
_COPILOT_TOOL_BY_ALIAS = {
    alias.casefold(): primary
    for primary, aliases in COPILOT_TOOL_ALIASES.items()
    for alias in (primary, *aliases)
}
# The same names as *authored*, for the lint remedy: a folded key is not
# something an author can copy into a source, and the PascalCase spellings are
# the ones a Claude-shaped source already uses.
_COPILOT_TOOL_NAMES = tuple(
    sorted(
        set(COPILOT_TOOL_ALIASES)
        | {alias for aliases in COPILOT_TOOL_ALIASES.values() for alias in aliases},
        key=str.casefold,
    )
)
# What our allowlist does NOT control on copilot, all measured 2026-07-31 on the
# copilot CLI against the logged tool schemas (basicly-8sxf). Record honestly:
# a "read-only" agent there is narrower than an unconstrained one but is not the
# read-only set this module names.
#   - `skill` and `sql` are granted UNCONDITIONALLY and the allowlist cannot
#     suppress them, so every agent we certify read-only holds two tools we never
#     declared (`sql` writes a per-session SQLite db, not the repo).
#   - `Bash` resolves to four tools — bash, read_bash, stop_bash, list_bash —
#     the same capability class over a wider surface.
#   - `NotebookEdit` alone resolves to both `create` AND `edit`, i.e. general
#     filesystem write: Claude's narrowest write tool is copilot's broadest.
#   - Expansion is not uniform: `Glob` did not pull in grep. A name with a 1:1
#     CLI counterpart maps narrowly, one without falls back to the broader
#     primary set.
# The guarantee that does hold: an unrecognised entry fails SAFE. An
# all-unrecognised list resolved to zero requested tools, with no grant-all
# fallback, so a typo costs function, not the read-only posture.
#
# A posture that declares the agent read-only must not grant mutating tools.
# Matched case-insensitively (basicly-e9jc): copilot's aliases are case
# insensitive, so a lowercase `edit` grants exactly the writes `Edit` does and
# has to fail the same check. Notes on the membership:
#   - `MultiEdit` is off Claude Code's published tool list but copilot still
#     accepts it as an alias of `edit`, so dropping it would only reopen a hole.
#   - `Create` is the copilot CLI's file-creating primary with no claude
#     equivalent — the same write grant under a name this set would otherwise
#     miss. It is deliberately not in COPILOT_TOOL_ALIASES: that table is
#     GitHub's published config vocabulary, and `create` is not in it.
WRITE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit", "Create"})
# Unioned with every alias of copilot's `edit` primary so the pinned table drives
# the posture check: if GitHub adds a write alias, updating COPILOT_TOOL_ALIASES
# extends the check instead of leaving a hole only a reader would notice.
_WRITE_TOOLS_FOLDED = frozenset(
    tool.casefold() for tool in (*WRITE_TOOLS, "edit", *COPILOT_TOOL_ALIASES["edit"])
)
READ_ONLY_MARKER = "read-only"
# Frontmatter keys the renderer owns; the claude passthrough map may not shadow
# them. `model` stays on the list even though nothing renders it any more:
# otherwise the passthrough is a back door that re-injects a provider model id
# into frontmatter and defeats the lint rule below.
RESERVED_FRONTMATTER_KEYS = frozenset({"name", "description", "tools", "model"})
# Marker injected into every rendered agent file so a hand-edit of the projected
# copy is obviously wrong — the YAML source is the thing to edit.
GENERATED_MARKER = (
    "<!-- Generated by `basicly agents-build` from agent.yaml. Do not edit; edit the source. -->"
)


@dataclass(frozen=True)
class AgentOutputRoot:
    """One projected agent root: where files land, how they are named, what renders."""

    family: str
    path: Path
    # Appended to the slug. Copilot's own template names the file
    # `<name>.agent.md`; Claude Code reads a plain `<name>.md`.
    suffix: str
    # Whether this family honors the source's `claude:` frontmatter passthrough.
    claude_passthrough: bool

    def target(self, repo_root: Path, slug: str) -> Path:
        """The projected file for *slug* under this root."""
        return repo_root / self.path / f"{slug}{self.suffix}"


# Every projected agent root. Both are written by `agents-build` and both are
# compared by `agents-check` — there is deliberately no opt-in flag, because a
# root only some commands write is exactly how a second root drifts unnoticed.
#
# basicly-ajq originally kept `.claude/agents` as the single root, on the reading
# that Claude Code and VS Code both parse it natively so one copy serves both.
# That was true and is still true, but it only ever covered VS Code. Reopened
# with measured facts on basicly-8sxf (2026-07-31):
#   - The copilot CLOUD agent reads only `.github/agents/<name>.agent.md` (the
#     org/enterprise equivalent is a root `agents/` directory in a `.github`
#     repo, which is not a repo-level projection target). Our subagents did not
#     exist for it at all.
#   - The copilot CLI does discover `.claude/agents/*.md`, measured — but GitHub
#     documents only `.github/agents/` and `~/.copilot/agents/`. Depending on
#     undocumented discovery that no gate would catch if it regressed is the real
#     defect this root fixes.
#   - Copilot custom agents support a `tools` allowlist (COPILOT_TOOL_ALIASES),
#     so the read-only posture check survives the crossing. That removes the
#     objection that declined the codex root in basicly-crkl.
#   - Copilot cannot intercept a spawn, so static per-surface emission is the only
#     path a declared `tier` can ever reach it by (basicly-a3yi).
# The double-load worry does not materialise: GitHub documents that the config
# file's name minus `.md`/`.agent.md` is the deduplication key, so `<slug>.md`
# and `<slug>.agent.md` collapse to one agent.
#
# Still declined: `.codex/agents/*.toml` (codex project subagents, basicly-crkl,
# 2026-07-31). Its documented field set is name/description/
# developer_instructions with no `tools` equivalent, so a codex copy would
# silently drop the mandatory allowlist this module validates against a read-only
# posture — a lost guarantee, not just a format cost. Codex gets the same
# guidance through AGENTS.md and .agents/skills. Reopen only for a real codex
# consumer whose need survives losing the tool allowlist.
#
# Either way this costs no always-on budget: subagents are on-demand files.
CLAUDE_AGENTS_ROOT = AgentOutputRoot(
    family="claude", path=Path(".claude/agents"), suffix=".md", claude_passthrough=True
)
COPILOT_AGENTS_ROOT = AgentOutputRoot(
    family="copilot", path=Path(".github/agents"), suffix=".agent.md", claude_passthrough=False
)
AGENTS_OUTPUT_ROOTS = (CLAUDE_AGENTS_ROOT, COPILOT_AGENTS_ROOT)


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
    tier: str
    # The deprecated `model` key as authored, empty when absent. Carried only so
    # lint_agent_sources can name it; nothing renders or resolves it.
    deprecated_model: str
    claude: tuple[tuple[str, object], ...]
    slots: tuple[tuple[str, tuple[SlotItem, ...]], ...]
    source: str
    override: bool
    source_path: Path
    technologies: tuple[str, ...] = ()

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
    shadowed = sorted(set(claude) & RESERVED_FRONTMATTER_KEYS)
    if shadowed:
        raise ValidationError(
            f"claude passthrough may not shadow the rendered key(s) {', '.join(shadowed)}",
            path,
        )

    technologies = validate_technologies(data.get("technologies") or [], path)

    return AgentDefinition(
        slug=slug,
        purpose=_require_str(data.get("purpose"), "purpose", path).strip(),
        triggers=_require_str(data.get("triggers"), "triggers", path).strip(),
        returns=_require_str(data.get("returns"), "returns", path).strip(),
        posture=_require_str(data.get("posture"), "posture", path).strip(),
        tools=tuple(tool.strip() for tool in tools),
        tier=str(data.get("tier") or "").strip(),
        deprecated_model=str(data.get(DEPRECATED_MODEL_KEY) or "").strip(),
        claude=tuple(sorted(claude.items())),
        slots=_parse_slots(data.get("slots"), path),
        source=data.get("source", source),
        override=bool(data.get("override", False)),
        source_path=path,
        technologies=tuple(technologies),
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


def resolve_copilot_tool(tool: str) -> str | None:
    """The copilot primary a declared tool name resolves to, or None if nothing.

    Resolution is case-insensitive, per GitHub's published alias table. `None`
    means copilot would drop the entry with no error, so callers should treat it
    as an authoring defect rather than a working grant.
    """
    return _COPILOT_TOOL_BY_ALIAS.get(tool.casefold())


def render_agent_md(
    agent: AgentDefinition, blocks: dict[str, BlockDefinition], root: AgentOutputRoot
) -> str:
    """Render the projected agent file (frontmatter + generated marker + body).

    Frontmatter carries the portable core every reader honors (name,
    description, tools); only a root that declares ``claude_passthrough`` also
    receives the source's Claude-only extras, so a Claude-specific key never
    leaks into a copilot agent file. *root* is required rather than defaulting:
    a caller that forgets it would quietly project only one family, which is the
    drift this module now gates against.

    No family gets a `model` line: a provider model id is not portable across
    agent families, so the source's `tier` is catalog metadata that resolves to a
    concrete model elsewhere, never a frontmatter key (basicly-kjc5.58).
    """
    front: dict[str, object] = {
        "name": agent.slug,
        "description": compose_description(agent),
        "tools": ", ".join(agent.tools),
    }
    if root.claude_passthrough:
        front.update(dict(agent.claude))
    frontmatter = yaml.safe_dump(front, sort_keys=False, width=100_000, allow_unicode=True)
    body = compose_body(agent, blocks)
    return f"---\n{frontmatter}---\n\n{GENERATED_MARKER}\n\n{body}\n"


def _is_generated_agent(path: Path) -> bool:
    return path.is_file() and GENERATED_MARKER in path.read_text(encoding="utf-8", errors="ignore")


def sync_agents(
    repo_root: Path, selection: frozenset[str] | None = None
) -> tuple[SyncResult, list[Path]]:
    """Render selected source agents into every output root.

    Hand-authored files are never touched, but a previously projected agent the
    technology *selection* now excludes is pruned (generated-marker files only).
    Returns the sync result plus the pruned paths.
    """
    roots = default_agent_roots(repo_root)
    blocks = discover_blocks(roots)
    result = SyncResult()
    pruned: list[Path] = []
    for agent in discover_agents(roots):
        selected = technology_selected(agent.technologies, selection)
        for out_root in AGENTS_OUTPUT_ROOTS:
            target_path = out_root.target(repo_root, agent.slug)
            if selected:
                sync_file(
                    target_path, render_agent_md(agent, blocks, out_root).encode("utf-8"), result
                )
            elif _is_generated_agent(target_path):
                target_path.unlink()
                pruned.append(target_path)
    return result, pruned


def check_synced_agents(
    repo_root: Path, selection: frozenset[str] | None = None
) -> list[tuple[Path, str]]:
    """Return missing or stale projected agent files, across every output root."""
    roots = default_agent_roots(repo_root)
    blocks = discover_blocks(roots)
    mismatches: list[tuple[Path, str]] = []
    for agent in discover_agents(roots):
        selected = technology_selected(agent.technologies, selection)
        for out_root in AGENTS_OUTPUT_ROOTS:
            target_path = out_root.target(repo_root, agent.slug)
            if not selected:
                if _is_generated_agent(target_path):
                    mismatches.append((target_path, "excluded by technology selection"))
                continue
            if not target_path.exists():
                mismatches.append((target_path, "missing"))
                continue
            if target_path.read_bytes() != render_agent_md(agent, blocks, out_root).encode("utf-8"):
                mismatches.append((target_path, "content mismatch"))
    return mismatches


def _rel(path: Path, repo_root: Path) -> str:
    try:
        return path.relative_to(repo_root).as_posix()
    except ValueError:
        return str(path)


def lint_agent_sources(repo_root: Path) -> list[str]:
    """Semantic agent lint over the merged core+overlay set (for catalog lint)."""
    try:
        roots = default_agent_roots(repo_root)
        blocks = discover_blocks(roots)
        agents = discover_agents(roots)
    except ValidationError as exc:
        return [str(exc)]

    violations: list[str] = []
    for agent in agents:
        rel = _rel(agent.source_path, repo_root)
        if agent.deprecated_model:
            violations.append(
                f"{rel}: '{DEPRECATED_MODEL_KEY}: {agent.deprecated_model}' pins a provider "
                "model id or alias, which is not portable across agent families; declare the "
                f"portable model tier instead — replace it with `tier: {' | '.join(MODEL_TIERS)}`"
            )
        # Every declared name has to resolve through the pinned alias table, or
        # the agent is weaker on the copilot root than it reads: copilot drops an
        # unrecognised entry with no error and no warning, so nothing downstream
        # would ever report it (basicly-8sxf).
        unresolved = [tool for tool in agent.tools if resolve_copilot_tool(tool) is None]
        if unresolved:
            violations.append(
                f"{rel}: tool(s) {', '.join(unresolved)} resolve to nothing in GitHub's "
                f"published tool aliases, so the {COPILOT_AGENTS_ROOT.path.as_posix()} "
                "projection would drop them with no error; declare one of "
                f"{', '.join(_COPILOT_TOOL_NAMES)}"
            )

        missing = unknown_block_refs(agent, blocks)
        violations.extend(f"{rel}: references unknown block '{ref}'" for ref in missing)
        if missing:
            continue

        body = compose_body(agent, blocks)
        if len(body) > MAX_BODY_CHARS:
            violations.append(
                f"{rel}: composed body is {len(body)} chars; the portable cap is "
                f"{MAX_BODY_CHARS} (GitHub cloud-agent prompt ceiling)"
            )

        if READ_ONLY_MARKER in agent.posture.lower():
            # Matched folded so no casing slips past the check, but reported as
            # authored so the author can find the line (basicly-e9jc).
            granted = sorted({
                tool for tool in agent.tools if tool.casefold() in _WRITE_TOOLS_FOLDED
            })
            if granted:
                violations.append(
                    f"{rel}: posture declares read-only but tools grant {', '.join(granted)}"
                )

    return violations
