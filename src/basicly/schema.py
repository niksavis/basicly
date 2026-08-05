"""Data classes and controlled vocabularies for fragments and targets."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PRIORITY_MAP = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

# Source-format version shared by every catalog source kind (fragment, skill,
# agent, block). Bump only when the loader contract changes; loaders reject a
# source authored for a NEWER version with an "upgrade basicly" error instead
# of misreading it.
SOURCE_SCHEMA_VERSION = 1

CATEGORIES = {
    "boundaries",
    "code-style",
    "commands",
    "decisions",
    "design",
    "hooks",
    "project",
    "security",
    "skills",
    "testing",
    "tools",
    "ci-cd",
    # One real incident, one bullet: env traps, timing, platform gotchas. The
    # landing zone for self-improvement-retro proposals.
    "quirks",
}

STATUSES = {"active", "draft", "deprecated"}

# Controlled vocabulary for technology scoping (§9): stack tags plus the
# environment/platform tools the catalog ships skills for. A source without a
# `technologies:` list is universal and always ships; catalog lint rejects
# values outside this list.
TECHNOLOGIES = {
    "dotnet",
    "go",
    "java",
    "node",
    "python",
    "rust",
    "starship",
    "tmux",
    "wezterm",
    "wsl",
    "zsh",
}

# Ordered model-capability vocabulary for agent sources (roster design R5),
# cheapest first. A provider model id is never portable across agent families —
# models.dev spells one model `claude-haiku-4.5` for Copilot and
# `claude-haiku-4-5` for Anthropic — so a source declares a *model tier* and the
# concrete model behind each tier stays configuration. Distinct from the
# instruction/skill/plugin "capability tier" `basicly install` delivers: the name
# collides, the mechanism does not, so prefer "model tier" in prose.
# `.basicly/core/schemas/agent.schema.json` necessarily restates these values as
# an `enum`; a tripwire test keeps the two in step.
MODEL_TIERS = ("low", "medium", "high", "maximum")

DEFAULT_SCOPE = ["**"]


def technology_selected(
    technologies: list[str] | tuple[str, ...], selection: frozenset[str] | None
) -> bool:
    """True when a source ships under the repo's technology selection.

    An untagged source is universal; ``selection`` is a set of selected tags or
    ``None`` meaning no selection was recorded (everything ships).
    """
    return selection is None or not technologies or bool(set(technologies) & selection)


def validate_technologies(technologies: object, path: Path) -> list[str]:
    """Validate a source's ``technologies`` value against the controlled vocabulary.

    Runs at load time for every source type (overlay sources never pass through
    catalog lint), so a typo'd tag fails loudly instead of silently dropping the
    source from every selection.
    """
    if not isinstance(technologies, list) or not all(
        isinstance(item, str) for item in technologies
    ):
        raise ValidationError("technologies must be a list of strings", path)
    unknown = sorted(set(technologies) - TECHNOLOGIES)
    if unknown:
        raise ValidationError(
            f"unknown technologies: {', '.join(unknown)} "
            f"(allowed: {', '.join(sorted(TECHNOLOGIES))})",
            path,
        )
    return technologies


@dataclass(frozen=True)
class Fragment:
    """A single tool-agnostic policy/practice/decision."""

    id: str
    description: str
    category: str
    applies_to: list[str]
    priority: str = "medium"
    scope_paths: list[str] = field(default_factory=lambda: list(DEFAULT_SCOPE))
    tags: list[str] = field(default_factory=list)
    technologies: list[str] = field(default_factory=list)
    status: str = "active"
    title: str | None = None
    body: str = ""
    source_path: Path | None = None
    # Phase 2 extension-mechanism fields (phase-1-safe defaults)
    source: str = "core"
    override: bool = False
    replaces: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    enforced_by: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Derive the title from the id if no title was provided."""
        object.__setattr__(self, "title", self.title or self._id_to_title(self.id))

    @staticmethod
    def _id_to_title(fragment_id: str) -> str:
        """Convert a kebab-case id to title case."""
        return " ".join(word.capitalize() for word in fragment_id.split("-"))

    @property
    def priority_value(self) -> int:
        """Return the numeric priority value for sorting."""
        return PRIORITY_MAP.get(self.priority, 2)

    @property
    def is_scoped(self) -> bool:
        """Return True if the fragment has a non-default scope."""
        return self.scope_paths != list(DEFAULT_SCOPE)

    @property
    def scope_summary(self) -> str:
        """Return a short scope representation for display."""
        if self.is_scoped:
            return self.scope_paths[0]
        return "**"


@dataclass(frozen=True)
class OutputDef:
    """Definition of a single generated output for a target."""

    name: str
    template: str
    path: str | None = None
    path_template: str | None = None
    applies_to_filter: list[str] = field(default_factory=list)
    has_scope: bool = False
    exclude_scoped: bool = False


@dataclass(frozen=True)
class Target:
    """A coding agent ecosystem with its own config format."""

    name: str
    enabled: bool
    tone: str
    max_size_warning: int
    outputs: list[OutputDef]


@dataclass(frozen=True)
class PlannedOutput:
    """A concrete output file planned for rendering."""

    target_name: str
    output_name: str
    output_path: Path
    template: str
    fragments: list[Fragment]


def display_path(path: Path, repo_root: Path | None = None) -> str:
    """Render ``path`` for a user-facing message: repo-relative, absolute when outside.

    Every call site raises with an absolute source path, so a load-time failure used to
    print a machine-specific path — leaking a home directory or a username into anything
    pasted into an issue or a CI log — while ``catalog_lint`` reported its own violations
    repo-relative. One lint run therefore showed the same finding in two path styles
    (basicly-ky5z). ``repo_root`` defaults to the process working directory, which is what
    ``cli._repo_root`` means by the repo root. A path outside the root keeps its absolute
    form: a relative path spelled with ``..`` would be more misleading, not less.
    """
    root = repo_root
    if root is None:
        try:
            root = Path.cwd()
        except OSError:
            return str(path)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


class ValidationError(Exception):
    """Raised when a fragment or target registry is invalid."""

    def __init__(
        self, message: str, path: Path | None = None, *, repo_root: Path | None = None
    ) -> None:
        """Initialize with a message, optional source path, and the root to show it against."""
        super().__init__(message)
        self.message = message
        self.path = path
        self.repo_root = repo_root

    def __str__(self) -> str:
        """Include the source path in the string when available, relative to the repo root."""
        if self.path:
            return f"{display_path(self.path, self.repo_root)}: {self.message}"
        return self.message
