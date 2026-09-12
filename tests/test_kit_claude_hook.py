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

AGENT_TOOL_MODEL_ENUM = {"sonnet", "opus", "haiku", "fable"}


def _load_kit() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tier_resolver_for_hook_tests", RESOLVER)
    assert spec and spec.loader
    module: ModuleType = importlib.util.module_from_spec(spec)
    sys.modules["tier_resolver_for_hook_tests"] = module
    spec.loader.exec_module(module)
    return module


kit = _load_kit()


def _resolver(map_path: Path = MAP):
    resolver = kit.TierResolver.from_map_path(map_path)
    assert resolver is not None
    return resolver


def _definition(path: Path, tier: str | None = None, model: str | None = None) -> Path:
    lines = ["---", f"name: {path.stem}", "description: An agent basicly never shipped."]
    if tier is not None:
        lines.append(f"tier: {tier}")
    if model is not None:
        lines.append(f"model: {model}")
    lines += ["---", "", "Do the thing.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def test_the_alias_table_covers_exactly_the_maps_tier_vocabulary() -> None:
    assert set(kit.HOST_MODEL_ALIASES["claude"]) == set(MODEL_TIERS)


def test_every_alias_is_one_the_agent_tool_would_accept() -> None:
    assert set(kit.HOST_MODEL_ALIASES["claude"].values()) == AGENT_TOOL_MODEL_ENUM


def test_every_alias_names_the_model_its_own_tier_resolves_to() -> None:
    surface, vendor = kit.HOST_SURFACES["claude"]
    for tier, alias in kit.HOST_MODEL_ALIASES["claude"].items():
        model = models.model_for(tier, vendor, surface, mapping=REFERENCE_MAP)
        assert models.same_model(alias, model), f"{tier}: {alias} does not name {model}"


def test_the_alias_check_can_tell_two_tiers_apart() -> None:
    surface, vendor = kit.HOST_SURFACES["claude"]
    low = models.model_for("low", vendor, surface, mapping=REFERENCE_MAP)
    assert not models.same_model(kit.HOST_MODEL_ALIASES["claude"]["high"], low)


def test_a_resolved_model_carries_its_alias(tmp_path: Path) -> None:
    definition = _definition(tmp_path / f"{AGENT_NAME}.md", tier=DECLARED_TIER)
    result = _resolver().resolve("claude", definition=definition)
    assert result.model == models.model_for(
        DECLARED_TIER, "anthropic", "anthropic", mapping=REFERENCE_MAP
    )
    assert result.alias == kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]


def test_a_host_with_no_narrower_vocabulary_resolves_a_model_but_no_alias() -> None:
    result = _resolver().resolve("copilot", tier=DECLARED_TIER)
    assert result.model is not None
    assert result.alias is None


def test_an_unavailable_cell_resolves_neither_a_model_nor_an_alias(tmp_path: Path) -> None:

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


def test_find_map_without_the_kit_fallback_answers_only_for_its_own_tree(
    tmp_path: Path,
) -> None:

    bare = tmp_path / "unrelated-project"
    bare.mkdir()
    assert kit.find_map(bare) == MAP
    assert kit.find_map(bare, beside_the_kit=False) is None


def test_find_map_without_the_kit_fallback_still_finds_the_repo_being_worked_in(
    tmp_path: Path,
) -> None:
    installed = tmp_path / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    nested = tmp_path / "src" / "deep"
    nested.mkdir(parents=True)
    assert kit.find_map(nested, beside_the_kit=False) == installed


def _pruned_env(tmp_path: Path, extra: dict[str, str] | None = None) -> dict[str, str]:

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
    consumer = tmp_path / "consumer"
    installed = consumer / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    _definition(consumer / ".claude" / "agents" / f"{AGENT_NAME}.md", tier=tier, **kwargs)
    return consumer


def _updated_input(result: subprocess.CompletedProcess[str]) -> dict:
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    specific = payload["hookSpecificOutput"]
    assert specific["hookEventName"] == "PreToolUse"
    return specific["updatedInput"]


def test_the_hook_runs_where_basicly_is_neither_importable_nor_on_path(
    tmp_path: Path,
) -> None:
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
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    updated = _updated_input(result)
    assert updated["model"] == kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]
    assert updated["model"] in AGENT_TOOL_MODEL_ENUM


def test_the_rewrite_carries_the_whole_original_tool_input(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)
    payload = _payload(consumer, some_future_key=["kept", 1, None])

    updated = _updated_input(_run_hook(payload, cwd=consumer, tmp_path=tmp_path))

    expected = dict(payload["tool_input"])
    expected["model"] = kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]
    assert updated == expected


def test_a_full_model_id_is_never_what_gets_injected(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    resolved = models.model_for(DECLARED_TIER, "anthropic", "anthropic", mapping=REFERENCE_MAP)
    assert resolved not in result.stdout


def test_a_directory_with_no_map_is_left_alone_though_the_kit_has_one_beside_it(
    tmp_path: Path,
) -> None:

    bare = tmp_path / "unrelated-project"
    _definition(bare / ".claude" / "agents" / f"{AGENT_NAME}.md", tier=DECLARED_TIER)

    declined = _run_hook(_payload(bare), cwd=bare, tmp_path=tmp_path)

    assert declined.returncode == 0, declined.stderr
    assert declined.stdout == ""

    installed = bare / ".basicly" / "core" / "models" / MAP.name
    installed.parent.mkdir(parents=True)
    shutil.copy2(MAP, installed)
    rewritten = _run_hook(_payload(bare), cwd=bare, tmp_path=tmp_path)
    assert _updated_input(rewritten)["model"] == kit.HOST_MODEL_ALIASES["claude"][DECLARED_TIER]


def test_the_subagent_model_environment_override_disables_the_rewrite(
    tmp_path: Path,
) -> None:
    consumer = _consumer_repo(tmp_path)
    payload = _payload(consumer)

    assert _run_hook(payload, cwd=consumer, tmp_path=tmp_path).stdout != ""

    silenced = _run_hook(
        payload, cwd=consumer, tmp_path=tmp_path, env_extra={"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}
    )
    assert silenced.returncode == 0, silenced.stderr
    assert silenced.stdout == ""


def test_a_definition_that_pins_its_own_model_is_left_alone(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path, model="claude-sonnet-5")

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_spawn_that_already_asks_for_a_model_is_left_alone(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(_payload(consumer, model="haiku"), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_definition_that_declares_no_tier_is_left_alone(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path, tier=None)

    result = _run_hook(_payload(consumer), cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_subagent_with_no_definition_at_all_is_left_alone(tmp_path: Path) -> None:
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(
        _payload(consumer, subagent_type="a-type-with-no-definition"),
        cwd=consumer,
        tmp_path=tmp_path,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""


def test_a_tier_the_map_marks_unavailable_pins_nothing(tmp_path: Path) -> None:
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
    consumer = _consumer_repo(tmp_path)

    result = _run_hook(text, cwd=consumer, tmp_path=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
