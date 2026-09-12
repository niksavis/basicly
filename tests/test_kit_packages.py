from __future__ import annotations

import importlib.util
import subprocess  # nosec B404
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = REPO_ROOT / "packages"
CATALOG = REPO_ROOT / ".basicly" / "core" / "kit"
KITS = ("comments", "tracker", "tier")
CLI_FILE = {"comments": "cli.py", "tracker": "cli.py", "tier": "tier_resolver.py"}


def _installer():
    spec = importlib.util.spec_from_file_location("kit_installer", PACKAGES / "kit_installer.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["kit_installer"] = module
    spec.loader.exec_module(module)
    return module


installer = _installer()


def ledger_rules(directory: Path):
    log_glob = installer.read_kit_constant(directory, "events.py", "LOG_GLOB")
    derived = installer.read_kit_constant(directory, "snapshot.py", "DERIVED_PATTERNS")
    return (
        (".gitattributes", (f"{log_glob} -text merge=union",)),
        (".gitignore", tuple(f".basicly/ledger/{pattern}" for pattern in derived)),
    )


def _kit(name: str):
    return installer.Kit(
        command=f"basicly-{name}",
        name=name,
        directory=CATALOG / name,
        module=f"basicly_{name}_under_test",
        cli_file=CLI_FILE[name],
        rules=ledger_rules if name == "tracker" else None,
    )


@pytest.mark.parametrize("kit", KITS)
def test_every_kit_has_a_package_that_names_the_same_command(kit: str) -> None:
    manifest = (PACKAGES / f"basicly-{kit}" / "pyproject.toml").read_text(encoding="utf-8")

    assert f'name = "basicly-{kit}"' in manifest
    assert f'basicly-{kit} = "basicly_{kit}:main"' in manifest
    assert 'requires-python = ">=3.9"' in manifest
    assert "dependencies = []" in manifest


@pytest.mark.parametrize("kit", KITS)
def test_a_package_ships_the_kit_from_its_one_source(kit: str) -> None:
    manifest = (PACKAGES / f"basicly-{kit}" / "pyproject.toml").read_text(encoding="utf-8")

    assert f'"../../.basicly/core/kit/{kit}" = "basicly_{kit}/kit"' in manifest
    assert f'"../kit_installer.py" = "basicly_{kit}/installer.py"' in manifest


def test_no_kit_is_copied_into_the_packages_tree() -> None:
    strays = [
        str(path.relative_to(REPO_ROOT))
        for path in PACKAGES.rglob("*.py")
        if path.name not in {"kit_installer.py", "__init__.py"}
    ]
    assert strays == []


@pytest.mark.parametrize("kit", KITS)
def test_install_then_uninstall_returns_the_tree_to_its_original_state(
    kit: str, tmp_path: Path
) -> None:
    (tmp_path / "keep.txt").write_text("mine\n", encoding="utf-8")
    before = sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*"))

    assert installer.run(_kit(kit), ["init", "--into", str(tmp_path)]) == 0
    assert (tmp_path / ".basicly" / "kit" / kit).is_dir()
    assert installer.run(_kit(kit), ["uninstall", "--into", str(tmp_path)]) == 0

    assert sorted(str(path.relative_to(tmp_path)) for path in tmp_path.rglob("*")) == before


@pytest.mark.parametrize("kit", KITS)
def test_a_second_install_changes_nothing(kit: str, tmp_path: Path, capsys) -> None:
    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])
    capsys.readouterr()

    assert installer.run(_kit(kit), ["update", "--into", str(tmp_path)]) == 0

    assert "already installed at this version" in capsys.readouterr().out


@pytest.mark.parametrize("kit", KITS)
def test_status_refuses_before_an_install_and_passes_after(kit: str, tmp_path: Path) -> None:
    assert installer.run(_kit(kit), ["status", "--into", str(tmp_path)]) == 1

    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])

    assert installer.run(_kit(kit), ["status", "--into", str(tmp_path)]) == 0


@pytest.mark.parametrize("kit", KITS)
def test_the_vendored_kit_runs_under_a_bare_interpreter(kit: str, tmp_path: Path) -> None:
    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])
    entry = tmp_path / ".basicly" / "kit" / kit / CLI_FILE[kit]

    completed = subprocess.run(  # nosec B603
        [sys.executable, "-I", "-S", str(entry), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin"},
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout, "the vendored kit printed nothing, so it did not run"


def test_a_kit_constant_is_read_from_the_kit_not_spelled_again() -> None:
    kit_dir = CATALOG / "tracker"

    assert installer.read_kit_constant(kit_dir, "events.py", "LOG_GLOB") == "events-*.jsonl"
    assert installer.read_kit_constant(kit_dir, "snapshot.py", "DERIVED_PATTERNS") == (
        "snapshot.jsonl",
        "checkpoint-*.jsonl",
    )


def test_the_tracker_shim_declares_the_rules_the_installer_writes() -> None:
    shim = (PACKAGES / "basicly-tracker" / "basicly_tracker" / "__init__.py").read_text("utf-8")

    assert "rules=ledger_rules" in shim
    assert '"events.py", "LOG_GLOB"' in shim
    assert '"snapshot.py", "DERIVED_PATTERNS"' in shim


def test_the_tracker_install_writes_the_attribute_git_actually_reads(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # nosec B603 B607
    (tmp_path / ".gitattributes").write_text("* text=auto\n", encoding="utf-8")

    installer.run(_kit("tracker"), ["init", "--into", str(tmp_path)])

    answer = subprocess.run(  # nosec B603 B607
        ["git", "check-attr", "text", "merge", "--", ".basicly/ledger/events-0001.jsonl"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "text: unset" in answer
    assert "merge: union" in answer


def test_the_tracker_install_ignores_the_derived_files(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)  # nosec B603 B607

    installer.run(_kit("tracker"), ["init", "--into", str(tmp_path)])

    for name in ("snapshot.jsonl", "checkpoint-0001.jsonl"):
        ignored = subprocess.run(  # nosec B603 B607
            ["git", "check-ignore", "-q", "--no-index", "--", f".basicly/ledger/{name}"],
            cwd=tmp_path,
            capture_output=True,
            check=False,
        )
        assert ignored.returncode == 0, f"{name} is not ignored, so a derived file can be committed"


def test_the_tracker_status_refuses_when_the_attribute_is_gone(tmp_path: Path) -> None:
    installer.run(_kit("tracker"), ["init", "--into", str(tmp_path)])
    (tmp_path / ".gitattributes").unlink()

    assert installer.run(_kit("tracker"), ["status", "--into", str(tmp_path)]) == 1


def test_an_unwritable_rule_file_installs_nothing(tmp_path: Path) -> None:
    (tmp_path / ".gitattributes").mkdir()

    with pytest.raises(SystemExit, match="nothing was installed"):
        installer.run(_kit("tracker"), ["init", "--into", str(tmp_path)])

    assert not (tmp_path / ".basicly").exists()


@pytest.mark.parametrize("kit", KITS)
def test_the_built_wheel_carries_the_kit_and_the_installer(kit: str, tmp_path: Path) -> None:
    built = subprocess.run(  # nosec B603 B607
        ["uv", "build", "--wheel", str(PACKAGES / f"basicly-{kit}"), "--out-dir", str(tmp_path)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr

    wheel = next(tmp_path.glob("*.whl"))
    names = zipfile.ZipFile(wheel).namelist()
    assert f"basicly_{kit}/installer.py" in names
    assert f"basicly_{kit}/kit/{CLI_FILE[kit]}" in names
    assert not [name for name in names if name.endswith(".pyc")]


def test_the_tier_wheel_carries_the_model_map_beside_its_resolver(tmp_path: Path) -> None:
    subprocess.run(  # nosec B603 B607
        ["uv", "build", "--wheel", str(PACKAGES / "basicly-tier"), "--out-dir", str(tmp_path)],
        capture_output=True,
        check=True,
    )

    wheel = next(tmp_path.glob("*.whl"))

    assert "basicly_tier/kit/model-map.json" in zipfile.ZipFile(wheel).namelist()


@pytest.mark.parametrize("kit", KITS)
def test_every_kit_carries_a_skill_and_an_always_on_block(kit: str) -> None:
    guidance = CATALOG / kit / "GUIDANCE.md"
    instruction = CATALOG / kit / "INSTRUCTION.md"

    assert guidance.is_file(), "a kit with no skill is code an agent never calls"
    assert instruction.is_file(), "a kit with no always-on block is a skill nothing triggers"

    body = guidance.read_text(encoding="utf-8")
    assert body.startswith("---\n"), "the skill needs frontmatter or no host will index it"
    assert "\nname:" in body
    assert "\ndescription:" in body


@pytest.mark.parametrize("kit", KITS)
def test_the_skill_description_says_when_to_use_it(kit: str) -> None:
    body = (CATALOG / kit / "GUIDANCE.md").read_text(encoding="utf-8")
    description = body.split("description:", 1)[1].split("\n", 1)[0].lower()

    assert "use when" in description or "use whenever" in description, (
        "a description that does not say when to reach for the skill is a skill nothing loads"
    )


@pytest.mark.parametrize("kit", KITS)
def test_init_writes_the_skill_into_every_root_an_agent_reads(kit: str, tmp_path: Path) -> None:
    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])

    for root in (".claude/skills", ".agents/skills"):
        path = tmp_path / root / kit / "SKILL.md"
        assert path.is_file(), f"{root} has no skill, so that agent family is never told"
        assert path.read_text(encoding="utf-8") == (CATALOG / kit / "GUIDANCE.md").read_text(
            encoding="utf-8"
        )


@pytest.mark.parametrize("kit", KITS)
def test_init_writes_no_third_copy_copilot_would_discover_again(kit: str, tmp_path: Path) -> None:
    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])

    assert not (tmp_path / ".github/skills" / kit / "SKILL.md").exists(), (
        "Copilot reads .github, .claude and .agents skill roots with no documented dedup, "
        "so a third identical copy is discovered a third time (basicly-sqn dropped it from "
        "the catalog; the kit installer kept writing it)"
    )


@pytest.mark.parametrize("kit", KITS)
def test_init_removes_a_third_copy_an_earlier_version_wrote(kit: str, tmp_path: Path) -> None:
    stale = tmp_path / ".github/skills" / kit / "SKILL.md"
    stale.parent.mkdir(parents=True)
    stale.write_text((CATALOG / kit / "GUIDANCE.md").read_text(encoding="utf-8"), encoding="utf-8")

    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])

    assert not stale.exists(), "an upgrade must clean the copy the previous version left"


def test_init_keeps_a_hand_authored_file_in_the_retired_root(tmp_path: Path) -> None:
    mine = tmp_path / ".github/skills/mine/SKILL.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("# mine\n\nNot written by any kit.\n", encoding="utf-8")

    installer.run(_kit("tier"), ["init", "--into", str(tmp_path)])

    assert mine.read_text(encoding="utf-8") == "# mine\n\nNot written by any kit.\n"


def test_init_keeps_a_third_copy_whose_body_is_not_ours(tmp_path: Path) -> None:
    theirs = tmp_path / ".github/skills/tier/SKILL.md"
    theirs.parent.mkdir(parents=True)
    theirs.write_text("# tier\n\nA consumer's own edit.\n", encoding="utf-8")

    installer.run(_kit("tier"), ["init", "--into", str(tmp_path)])

    assert theirs.read_text(encoding="utf-8") == "# tier\n\nA consumer's own edit.\n", (
        "only a byte-for-byte match with what we would write is ours to remove"
    )


@pytest.mark.parametrize("kit", KITS)
def test_uninstall_still_cleans_the_retired_root(kit: str, tmp_path: Path) -> None:
    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])
    stale = tmp_path / ".github/skills" / kit / "SKILL.md"
    stale.parent.mkdir(parents=True, exist_ok=True)
    stale.write_text((CATALOG / kit / "GUIDANCE.md").read_text(encoding="utf-8"), encoding="utf-8")

    installer.run(_kit(kit), ["uninstall", "--into", str(tmp_path)])

    assert not stale.exists()


@pytest.mark.parametrize("kit", KITS)
def test_init_does_not_touch_an_instruction_file_without_the_flag(kit: str, tmp_path: Path) -> None:
    original = "# mine\n\nMy own guidance.\n"
    (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")

    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])

    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == original


@pytest.mark.parametrize("kit", KITS)
def test_the_flag_writes_a_marked_block_that_a_second_run_does_not_duplicate(
    kit: str, tmp_path: Path
) -> None:
    (tmp_path / "CLAUDE.md").write_text("# mine\n\nMy own guidance.\n", encoding="utf-8")

    installer.run(_kit(kit), ["init", "--into", str(tmp_path), "--with-instructions"])
    once = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    installer.run(_kit(kit), ["init", "--into", str(tmp_path), "--with-instructions"])

    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == once
    assert once.count(f"<!-- basicly-kit:{kit} begin -->") == 1


@pytest.mark.parametrize("kit", KITS)
def test_uninstall_leaves_the_instruction_file_byte_for_byte(kit: str, tmp_path: Path) -> None:
    original = "# mine\n\nMy own guidance.\n"
    (tmp_path / "CLAUDE.md").write_text(original, encoding="utf-8")
    installer.run(_kit(kit), ["init", "--into", str(tmp_path), "--with-instructions"])

    installer.run(_kit(kit), ["uninstall", "--into", str(tmp_path)])

    assert (tmp_path / "CLAUDE.md").read_text(encoding="utf-8") == original
    assert not (tmp_path / ".claude").exists()


@pytest.mark.parametrize("kit", KITS)
def test_with_no_instruction_file_the_block_is_printed_rather_than_placed(
    kit: str, tmp_path: Path, capsys
) -> None:
    installer.run(_kit(kit), ["init", "--into", str(tmp_path), "--with-instructions"])

    printed = capsys.readouterr().out
    assert "no always-on instruction file here" in printed
    assert f"<!-- basicly-kit:{kit} begin -->" in printed


@pytest.mark.parametrize("kit", KITS)
def test_the_wheel_carries_the_guidance_so_a_consumer_gets_it(kit: str, tmp_path: Path) -> None:
    subprocess.run(  # nosec B603 B607
        ["uv", "build", "--wheel", str(PACKAGES / f"basicly-{kit}"), "--out-dir", str(tmp_path)],
        capture_output=True,
        check=True,
    )

    names = zipfile.ZipFile(next(tmp_path.glob("*.whl"))).namelist()

    assert f"basicly_{kit}/kit/GUIDANCE.md" in names
    assert f"basicly_{kit}/kit/INSTRUCTION.md" in names


def test_the_catalog_skill_and_the_kit_guidance_are_one_text() -> None:
    guidance = (CATALOG / "comments" / "GUIDANCE.md").read_text(encoding="utf-8")
    _, front, body = guidance.split("---\n", 2)
    described = dict(line.split(":", 1) for line in front.strip().split("\n"))

    source = yaml.safe_load(
        (REPO_ROOT / ".basicly" / "core" / "skills" / "no-comments" / "skill.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert source["name"] == described["name"].strip()
    assert " ".join(source["description"].split()) == " ".join(described["description"].split())
    assert source["instructions"].strip() == body.strip(), (
        "the catalog skill and the kit guidance have drifted; regenerate one from the other"
    )


@pytest.mark.parametrize("kit", KITS)
def test_a_standalone_install_refuses_where_basicly_already_manages_the_kit(
    kit: str, tmp_path: Path
) -> None:
    managed = tmp_path / ".basicly" / "core" / "kit" / kit
    managed.mkdir(parents=True)
    (managed / "cli.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(SystemExit, match="basicly already manages this kit"):
        installer.run(_kit(kit), ["init", "--into", str(tmp_path)])

    assert not (tmp_path / ".basicly" / "kit").exists()


@pytest.mark.parametrize("kit", KITS)
def test_status_names_the_managed_copy_as_the_one_in_use(kit: str, tmp_path: Path) -> None:
    (tmp_path / ".basicly" / "core" / "kit" / kit).mkdir(parents=True)

    assert installer.run(_kit(kit), ["status", "--into", str(tmp_path)]) == 0
