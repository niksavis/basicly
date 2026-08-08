"""Tests for the portable tier resolver kit (basicly-wbsz.1).

The kit's whole contract is that it works with **no basicly**, and this suite is
inside basicly — where `basicly` is importable and on PATH — so a test that merely
imports the module proves nothing about the constraint. Three kinds of test carry
it instead:

- an AST gate that the module imports nothing outside the standard library;
- subprocess tests that copy the two kit files into a consumer project and run
  them under an interpreter started with ``-S -I`` and an environment built from
  empty, where the subprocess itself asserts ``basicly`` is neither importable nor
  on PATH before it resolves anything;
- a drift gate that the kit and ``basicly.models`` agree on every cell, because
  the host/surface rule is a deliberate copy and a copy needs a check, not a
  convention (the pattern ``tests/test_tracker_path_scan.py`` already uses).

Every expected model id is taken from ``basicly.models`` or from the committed map
indexed directly, never retyped: the map is regenerated data, so a literal here
would turn a legitimate upstream change into a puzzle in this file.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from basicly import models
from basicly.schema import MODEL_TIERS

REPO_ROOT = Path(__file__).parent.parent
KIT = REPO_ROOT / ".basicly/core/kit/tier/tier_resolver.py"
MAP = REPO_ROOT / ".basicly" / "core" / "models" / "model-map.json"
REFERENCE_MAP: dict = json.loads(MAP.read_text(encoding="utf-8"))

# A genuine unavailable cell in the shipped map, and the tier next door that does
# have a model there — the pair that makes "nothing" and "some other tier's
# model" distinguishable outcomes rather than a claim.
UNAVAILABLE_VENDOR = "moonshotai"
UNAVAILABLE_TIER = "low"
NEIGHBOUR_TIER = "medium"
COPILOT_SURFACE = "github-copilot"


def _load_kit(path: Path = KIT) -> ModuleType:
    """Load the kit the way a hook does: by file path, as a standalone module.

    Registering the module in ``sys.modules`` before executing it is the recipe
    the importlib docs give and is not optional here — ``dataclasses`` resolves a
    string annotation through ``sys.modules[cls.__module__]``, so a module absent
    from it fails at class-definition time. The kit's docstring says so, and this
    loader is the pinned copy of that instruction.
    """
    name = f"tier_resolver_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kit = _load_kit()


def _resolver(default_tier: str | None = None):
    """A resolver over the committed map."""
    resolver = kit.TierResolver.from_map_path(MAP, default_tier=default_tier)
    assert resolver is not None
    return resolver


def _definition(path: Path, tier: str | None = None) -> Path:
    """An agent definition written by a consumer, with or without a tier."""
    lines = ["---", "name: my-own-agent", "description: An agent basicly never shipped."]
    if tier is not None:
        lines.append(f"tier: {tier}")
    lines += ["---", "", "Do the thing.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _expected(tier: str, vendor: str, surface: str) -> str:
    """The in-harness resolver's answer for one cell, as the pinned expectation."""
    return models.model_for(tier, vendor, surface, mapping=REFERENCE_MAP)


# --- the constraint: no basicly, standard library only -------------------------


def test_the_kit_imports_nothing_but_the_standard_library() -> None:
    """A third-party or basicly import would break the kit in a consumer repo."""
    tree = ast.parse(KIT.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import would make the kit need a package"
            if node.module:
                imported.add(node.module.split(".")[0])
    assert imported, "the AST walk found no imports at all, so it proves nothing"
    assert "basicly" not in imported
    assert imported <= set(sys.stdlib_module_names), sorted(imported - set(sys.stdlib_module_names))


# --- the mirror of the surface/vendor rule ------------------------------------


def test_the_host_surface_map_mirrors_the_package_exactly() -> None:
    """The kit cannot import the rule, so the copy is checked rather than trusted."""
    assert dict(models.FAMILY_MODEL_SURFACES) == kit.HOST_SURFACES


def test_every_cell_resolves_the_same_as_the_in_harness_resolver() -> None:
    """Both halves answer identically, so the copy cannot become a second rule."""
    resolver = _resolver()
    resolved = 0
    empty = 0
    for tier in MODEL_TIERS:
        for vendor in REFERENCE_MAP["vendors"]:
            for surface, _default_vendor in kit.HOST_SURFACES.values():
                try:
                    expected: str | None = _expected(tier, vendor, surface)
                except models.ModelUnavailableError:
                    expected = None
                assert resolver.model_for(tier, vendor, surface) == expected, (
                    f"{vendor} {tier} on {surface}"
                )
                if expected is None:
                    empty += 1
                else:
                    resolved += 1
    # Positive control: a comparison that only ever saw one branch would pass
    # while proving nothing about the other.
    assert resolved > 0 and empty > 0, f"resolved={resolved} empty={empty}"


def test_the_tier_vocabulary_comes_from_the_map_not_a_second_copy() -> None:
    """`tier_order` is read from the map, so no constant here can drift from it."""
    assert _resolver().tier_order == tuple(MODEL_TIERS)


# --- the AC: a declared tier resolves for the requested host surface ----------


def test_a_consumer_authored_definition_resolves_its_declared_tier(tmp_path: Path) -> None:
    """A definition basicly never shipped, outside any catalog, resolves normally."""
    definition = _definition(tmp_path / "elsewhere" / "my-own-agent.md", tier="high")
    result = _resolver().resolve("claude", definition=definition)
    assert result.model == _expected("high", "anthropic", "anthropic")
    assert (result.tier, result.source, result.surface, result.vendor) == (
        "high",
        "definition",
        "anthropic",
        "anthropic",
    )
    assert result.reason is None


def test_a_catalog_definition_and_a_consumer_definition_resolve_identically(
    tmp_path: Path,
) -> None:
    """Resolution keys off the file, so where the file came from cannot change it."""
    catalog_style = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="medium")
    consumer_style = _definition(tmp_path / "somewhere" / "else.md", tier="medium")
    resolver = _resolver()
    assert (
        resolver.resolve("claude", definition=catalog_style).model
        == resolver.resolve("claude", definition=consumer_style).model
        == _expected("medium", "anthropic", "anthropic")
    )


def test_one_declared_tier_resolves_to_each_hosts_own_spelling(tmp_path: Path) -> None:
    """Surface decides the spelling; the low anthropic tier is the pinned example.

    ``claude-haiku-4-5`` on the anthropic surface versus ``claude-haiku-4.5`` on
    github-copilot is the map README's canonical case, and copilot rejects the
    hyphenated form outright — so the two answers differing is the behaviour, not
    an incidental fact.
    """
    definition = _definition(tmp_path / "my-own-agent.md", tier="low")
    resolver = _resolver()
    claude = resolver.resolve("claude", definition=definition)
    copilot = resolver.resolve("copilot", definition=definition)
    assert claude.model == _expected("low", "anthropic", "anthropic")
    assert copilot.model == _expected("low", "anthropic", COPILOT_SURFACE)
    assert (claude.surface, copilot.surface) == ("anthropic", COPILOT_SURFACE)
    assert claude.model != copilot.model


def test_a_vendor_override_resolves_the_copilot_surface_for_that_vendor(
    tmp_path: Path,
) -> None:
    """Copilot is multi-vendor, so the default vendor is overridable per call."""
    definition = _definition(tmp_path / "my-own-agent.md", tier="medium")
    result = _resolver().resolve("copilot", definition=definition, vendor=UNAVAILABLE_VENDOR)
    assert result.model == _expected("medium", UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert result.vendor == UNAVAILABLE_VENDOR


def test_an_explicit_tier_outranks_the_definition(tmp_path: Path) -> None:
    """A caller that already knows the tier does not need the file consulted."""
    definition = _definition(tmp_path / "my-own-agent.md", tier="low")
    result = _resolver().resolve("claude", definition=definition, tier="high")
    assert (result.tier, result.source) == ("high", "argument")
    assert result.model == _expected("high", "anthropic", "anthropic")


# --- the AC: fail closed, never a substitution --------------------------------


def test_an_unavailable_cell_resolves_to_nothing_not_a_neighbouring_tier(
    tmp_path: Path,
) -> None:
    """The silent demotion basicly-izda exists to prevent, pinned as a test."""
    definition = _definition(tmp_path / "my-own-agent.md", tier=UNAVAILABLE_TIER)
    resolver = _resolver()
    result = resolver.resolve("copilot", definition=definition, vendor=UNAVAILABLE_VENDOR)
    assert result.model is None
    assert result.reason is not None
    # The map's own reason, carried through verbatim rather than flattened to
    # "not found", so the refusal names the real cause.
    cell = REFERENCE_MAP["tiers"][UNAVAILABLE_TIER]["vendors"][UNAVAILABLE_VENDOR]["surfaces"][
        COPILOT_SURFACE
    ]
    assert cell["reason"] in result.reason
    # Positive control: the neighbouring tier really does have a model on this
    # surface, so "nothing" and "the wrong model" are distinguishable outcomes.
    neighbour = resolver.model_for(NEIGHBOUR_TIER, UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert neighbour == _expected(NEIGHBOUR_TIER, UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert result.model != neighbour


def test_an_undeclared_tier_with_no_default_resolves_to_nothing(tmp_path: Path) -> None:
    """No tier and no configured default is a refusal, not the cheapest tier."""
    definition = _definition(tmp_path / "my-own-agent.md")
    result = _resolver().resolve("claude", definition=definition)
    assert (result.model, result.tier, result.source) == (None, None, None)
    assert result.reason is not None and "no default tier is configured" in result.reason


def test_an_undeclared_tier_uses_the_configured_default_when_there_is_one(
    tmp_path: Path,
) -> None:
    """A default is honoured, and recorded as the input that decided the tier."""
    definition = _definition(tmp_path / "my-own-agent.md")
    result = _resolver(default_tier="low").resolve("claude", definition=definition)
    assert (result.tier, result.source) == ("low", "default")
    assert result.model == _expected("low", "anthropic", "anthropic")


def test_a_missing_definition_file_resolves_to_nothing(tmp_path: Path) -> None:
    """An absent definition declares no tier; it does not raise in the spawn path."""
    result = _resolver().resolve("claude", definition=tmp_path / "absent.md")
    assert result.model is None
    assert kit.declared_tier(tmp_path / "absent.md") is None


def test_a_tier_the_map_does_not_carry_resolves_to_nothing() -> None:
    """An unknown tier names the map's vocabulary instead of guessing at one."""
    result = _resolver().resolve("claude", tier="enormous")
    assert result.model is None
    assert result.reason is not None and "enormous" in result.reason
    for tier in MODEL_TIERS:
        assert tier in result.reason


def test_an_unknown_host_resolves_to_nothing() -> None:
    """A host with no known surface cannot be given a spelling, so it gets none."""
    result = _resolver().resolve("some-future-host", tier="high")
    assert (result.model, result.surface, result.vendor) == (None, None, None)
    assert result.reason is not None and "unknown host" in result.reason


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{ not json", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"tier_order": ["low"]}', id="no-tiers-section"),
    ],
)
def test_a_map_that_cannot_be_used_yields_no_resolver(tmp_path: Path, payload: str) -> None:
    """A broken map means "leave the spawn alone", never "every tier is unavailable"."""
    broken = tmp_path / "model-map.json"
    broken.write_text(payload, encoding="utf-8")
    assert kit.load_map(broken) is None
    assert kit.TierResolver.from_map_path(broken) is None


def test_an_absent_map_yields_no_resolver(tmp_path: Path) -> None:
    """The case a machine-wide hook hits in every repo that has no map."""
    assert kit.load_map(tmp_path / "model-map.json") is None
    assert kit.TierResolver.from_map_path(tmp_path / "model-map.json") is None


# --- reading the declared tier off the definition -----------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        pytest.param("---\ntier: high\n---\n\nBody.\n", "high", id="plain"),
        pytest.param('---\ntier: "high"\n---\n', "high", id="double-quoted"),
        pytest.param("---\ntier: 'high'\n---\n", "high", id="single-quoted"),
        pytest.param("---\ntier:  HIGH \n---\n", "high", id="padded-and-uppercase"),
        pytest.param("---\ntier: low # cheapest\n---\n", "low", id="trailing-comment"),
        pytest.param("\ufeff---\ntier: high\n---\n", "high", id="byte-order-mark"),
        pytest.param("---\nname: a\n---\n\ntier: high\n", None, id="in-the-body-not-frontmatter"),
        pytest.param("---\nmetadata:\n  tier: high\n---\n", None, id="nested-under-another-key"),
        pytest.param("---\nname: a\n---\n", None, id="frontmatter-without-a-tier"),
        pytest.param("tier: high\n", None, id="no-frontmatter-at-all"),
        pytest.param("---\ntier:\n---\n", None, id="empty-value"),
        pytest.param("", None, id="empty-file"),
    ],
)
def test_declared_tier_reads_only_a_top_level_frontmatter_scalar(
    tmp_path: Path, body: str, expected: str | None
) -> None:
    """What counts as a declared tier, and what deliberately does not."""
    path = tmp_path / "definition.md"
    path.write_text(body, encoding="utf-8")
    assert kit.declared_tier(path) == expected


def test_declared_tier_stops_before_reading_an_unbounded_body(tmp_path: Path) -> None:
    """An unclosed fence must not make the scanner read a whole long prompt.

    A thousand lines is past any frontmatter cap the module could reasonably
    use, so this pins the bound existing rather than its exact value.
    """
    path = tmp_path / "definition.md"
    filler = "\n".join(f"line {index}" for index in range(1000))
    path.write_text(f"---\nname: a\n{filler}\ntier: high\n", encoding="utf-8")
    assert kit.declared_tier(path) is None


def test_declared_tier_survives_bytes_that_are_not_utf8(tmp_path: Path) -> None:
    """A definition with an undecodable byte still yields its tier, not a crash."""
    path = tmp_path / "definition.md"
    path.write_bytes(b"---\ndescription: caf\xe9\ntier: high\n---\n")
    assert kit.declared_tier(path) == "high"


# --- finding a definition by subagent name ------------------------------------


def test_find_definition_locates_a_project_level_claude_agent(tmp_path: Path) -> None:
    """The path claude reads a project agent from."""
    expected = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude", roots=[tmp_path]) == expected


def test_find_definition_locates_a_copilot_agent_file(tmp_path: Path) -> None:
    """Copilot's own suffix, plus the claude directory VS Code also reads."""
    expected = _definition(tmp_path / ".github" / "agents" / "my-own-agent.agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "copilot", roots=[tmp_path]) == expected
    shared = _definition(tmp_path / ".claude" / "agents" / "shared.md", tier="high")
    assert kit.find_definition("shared", "copilot", roots=[tmp_path]) == shared


def test_find_definition_finds_nothing_for_a_host_with_no_definition_files() -> None:
    """Codex has no per-agent file, so a tier for it can only be argued or defaulted."""
    assert kit.find_definition("my-own-agent", "codex", roots=[REPO_ROOT]) is None


@pytest.mark.parametrize(
    ("name", "decoy"),
    [
        pytest.param("../../escaped", "escaped.md", id="parent-traversal"),
        pytest.param("nested/escaped", ".claude/agents/nested/escaped.md", id="posix-separator"),
        pytest.param(
            "nested\\escaped", ".claude/agents/nested\\escaped.md", id="windows-separator"
        ),
        pytest.param(".hidden", ".claude/agents/.hidden.md", id="leading-dot"),
        pytest.param("", ".claude/agents/.md", id="empty"),
        pytest.param("has space", ".claude/agents/has space.md", id="space"),
    ],
)
def test_find_definition_refuses_a_name_that_is_not_an_agent_slug(
    tmp_path: Path, name: str, decoy: str
) -> None:
    """The name comes from the host's tool input, so it is validated, not joined.

    Each rejected name gets a decoy file it *would* reach if the name were joined
    onto the path unchecked, and a legitimate agent is looked up first so the
    directories the traversal needs really exist. Without both, a refusal and a
    plain miss look identical and the test passes on an unvalidated resolver —
    which is exactly what a mutation run showed before the decoys were added.
    The separator cases carry their own platform difference as data: one path
    string names a nested file on Windows and a literal filename on POSIX, and
    both are created, so neither platform is the one that skips.
    """
    control = _definition(tmp_path / ".claude" / "agents" / "legit.md", tier="high")
    assert kit.find_definition("legit", "claude", roots=[tmp_path]) == control
    _definition(tmp_path / decoy, tier="high")
    assert kit.find_definition(name, "claude", roots=[tmp_path]) is None


def test_find_definition_falls_back_to_the_user_level_root(tmp_path: Path) -> None:
    """A user-level agent resolves when the project has none, project first."""
    user = tmp_path / "home"
    project = tmp_path / "project"
    user_agent = _definition(user / ".claude" / "agents" / "my-own-agent.md", tier="low")
    assert kit.find_definition("my-own-agent", "claude", roots=[project, user]) == user_agent
    project_agent = _definition(project / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude", roots=[project, user]) == project_agent


def test_find_definition_tolerates_an_undeterminable_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A hook in a context with no home must miss, not raise.

    ``Path.home()`` raises when neither the environment nor the password
    database can answer — a real state for a service or container invocation,
    and one no platform-specific skip can cover.
    """

    def no_home() -> Path:
        raise RuntimeError("could not determine home directory")

    monkeypatch.setattr(Path, "home", staticmethod(no_home))
    monkeypatch.chdir(tmp_path)
    expected = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="high")
    assert kit.find_definition("my-own-agent", "claude") == expected
    assert kit.find_definition("absent-agent", "claude") is None


# --- finding the map ----------------------------------------------------------


def test_find_map_walks_up_to_the_repository_being_worked_in(tmp_path: Path) -> None:
    """What lets one machine-wide hook answer for whichever repo it runs in."""
    installed = tmp_path / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert kit.find_map(nested) == installed


def test_find_map_falls_back_to_the_map_beside_the_kit(tmp_path: Path) -> None:
    """A kit installed outside a repo still has its own committed neighbour."""
    assert kit.find_map(tmp_path) == MAP


def test_the_two_files_resolve_from_a_flat_copy_anywhere(tmp_path: Path) -> None:
    """The plug-and-play claim: copy the module and the map into one directory."""
    flat = tmp_path / "flat"
    flat.mkdir()
    shutil.copy2(KIT, flat / KIT.name)
    shutil.copy2(MAP, flat / MAP.name)
    elsewhere = tmp_path / "unrelated"
    elsewhere.mkdir()
    copied = _load_kit(flat / KIT.name)
    assert copied.find_map(elsewhere) == flat / MAP.name
    resolver = copied.TierResolver.discover(elsewhere)
    assert resolver is not None
    assert resolver.resolve("claude", tier="high").model == _expected(
        "high", "anthropic", "anthropic"
    )


# --- the no-basicly proof, in a subprocess ------------------------------------

# The subprocess asserts the constraint itself, before resolving anything: an
# environment that quietly still had basicly in it would otherwise make this
# whole section vacuous.
_DRIVER = """
import importlib.util
import json
import shutil
import sys
from pathlib import Path

assert importlib.util.find_spec("basicly") is None, "basicly is importable"
assert shutil.which("basicly") is None, "basicly is on PATH"

spec = importlib.util.spec_from_file_location("tier_resolver", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["tier_resolver"] = module
spec.loader.exec_module(module)

resolver = module.TierResolver.discover()
assert resolver is not None, "no map was discovered from the consumer repo"
definition = module.find_definition(sys.argv[2], "claude", roots=[Path.cwd()])
assert definition is not None, "the consumer definition was not found"
print(json.dumps(resolver.resolve("claude", definition=definition).as_dict()))
"""


def _consumer_repo(tmp_path: Path) -> Path:
    """A project carrying the two kit files and one agent basicly never shipped."""
    consumer = tmp_path / "consumer"
    core = consumer / ".basicly" / "core"
    (core / "kit").mkdir(parents=True)
    shutil.copy2(KIT, core / "kit" / KIT.name)
    (core / "models").mkdir(parents=True)
    shutil.copy2(MAP, core / "models" / MAP.name)
    _definition(consumer / ".claude" / "agents" / "my-own-agent.md", tier="high")
    return consumer


def _pruned_env(tmp_path: Path) -> dict[str, str]:
    """An environment with no basicly on PATH and nothing pointing at this repo.

    Built from empty rather than filtered, so nothing inherited can smuggle the
    package back in — no ``PYTHONPATH``, no ``VIRTUAL_ENV``. The few names copied
    back are what an interpreter and ``Path.home()`` need on their own platform,
    which makes the platform difference test data rather than a skip: ``HOME``
    and ``USERPROFILE`` point at an empty scratch directory, so the user-level
    agent root is controlled instead of being the developer's real one.
    """
    empty = tmp_path / "empty-path-dir"
    empty.mkdir(exist_ok=True)
    home = tmp_path / "scratch-home"
    home.mkdir(exist_ok=True)
    env = {"PATH": str(empty), "HOME": str(home), "USERPROFILE": str(home)}
    for name in ("SystemRoot", "SYSTEMROOT", "COMSPEC", "TEMP", "TMP"):
        value = os.environ.get(name)
        if value is not None:
            env[name] = value
    return env


def _run_without_basicly(
    args: list[str], cwd: Path, tmp_path: Path
) -> subprocess.CompletedProcess[str]:
    """Run under an interpreter with no site-packages and no inherited environment.

    ``-S`` drops site-packages, which is where this repo's own ``basicly`` lives,
    and ``-I`` drops ``PYTHONPATH``, the user site directory and the script's own
    directory. Together they are the closest thing to a consumer's interpreter
    that can be started from inside this repo's virtualenv.
    """
    return subprocess.run(
        [sys.executable, "-S", "-I", *args],
        cwd=cwd,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_resolver_answers_with_no_basicly_importable_and_none_on_path(
    tmp_path: Path,
) -> None:
    """The bead's hard constraint, exercised the way a consumer would exercise it."""
    consumer = _consumer_repo(tmp_path)
    driver = tmp_path / "spawn.py"
    driver.write_text(_DRIVER, encoding="utf-8")
    copied_kit = consumer / ".basicly" / "core" / "kit" / KIT.name

    result = _run_without_basicly(
        [str(driver), str(copied_kit), "my-own-agent"], cwd=consumer, tmp_path=tmp_path
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["model"] == _expected("high", "anthropic", "anthropic")
    assert payload["tier"] == "high"
    assert payload["source"] == "definition"
    assert payload["reason"] is None


def test_the_kit_command_line_prints_the_resolved_model_without_basicly(
    tmp_path: Path,
) -> None:
    """The invocable surface a spawner in any language can drive."""
    consumer = _consumer_repo(tmp_path)
    copied_kit = consumer / ".basicly" / "core" / "kit" / KIT.name

    result = _run_without_basicly(
        [str(copied_kit), "--host", "claude", "--name", "my-own-agent"],
        cwd=consumer,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["model"] == _expected("high", "anthropic", "anthropic")
    assert payload["surface"] == "anthropic"


def test_the_kit_command_line_resolves_nothing_for_an_unavailable_cell(
    tmp_path: Path,
) -> None:
    """Fail-closed stays observable without basicly: exit status plus a reason."""
    consumer = _consumer_repo(tmp_path)
    copied_kit = consumer / ".basicly" / "core" / "kit" / KIT.name

    result = _run_without_basicly(
        [
            str(copied_kit),
            "--host",
            "copilot",
            "--vendor",
            UNAVAILABLE_VENDOR,
            "--name",
            "my-own-agent",
            "--tier",
            UNAVAILABLE_TIER,
        ],
        cwd=consumer,
        tmp_path=tmp_path,
    )

    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["model"] is None
    assert payload["reason"] is not None and "unavailable" in payload["reason"]
    neighbour = _expected(NEIGHBOUR_TIER, UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert neighbour not in result.stdout


def test_the_kit_command_line_leaves_a_repository_with_no_map_alone(tmp_path: Path) -> None:
    """The machine-wide-hook case: no map means no answer, not an error trace."""
    bare = tmp_path / "unrelated-project"
    bare.mkdir()
    copied_kit = tmp_path / KIT.name
    shutil.copy2(KIT, copied_kit)

    result = _run_without_basicly(
        [str(copied_kit), "--host", "claude", "--tier", "high"], cwd=bare, tmp_path=tmp_path
    )

    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["model"] is None
    assert payload["reason"] is not None and MAP.name in payload["reason"]
    assert result.stderr == ""
