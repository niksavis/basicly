from __future__ import annotations

import os
from pathlib import Path
from typing import NamedTuple

KITS_ROOT = Path(".basicly")
USER_SKILL_ROOT = Path(".claude") / "skills"
USER_MARK = "<!-- written by {command} init --user; {command} uninstall --user removes it -->"


class UserSkill(NamedTuple):
    command: str
    engine_use: str
    places: tuple
    replacements: tuple


def sandbox_file(name: str) -> Path:
    return KITS_ROOT / f"{name}.pyz"


def user_home(environ=None) -> Path:
    values = os.environ if environ is None else environ
    return Path(values.get("HOME") or values.get("USERPROFILE") or Path.home())


def user_skill_path(home: Path, skill: UserSkill) -> Path:
    return home / USER_SKILL_ROOT / skill.command / "SKILL.md"


def _where(skill: UserSkill) -> str:
    sandbox, vendored = skill.places
    return "\n".join((
        "## Where this applies",
        "",
        "Use this skill only in a repository that holds `.basicly/ledger/`. If the repository",
        f"also holds `.basicly/core/`, it runs the basicly engine: use {skill.engine_use} instead.",
        "",
        f"This skill writes the command as `{skill.command}`. Type the one the repository holds:",
        "",
        f"- if `{sandbox}` exists, type `python3 {sandbox}`;",
        f"- else if `{vendored}` exists, type `python3 {vendored}`;",
        f"- else type `{skill.command}`.",
        "",
        "Each command refuses to run when its version differs from the one the ledger pins in",
        "`.basicly/ledger/.kit-version`, and it prints the install command for the pinned one.",
        "",
    ))


def user_skill_body(guidance: str, skill: UserSkill) -> str:
    _, front, body = guidance.split("---\n", 2)
    for old, new in skill.replacements:
        if old not in guidance:
            raise SystemExit(f"{skill.command}: the kit guidance no longer holds {old!r}")
        front, body = front.replace(old, new), body.replace(old, new)
    title, _, rest = body.lstrip("\n").partition("\n")
    mark = USER_MARK.format(command=skill.command)
    return f"---\n{front}---\n{mark}\n\n{title}\n\n{_where(skill)}{rest}"


def install_user(kit_dir: Path, skill: UserSkill, home: Path, stream) -> int:
    body = user_skill_body((kit_dir / "GUIDANCE.md").read_text(encoding="utf-8"), skill)
    path = user_skill_path(home, skill)
    if path.is_file() and path.read_text(encoding="utf-8") == body:
        stream.write(f"{skill.command}: the user skill at {path} is current; nothing changed\n")
        return 0
    if path.is_file() and USER_MARK.format(command=skill.command) not in path.read_text("utf-8"):
        raise SystemExit(
            f"{skill.command}: {path} holds a skill this installer did not write, so it was "
            f"left alone; move it aside and re-run `{skill.command} init --user`"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    stream.write(f"{skill.command}: wrote the user skill to {path}\n")
    return 0


def uninstall_user(skill: UserSkill, home: Path, stream) -> int:
    path = user_skill_path(home, skill)
    if not path.is_file():
        stream.write(f"{skill.command}: no user skill at {path}; nothing removed\n")
        return 0
    if USER_MARK.format(command=skill.command) not in path.read_text(encoding="utf-8"):
        raise SystemExit(f"{skill.command}: {path} was not written by this installer; kept")
    path.unlink()
    if not any(path.parent.iterdir()):
        path.parent.rmdir()
    stream.write(f"{skill.command}: removed the user skill {path}\n")
    return 0
