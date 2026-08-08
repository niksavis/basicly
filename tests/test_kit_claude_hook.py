"""Tests for the claude tier injection hook and the alias table (basicly-wbsz.2).

Two things are under test and they fail in different ways, so they are checked
differently:

- **The alias table.** ``tier_resolver.HOST_MODEL_ALIASES`` is committed data
  that must keep naming the same model the map resolves that tier to. It is held
  to the map through ``models.same_model`` — the repo's own rule for whether a
  bare alias names an id — rather than by retyping model ids here, which would
  turn a legitimate upstream map change into a puzzle in this file. The one
  literal that *is* right here is the four-value enum: that is a contract with
  the host binary, not data this repo generates.
- **The hook.** Its whole contract is observable from outside — stdin JSON in,
  stdout JSON or nothing out — so it is driven as a subprocess rather than
  imported, which is also how it runs in production. Every run uses ``-S -I``
  and an environment built from empty, so the no-basicly constraint the kit
  exists for is carried by the same harness that checks the behaviour.

Each "leaves the spawn alone" test needs a positive control, because *no output*
is what a broken hook produces too. Where the decline is the point, the same
payload is run twice — once in the state that should rewrite and once in the
state that should not.
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
KIT_DIR = REPO_ROOT / ".basicly/core/kit/tier"
HOOK = KIT_DIR / "claude_tier_hook.py"
RESOLVER = KIT_DIR / "tier_resolver.py"
MAP = REPO_ROOT / ".basicly" / "core" / "models" / "model-map.json"
REFERENCE_MAP: dict = json.loads(MAP.read_text(encoding="utf-8"))

AGENT_NAME = "my-own-agent"
DECLARED_TIER = "high"

# The Agent tool's `model` parameter, extracted verbatim from the claude 2.1.220
# binary on basicly-wbsz.2: `v.enum(["sonnet","opus","haiku","fable"])`. A
# literal is correct here and nowhere else in this file — it pins what the host
# accepts, which is not something this repo generates and not something a map
# regeneration may quietly widen.
AGENT_TOOL_MODEL_ENUM = {"sonnet", "opus", "haiku", "fable"}


def _load_kit() -> ModuleType:
    """The resolver, loaded by file path the way the hook loads it."""
    spec = importlib.util.spec_from_file_location("tier_resolver_for_hook_tests", RESOLVER)
    assert spec and spec.loader
    module: ModuleType = importlib.util.module_from_spec(spec)
    # Required, not decoration: `dataclasses` resolves a string annotation
    # through `sys.modules[cls.__module__]`. The kit's docstring says so.
    sys.modules["tier_resolver_for_hook_tests"] = module
    spec.loader.exec_module(module)
    return module


kit = _load_kit()


def _resolver(map_path: Path = MAP):
    resolver = kit.TierResolver.from_map_path(map_path)
    assert resolver is not None
    return resolver


def _definition(path: Path, tier: str | None = None, model: str | None = None) -> Path:
    """An agent definition a consumer wrote, with or without a tier or a model."""
    lines = ["---", f"name: {path.stem}", "description: An agent basicly never shipped."]
    if tier is not None:
        lines.append(f"tier: {tier}")
    if model is not None:
        lines.append(f"model: {model}")
    lines += ["---", "", "Do the thing.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --- the alias table ----------------------------------------------------------


def test_the_alias_table_covers_exactly_the_maps_tier_vocabulary() -> None:
    """A tier with no alias would silently stop being injectable on claude."""
    assert set(kit.HOST_MODEL_ALIASES["claude"]) == set(MODEL_TIERS)


def test_every_alias_is_one_the_agent_tool_would_accept() -> None:
    """An alias outside the host's enum fails tool-input validation at spawn time."""
    assert set(kit.HOST_MODEL_ALIASES["claude"].values()) == AGENT_TOOL_MODEL_ENUM


def test_every_alias_names_the_model_its_own_tier_resolves_to() -> None:
    """The drift gate: the committed table cannot come to disagree with the map."""
    surface, vendor = kit.HOST_SURFACES["claude"]
    for tier, alias in kit.HOST_MODEL_ALIASES["claude"].items():
        model = models.model_for(tier, vendor, surface, mapping=REFERENCE_MAP)
        assert models.same_model(alias, model), f"{tier}: {alias} does not name {model}"


def test_the_alias_check_can_tell_two_tiers_apart() -> None:
    """Positive control: the assertion above would be vacuous if it never failed."""
    surface, vendor = kit.HOST_SURFACES["claude"]
    low = models.model_for("low", vendor, surface, mapping=REFERENCE_MAP)
    assert not models.same_model(kit.HOST_MODEL_ALIASES["claude"]["high"], low)


def test_a_resolved_model_carries_its_alias(tmp_path: Path) -> None:
    """The kit's own surface, before any hook is involved."""
    definition = _definition(tmp_path / f"{AGENT_NAME}.md", tier=DECLARED_TIER)
    result = _resolver().resolve("claude", definition=definition)
    assert result.model == models.model_for(
        DECLARED_TIER, "anthropic", "anthropic", mapping=REFERENCE_MAP
    )
    assert result.alias == kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]


def test_a_host_with_no_narrower_vocabulary_resolves_a_model_but_no_alias() -> None:
    """Copilot's model field takes the surface spelling, so there is no alias to add."""
    result = _resolver().resolve("copilot", tier=DECLARED_TIER)
    assert result.model is not None
    assert result.alias is None


def test_an_unavailable_cell_resolves_neither_a_model_nor_an_alias(tmp_path: Path) -> None:
    """An alias is the alias *of a resolved model*, so it cannot outlive one.

    Otherwise a tier the map marks unavailable would still be pinned by name —
    the silent substitution the whole kit exists to refuse.
    """
    doctored = json.loads(json.dumps(REFERENCE_MAP))
    cell = doctored["tiers"][DECLARED_TIER]["vendors"]["anthropic"]["surfaces"]["anthropic"]
    cell["status"] = "unavailable"
    cell.pop("model", None)
    map_path = tmp_path / MAP.name
    map_path.write_text(json.dumps(doctored), encoding="utf-8")

    result = _resolver(map_path).resolve("claude", tier=DECLARED_TIER)

    assert result.model is None
    assert result.alias is None
    assert result.reason is not None


# --- scoping the map lookup to the spawn's own tree ---------------------------


def test_find_map_without_the_kit_fallback_answers_only_for_its_own_tree(
    tmp_path: Path,
) -> None:
    """The exact shape that fooled the basicly-wbsz.2 probe.

    With the fallback on, a directory with no map anywhere in its tree still
    resolves, because the kit is by definition always beside itself — which for a
    machine-wide hook means every unrelated repository on the machine.
    """
    bare = tmp_path / "unrelated-project"
    bare.mkdir()
    assert kit.find_map(bare) == MAP
    assert kit.find_map(bare, beside_the_kit=False) is None


def test_find_map_without_the_kit_fallback_still_finds_the_repo_being_worked_in(
    tmp_path: Path,
) -> None:
    """Dropping the fallback must not drop the walk the hook actually relies on."""
    installed = tmp_path / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert kit.find_map(nested, beside_the_kit=False) == installed


# --- the hook, driven as the host drives it -----------------------------------


def _pruned_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:
    """An environment with no basicly on PATH and nothing pointing at this repo.

    Built from empty rather than filtered, so nothing inherited can smuggle the
    package back in. ``HOME``/``USERPROFILE`` point at an empty scratch directory
    so the user-level agent root is controlled rather than the developer's own —
    which makes the platform difference test data instead of a skip.
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
    if extra:
        env.update(extra)
    return env


def _payload(cwd: Path, **tool_input: object) -> dict:
    """A PreToolUse event for an Agent spawn, in the host's own shape."""
    spawn: dict[str, object] = {
        "description": "do a thing",
        "prompt": "Do the thing and report back.",
        "subagent_type": AGENT_NAME,
    }
    spawn.update(tool_input)
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": "Agent",
        "tool_input": spawn,
        "cwd": str(cwd),
    }


def _run_hook(
    payload: dict | str,
    cwd: Path,
    tmp_path: Path,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run the hook the way the host runs it, under a consumer-shaped interpreter.

    ``-S`` drops site-packages, where this repo's own ``basicly`` lives, and
    ``-I`` drops ``PYTHONPATH``, the user site directory and the script's own
    directory — so the hook's explicit ``sys.path`` insert is what finds the
    resolver beside it, exactly as it must in a consumer repo.
    """
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run(
        [sys.executable, "-S", "-I", str(HOOK)],
        input=text,
        cwd=cwd,
        env=_pruned_env(tmp_path, env_extra),
        capture_output=True,
        text=True,
        check=False,
    )


def _consumer_repo(tmp_path: Path, tier: str | None = DECLARED_TIER, **kwargs) -> Path:
    """A project with its own committed map and one agent basicly never shipped."""
    consumer = tmp_path / "consumer"
    installed = consumer / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    _definition(consumer / ".claude" / "agents" / f"{AGENT_NAME}.md", tier=tier, **kwargs)
    return consumer


def _updated_input(result: subprocess.CompletedProcess[str]) -> dict:
    """The rewrite the hook emitted, asserting the envelope shape as it goes."""
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    specific = payload["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    return specific["updatedInput"]


def test_the_hook_runs_where_basicly_is_neither_importable_nor_on_path(
    tmp_path: Path,
) -> None:
    """Validates the harness every other hook test runs under, not the hook."""
    probe = (
        "import importlib.util, shutil;"
        " assert importlib.util.find_spec('basicly') is None, 'importable';"
        " assert shutil.which('basicly') is None, 'on PATH'"
    )
    result = subprocess.run(
        [sys.executable, "-S", "-I", "-c", probe],
        cwd=tmp_path,
        env=_pruned_env(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_hook_imports_nothing_but_the_standard_library_and_the_resolver() -> None:
    """A third-party or basicly import would break the hook in a consumer repo."""
    tree = ast.parse(HOOK.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import would make the kit need a package"
            if node.module:
                imported.add(node.module.split(".")[0])
    assert "tier_resolver" in imported, "the AST walk found no kit import, so it proves nothing"
    assert "basicly" not in imported
    outside = imported - set(sys.stdlib_module_names) - {"tier_resolver"}
    assert not outside, sorted(outside)


def test_a_declared_tier_pins_the_spawn_to_its_alias(tmp_path: Path) -> None:
    """The acceptance criterion: a definition declaring a tier spawns on that tier."""
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    updated = _updated_input(result)
    assert updated["model"] == kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]
    assert updated["model"] in AGENT_TOOL_MODEL_ENUM


def test_the_rewrite_carries_the_whole_original_tool_input(tmp_path: Path) -> None:
    """`updatedInput` replaces the arguments, so a dropped key is a dropped argument."""
    consumer = _consumer_repo(tmp_path)
    payload = _payload(consumer, some_future_key=["kept", 1, None])

    updated = _updated_input(_run_hook(payload, cwd=consumer, tmp_path=tmp_path))

    expected = dict(payload["tool_input"])
    expected["model"] = kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]
    assert updated == expected


def test_a_full_model_id_is_never_what_gets_injected(tmp_path: Path) -> None:
    """The measured constraint: the Agent tool's model field rejects a full id."""
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    resolved = models.model_for(DECLARED_TIER, "anthropic", "anthropic", mapping=REFERENCE_MAP)
    assert resolved not in result.stdout


def test_a_directory_with_no_map_is_left_alone_though_the_kit_has_one_beside_it(
    tmp_path: Path,
) -> None:
    """The trap this hook is written around, with its own positive control.

    The hook lives at this repository's real path, so the resolver's kit-adjacent
    fallback has a perfectly good map to find. Injecting from it would pin a
    model in every unrelated repository on a machine where the hook is installed
    at user level.
    """
    bare = tmp_path / "unrelated-project"
    _definition(bare / ".claude" / "agents" / f"{AGENT_NAME}.md", tier=DECLARED_TIER)

    declined = _run_hook(_payload(bare), cwd=bare, tmp_path=tmp_path)

    assert declined.returncode == 0, declined.stderr
    assert declined.stdout == ""

    # Positive control: the identical payload in a repo that does have a map.
    installed = bare / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    rewritten = _run_hook(_payload(bare), cwd=bare, tmp_path=tmp_path)
    assert _updated_input(rewritten)["model"] == kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]


def test_the_subagent_model_environment_override_disables_the_rewrite(
    tmp_path: Path,
) -> None:
    """It outranks the parameter the hook writes, so a rewrite would be inert."""
    consumer = _consumer_repo(tmp_path)
    payload = _payload(consumer)

    assert _run_hook(payload, cwd=consumer, tmp_path=tmp_path).stdout != ""

    silenced = _run_hook(
        payload, cwd=consumer, tmp_path=tmp_path, env_extra={"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}
    )
    assert silenced.returncode == 0, silenced.stderr
    assert silenced.stdout == ""


def test_a_definition_that_pins_its_own_model_is_left_alone(tmp_path: Path) -> None:
    """The definition already answered the question the tier exists to answer."""
    consumer = _consumer_repo(tmp_path, model="claude-sonnet-5")

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_spawn_that_already_asks_for_a_model_is_left_alone(tmp_path: Path) -> None:
    """An explicit per-invocation choice is not the hook's to overrule."""
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(_payload(consumer, model="haiku"), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_definition_that_declares_no_tier_is_left_alone(tmp_path: Path) -> None:
    """Fail closed: no declared tier means the host's own default stands."""
    consumer = _consumer_repo(tmp_path, tier=None)

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_subagent_with_no_definition_at_all_is_left_alone(tmp_path: Path) -> None:
    """A built-in agent type has no file to declare a tier in."""
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(
        _payload(consumer, subagent_type="a-type-with-no-definition"),
        cwd=consumer,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_tier_the_map_marks_unavailable_pins_nothing(tmp_path: Path) -> None:
    """Never a neighbouring tier's alias, which is the failure worth preventing."""
    consumer = _consumer_repo(tmp_path)
    installed = consumer / ".basicly" / "core" / "models" / MAP.name
    doctored = json.loads(json.dumps(REFERENCE_MAP))
    cell = doctored["tiers"][DECLARED_TIER]["vendors"]["anthropic"]["surfaces"]["anthropic"]
    cell["status"] = "unavailable"
    cell.pop("model", None)
    installed.write_text(json.dumps(doctored), encoding="utf-8")

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize("tool_name", ["Read", "Bash", "Write"], ids=["read", "bash", "write"])
def test_a_call_that_is_not_an_agent_spawn_is_left_alone(tool_name: str, tmp_path: Path) -> None:
    """The hook may be wired to a wider matcher than it answers for."""
    consumer = _consumer_repo(tmp_path)
    payload = _payload(consumer)
    payload["tool_name"] = tool_name

    result = _run_hook(payload, cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


@pytest.mark.parametrize(
    "text",
    ["", "not json at all", "[]", '{"tool_name": "Agent", "tool_input": "not a dict"}'],
    ids=["empty", "garbage", "not-an-object", "input-not-an-object"],
)
def test_input_the_hook_cannot_use_exits_zero_without_output(text: str, tmp_path: Path) -> None:
    """A bug here must never be able to stop an agent from spawning."""
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(text, cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
