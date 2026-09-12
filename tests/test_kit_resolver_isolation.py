from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from basicly import models
from basicly.schema import MODEL_TIERS
from tests.kit_resolver_helpers import (
    COPILOT_SURFACE,
    KIT,
    MAP,
    NEIGHBOUR_TIER,
    REFERENCE_MAP,
    UNAVAILABLE_TIER,
    UNAVAILABLE_VENDOR,
    _definition,
    _expected,
    _resolver,
    kit,
)


def test_the_kit_imports_nothing_but_the_standard_library() -> None:
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


def test_the_host_surface_map_mirrors_the_package_exactly() -> None:
    assert dict(models.FAMILY_MODEL_SURFACES) == kit.HOST_SURFACES


def test_every_cell_resolves_the_same_as_the_in_harness_resolver() -> None:
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
    assert resolved > 0 and empty > 0, f"resolved={resolved} empty={empty}"


def test_the_tier_vocabulary_comes_from_the_map_not_a_second_copy() -> None:
    assert _resolver().tier_order == tuple(MODEL_TIERS)


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
    consumer = tmp_path / "consumer"
    core = consumer / ".basicly" / "core"
    (core / "kit").mkdir(parents=True)
    shutil.copy2(KIT, core / "kit" / KIT.name)
    (core / "models").mkdir(parents=True)
    shutil.copy2(MAP, core / "models" / MAP.name)
    _definition(consumer / ".claude" / "agents" / "my-own-agent.md", tier="high")
    return consumer


def _pruned_env(tmp_path: Path) -> dict[str, str]:

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
