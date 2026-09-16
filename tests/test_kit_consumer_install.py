from __future__ import annotations

import json
import subprocess  # nosec B404
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGES = REPO_ROOT / "packages"
KITS = ("comments", "tracker", "tier")


def _wheel(kit: str, out_dir: Path) -> Path:
    built = subprocess.run(  # nosec B603 B607
        ["uv", "build", "--wheel", str(PACKAGES / f"basicly-{kit}"), "--out-dir", str(out_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr
    return next(out_dir.glob("*.whl"))


def _from_wheel(wheel: Path, kit: str, cwd: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603 B607
        ["uvx", "--from", str(wheel), f"basicly-{kit}", *argv],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def consumer(tmp_path: Path) -> Path:
    repo = tmp_path / "consumer"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=False)  # nosec B603 B607
    return repo


@pytest.mark.parametrize("kit", KITS)
def test_a_consumer_installs_the_kit_from_a_built_wheel(
    kit: str, consumer: Path, tmp_path: Path
) -> None:

    wheel = _wheel(kit, tmp_path / "dist")

    installed = _from_wheel(wheel, kit, consumer, "init")

    assert installed.returncode == 0, installed.stderr
    vendored = consumer / ".basicly" / "kit" / kit
    assert vendored.is_dir(), installed.stdout
    assert (consumer / ".claude" / "skills" / kit / "SKILL.md").is_file()


def test_the_tier_kit_wires_the_host_and_resolves_without_basicly(
    consumer: Path, tmp_path: Path
) -> None:

    agent = consumer / ".claude" / "agents" / "scribe.md"
    agent.parent.mkdir(parents=True)
    agent.write_text(
        "---\nname: scribe\ndescription: A consumer agent.\ntools: Read\ntier: low\n---\nok\n",
        encoding="utf-8",
    )
    wheel = _wheel("tier", tmp_path / "dist")
    assert not (consumer / ".basicly" / "core").exists(), "the control: no catalog here"

    assert _from_wheel(wheel, "tier", consumer, "init").returncode == 0

    settings = json.loads((consumer / ".claude" / "settings.json").read_text(encoding="utf-8"))
    ours = [g for g in settings["hooks"]["PreToolUse"] if g.get("matcher") == "Agent"]
    assert len(ours) == 1
    hook = consumer / ".basicly" / "kit" / "tier" / "claude_tier_hook.py"
    payload = json.dumps({
        "tool_name": "Agent",
        "cwd": str(consumer),
        "tool_input": {"subagent_type": "scribe", "prompt": "x"},
    })
    rewritten = subprocess.run(  # nosec B603
        ["python3", "-S", "-I", str(hook)],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rewritten.returncode == 0, rewritten.stderr
    chosen = json.loads(rewritten.stdout)["hookSpecificOutput"]["updatedInput"]["model"]
    assert chosen == "haiku", "tier low must resolve from the map the kit vendored beside itself"


def test_uninstalling_from_a_wheel_leaves_no_hook_naming_a_deleted_kit(
    consumer: Path, tmp_path: Path
) -> None:

    wheel = _wheel("tier", tmp_path / "dist")
    assert _from_wheel(wheel, "tier", consumer, "init").returncode == 0

    removed = _from_wheel(wheel, "tier", consumer, "uninstall")

    assert removed.returncode == 0, removed.stderr
    assert not (consumer / ".basicly" / "kit" / "tier").exists()
    settings_file = consumer / ".claude" / "settings.json"
    settings = (
        json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.is_file() else {}
    )
    agent_hooks = [
        g
        for g in (settings.get("hooks", {}).get("PreToolUse") or [])
        if g.get("matcher") == "Agent"
    ]
    assert agent_hooks == [], "a hook naming a deleted kit is the defect this pins"


def test_the_comments_kit_refuses_prose_and_removes_it_without_basicly(
    consumer: Path, tmp_path: Path
) -> None:

    wheel = _wheel("comments", tmp_path / "dist")
    assert _from_wheel(wheel, "comments", consumer, "init").returncode == 0
    sample = consumer / "sample.py"
    sample.write_text("def f():\n    # a prose comment a human wrote\n    return 1\n", "utf-8")
    cli = consumer / ".basicly" / "kit" / "comments" / "cli.py"

    refused = subprocess.run(  # nosec B603
        ["python3", str(cli), "check", str(sample)], capture_output=True, text=True, check=False
    )
    fixed = subprocess.run(  # nosec B603
        ["python3", str(cli), "fix", str(sample)], capture_output=True, text=True, check=False
    )

    assert refused.returncode == 1, refused.stdout
    assert fixed.returncode == 0, fixed.stderr
    assert "prose comment" not in sample.read_text(encoding="utf-8")


def test_the_tracker_kit_holds_a_record_without_basicly(consumer: Path, tmp_path: Path) -> None:

    wheel = _wheel("tracker", tmp_path / "dist")
    assert _from_wheel(wheel, "tracker", consumer, "init").returncode == 0
    cli = consumer / ".basicly" / "kit" / "tracker" / "cli.py"
    ledger = consumer / ".basicly" / "ledger"

    created = subprocess.run(  # nosec B603
        ["python3", str(cli), "create", str(ledger), "--prefix", "acme", "--title", "a record"],
        capture_output=True,
        text=True,
        check=False,
    )
    ready = subprocess.run(  # nosec B603
        ["python3", str(cli), "ready", str(ledger)], capture_output=True, text=True, check=False
    )

    assert created.returncode == 0, created.stderr
    assert ledger.is_dir(), "create makes its own ledger; a consumer needs no mkdir"
    assert json.loads(ready.stdout)["count"] == 1


@pytest.mark.parametrize("kit", KITS)
def test_uninstalling_leaves_no_residue(kit: str, consumer: Path, tmp_path: Path) -> None:

    wheel = _wheel(kit, tmp_path / "dist")
    assert _from_wheel(wheel, kit, consumer, "init").returncode == 0

    assert _from_wheel(wheel, kit, consumer, "uninstall").returncode == 0

    assert not (consumer / ".basicly" / "kit" / kit).exists()
    for root in (".claude/skills", ".agents/skills"):
        assert not (consumer / root / kit).exists(), f"{root} kept the skill"
    for name in (".gitignore", ".gitattributes"):
        marker = consumer / name
        if marker.is_file():
            assert "basicly" not in marker.read_text(encoding="utf-8"), f"{name} kept a line"
