from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
import sysconfig
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

KITS_ROOT = Path(".basicly")
USER_SKILL_ROOT = Path(".claude") / "skills"
USER_SKILL_ROOTS = (USER_SKILL_ROOT, Path(".agents") / "skills")
USER_MARK = "<!-- written by {command} init --user; {command} uninstall --user removes it -->"
SANDBOX_FLAG = "--sandbox"
RENDERED = ("GUIDANCE.md", "INSTRUCTION.md")


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


def user_skill_paths(home: Path, skill: UserSkill) -> list[Path]:
    return [home / folder / skill.command / "SKILL.md" for folder in USER_SKILL_ROOTS]


def _owned_user_skills(home: Path, skill: UserSkill) -> list[Path]:
    paths = user_skill_paths(home, skill)
    for path in paths:
        if path.is_file() and USER_MARK.format(command=skill.command) not in path.read_text(
            "utf-8"
        ):
            raise SystemExit(
                f"{skill.command}: {path} holds a skill this installer did not write "
                "(not written by this installer), so it was "
                f"left alone; move it aside and re-run `{skill.command} init --user`"
            )
    return paths


def install_user(kit_dir: Path, skill: UserSkill, home: Path, stream) -> int:
    body = user_skill_body((kit_dir / "GUIDANCE.md").read_text(encoding="utf-8"), skill)
    for path in _owned_user_skills(home, skill):
        if path.is_file() and path.read_text(encoding="utf-8") == body:
            stream.write(f"{skill.command}: the user skill at {path} is current; nothing changed\n")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        stream.write(f"{skill.command}: wrote the user skill to {path}\n")
    return 0


def uninstall_user(skill: UserSkill, home: Path, stream) -> int:
    for path in _owned_user_skills(home, skill):
        if not path.is_file():
            stream.write(f"{skill.command}: no user skill at {path}; nothing removed\n")
            continue
        path.unlink()
        if not any(path.parent.iterdir()):
            path.parent.rmdir()
        stream.write(f"{skill.command}: removed the user skill {path}\n")
    return 0


class Package(NamedTuple):
    installer: Any
    kit: Any
    user: UserSkill
    source: str
    replacements: Callable[[str], tuple]
    bundle: Callable[[Path], Path]
    check: tuple = ()


def user_command(command: str, environ=None, scripts: str | None = None) -> str | None:
    values = os.environ if environ is None else environ
    running = Path(scripts or sysconfig.get_path("scripts")).resolve()
    entries = [
        entry
        for entry in values.get("PATH", "").split(os.pathsep)
        if entry and Path(entry).resolve() != running
    ]
    return shutil.which(command, path=os.pathsep.join(entries))


def runner(package: Package, sandbox: bool) -> str:
    return f"python3 {package.user.places[0]}" if sandbox else package.user.command


def _render(package: Package, into: Path, typed: str) -> Path:
    for name in RENDERED:
        source = package.kit.directory / name
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        for old, new in package.replacements(typed):
            if old in text:
                text = text.replace(old, new)
        (into / name).write_text(text, encoding="utf-8")
    return into


def _write_guidance(package: Package, request, typed: str, *, skill: bool) -> None:
    installer, name = package.installer, package.kit.name
    with tempfile.TemporaryDirectory() as scratch:
        rendered = _render(package, Path(scratch), typed)
        if skill:
            installer.write_skill(rendered, request.target, name, request.stream)
        if request.instructions:
            installer.write_block(rendered, request.target, name, request.stream)


def _refuse_no_user_install(package: Package) -> None:
    command = package.user.command
    raise SystemExit(
        f"{command}: the default mode runs {command} from a user install, and none is on "
        f"PATH outside this run, so nothing was installed. Install it once per machine with "
        f"`uv tool install --force '{package.source}'`, or run `{command} init "
        f"{SANDBOX_FLAG}` to keep one file in this repository instead"
    )


def _check_version(package: Package, request, found: str) -> None:
    if not package.check:
        return
    done = subprocess.run(  # noqa: S603 # nosec B603 — only the installed tool knows its version; reading its files would guess at uv's layout
        [found, *package.check],
        cwd=request.target,
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise SystemExit(
            f"{package.user.command}: the user install at {found} refused this repository, "
            f"so the vendored copy was kept:\n{done.stdout}{done.stderr}"
        )


def _drop_file(target: Path, relative: Path, stream, command: str) -> None:
    path = target / relative
    if path.is_file():
        path.unlink()
        stream.write(f"{command}: removed {relative.as_posix()}\n")


def install_mode(package: Package, request, sandbox: bool) -> int:
    installer, kit, command = package.installer, package.kit, package.user.command
    target, stream = request.target, request.stream
    installer.refuse_second_copy(target, kit.name, command)
    found = None if sandbox else user_command(command)
    if not sandbox and found is None:
        _refuse_no_user_install(package)
    declared = () if kit.rules is None else tuple(kit.rules(kit.directory))
    installer.write_rules(request, declared)
    typed = runner(package, sandbox)
    if sandbox:
        package.bundle(target / package.user.places[0])
        stream.write(f"{command}: wrote {package.user.places[0]}; run it as {typed} <verb>\n")
    again = f"{typed} init{' ' + SANDBOX_FLAG if sandbox else ''} --import {{source}}"
    passed = ("--runner", typed, "--import-command", again)
    configured = request._replace(kit=kit._replace(configure_args=(*kit.configure_args, *passed)))
    if kit.configure_file:
        installer.configure_host(configured, kit.directory)
    if found is not None:
        _check_version(package, request, found)
        _drop_file(target, Path(package.user.places[0]), stream, command)
        install_user(kit.directory, package.user, user_home(), stream)
    _write_guidance(package, request, typed, skill=found is None)
    if found is not None:
        installer.drop_skill(target, kit.name, stream)
    drop_vendored(installer, request)
    return 0


def drop_vendored(installer, request) -> None:
    destination = installer.vendored_root(request.target, request.kit.name)
    rule_file, cache = installer.cache_ignore_rule(request.kit.name)
    installer.drop_lines(request.target / rule_file, set(cache))
    if not destination.is_dir():
        return
    shipped = {
        path.relative_to(request.kit.directory)
        for path in installer.kit_files(request.kit.directory)
    }
    for path in sorted(destination.rglob("*"), reverse=True):
        relative = path.relative_to(destination)
        if path.is_dir():
            if not any(path.iterdir()):
                path.rmdir()
        elif relative in shipped or path.suffix == ".pyc":
            path.unlink()
    if destination.is_dir() and not any(destination.iterdir()):
        installer.prune_empty(destination, request.target.resolve())
    left = "; files the kit did not ship were kept" if destination.is_dir() else ""
    request.stream.write(f"{request.kit.name}: removed the vendored copy {destination}{left}\n")


def uninstall_mode(package: Package, request) -> int:
    installer, kit = package.installer, package.kit
    _drop_file(request.target, Path(package.user.places[0]), request.stream, package.user.command)
    if kit.configure_file:
        installer.unconfigure_host(request, kit.directory)
    return installer.uninstall(request)
