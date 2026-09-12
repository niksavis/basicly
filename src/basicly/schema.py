from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

PRIORITY_MAP = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
}

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
    "quirks",
}

STATUSES = {"active", "draft", "deprecated"}

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

MODEL_TIERS = ("low", "medium", "high", "maximum")

DEFAULT_SCOPE = ["**"]


def technology_selected(
    technologies: list[str] | tuple[str, ...], selection: frozenset[str] | None
) -> bool:

    return selection is None or not technologies or bool(set(technologies) & selection)


def validate_technologies(technologies: object, path: Path) -> list[str]:

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
    source: str = "core"
    override: bool = False
    replaces: list[str] = field(default_factory=list)
    extends: list[str] = field(default_factory=list)
    enforced_by: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", self.title or self._id_to_title(self.id))

    @staticmethod
    def _id_to_title(fragment_id: str) -> str:
        return " ".join(word.capitalize() for word in fragment_id.split("-"))

    @property
    def priority_value(self) -> int:
        return PRIORITY_MAP.get(self.priority, 2)

    @property
    def is_scoped(self) -> bool:
        return self.scope_paths != list(DEFAULT_SCOPE)

    @property
    def scope_summary(self) -> str:
        if self.is_scoped:
            return self.scope_paths[0]
        return "**"


@dataclass(frozen=True)
class OutputDef:
    name: str
    template: str
    path: str | None = None
    path_template: str | None = None
    applies_to_filter: list[str] = field(default_factory=list)
    has_scope: bool = False
    exclude_scoped: bool = False


@dataclass(frozen=True)
class Target:
    name: str
    enabled: bool
    tone: str
    max_size_warning: int
    max_lines_warning: int
    outputs: list[OutputDef]


@dataclass(frozen=True)
class PlannedOutput:
    target_name: str
    output_name: str
    output_path: Path
    template: str
    fragments: list[Fragment]


def display_path(path: Path, repo_root: Path | None = None) -> str:

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
    def __init__(
        self, message: str, path: Path | None = None, *, repo_root: Path | None = None
    ) -> None:
        super().__init__(message)
        self.message = message
        self.path = path
        self.repo_root = repo_root

    def __str__(self) -> str:
        if self.path:
            return f"{display_path(self.path, self.repo_root)}: {self.message}"
        return self.message
