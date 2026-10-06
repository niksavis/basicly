from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kit_packages import CATALOG, KITS, _kit, installer


@pytest.mark.parametrize("kit", KITS)
def test_init_writes_the_skill_into_every_root_an_agent_reads(kit: str, tmp_path: Path) -> None:
    installer.run(_kit(kit), ["init", "--into", str(tmp_path)])
    expected = (
        (CATALOG / kit / "GUIDANCE.md")
        .read_text(encoding="utf-8")
        .replace("\n---\n", f"\n---\n<!-- basicly-kit:{kit} skill -->\n", 1)
    )
    for root in (".claude/skills", ".agents/skills"):
        path = tmp_path / root / kit / "SKILL.md"
        assert path.is_file()
        assert path.read_text(encoding="utf-8") == expected


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
