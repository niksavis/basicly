from __future__ import annotations

import shlex
from collections.abc import Sequence
from io import StringIO
from pathlib import Path
from typing import Protocol, runtime_checkable

import yaml
from ruamel.yaml import YAML


@runtime_checkable
class ManagedHook(Protocol):
    @property
    def id(self) -> str: ...

    @property
    def script(self) -> str: ...

    @property
    def stage(self) -> str: ...

    @property
    def pass_filenames(self) -> bool: ...

    @property
    def always_run(self) -> bool: ...

    @property
    def files(self) -> str: ...


HOOK_PYTHON = "uv run --no-project python"


def _hook_entry(spec: ManagedHook, hooks_relpath: str) -> dict:
    entry: dict = {
        "id": spec.id,
        "name": spec.id,
        "entry": f"{HOOK_PYTHON} {shlex.quote(f'{hooks_relpath}/{spec.script}')}",
        "language": "system",
        "stages": [spec.stage],
        "pass_filenames": spec.pass_filenames,
    }
    if spec.always_run:
        entry["always_run"] = True
    if spec.files:
        entry["files"] = spec.files
    return entry


def _managed_local_block(specs: Sequence[ManagedHook], hooks_relpath: str) -> dict:
    return {"repo": "local", "hooks": [_hook_entry(spec, hooks_relpath) for spec in specs]}


def _is_managed(hook: object, managed_ids: set[str], hooks_relpath: str) -> bool:

    if not isinstance(hook, dict):
        return False
    if hook.get("id") in managed_ids:
        return True
    entry = hook.get("entry")
    return isinstance(entry, str) and f"{hooks_relpath}/" in entry


def merge_precommit_config(
    existing: dict | None,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
    strip_ids: set[str] | None = None,
) -> dict:

    config = dict(existing) if isinstance(existing, dict) else {}
    managed_ids = strip_ids or {spec.id for spec in specs}

    kept: list = []
    for repo in config.get("repos") or []:
        if isinstance(repo, dict) and repo.get("repo") == "local":
            hooks = [
                hook
                for hook in (repo.get("hooks") or [])
                if not _is_managed(hook, managed_ids, hooks_relpath)
            ]
            if hooks:
                kept.append({**repo, "hooks": hooks})
        else:
            kept.append(repo)

    kept.append(_managed_local_block(specs, hooks_relpath))
    config["repos"] = kept
    return config


def _round_trip_yaml() -> YAML:
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096
    return ryaml


def _replace_managed_block(
    config: dict,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
    strip_ids: set[str] | None,
) -> None:

    managed_ids = strip_ids or {spec.id for spec in specs}
    repos = config.get("repos")
    if not isinstance(repos, list):
        repos = []
        config["repos"] = repos
    for ri in range(len(repos) - 1, -1, -1):
        repo = repos[ri]
        if not (isinstance(repo, dict) and repo.get("repo") == "local"):
            continue
        hooks = repo.get("hooks")
        if isinstance(hooks, list):
            for hi in range(len(hooks) - 1, -1, -1):
                hook = hooks[hi]
                if _is_managed(hook, managed_ids, hooks_relpath):
                    del hooks[hi]
        if not hooks:
            del repos[ri]
    repos.append(_managed_local_block(specs, hooks_relpath))


def render_precommit_config(
    existing_text: str | None,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
    strip_ids: set[str] | None = None,
) -> str:

    if not existing_text:
        merged = merge_precommit_config(None, specs, hooks_relpath, strip_ids)
        return yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)
    ryaml = _round_trip_yaml()
    config = ryaml.load(existing_text)
    if not isinstance(config, dict):
        merged = merge_precommit_config(None, specs, hooks_relpath, strip_ids)
        return yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)
    _replace_managed_block(config, specs, hooks_relpath, strip_ids)
    buf = StringIO()
    ryaml.dump(config, buf)
    return buf.getvalue()


def parse_config(config_path: Path, existing_text: str) -> dict:

    parsed = yaml.safe_load(existing_text)
    if not isinstance(parsed, dict):
        raise ValueError(f"{config_path}: not a valid pre-commit config (expected a mapping)")
    return parsed


def managed_hook_mismatches(
    config: dict,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
) -> list[str]:

    found: dict[str, dict] = {}
    for repo in config.get("repos") or []:
        if isinstance(repo, dict) and repo.get("repo") == "local":
            for hook in repo.get("hooks") or []:
                if isinstance(hook, dict) and "id" in hook:
                    found[hook["id"]] = hook

    mismatches: list[str] = []
    for spec in specs:
        expected = _hook_entry(spec, hooks_relpath)
        actual = found.get(spec.id)
        if actual is None:
            mismatches.append(f"managed hook '{spec.id}' missing")
        elif any(actual.get(key) != value for key, value in expected.items()):
            mismatches.append(f"managed hook '{spec.id}' out of sync")
    return mismatches


def retired_hooks_present(config: dict, known_ids: set[str], hooks_relpath: str) -> list[str]:

    reasons: list[str] = []
    for repo in config.get("repos") or []:
        if isinstance(repo, dict) and repo.get("repo") == "local":
            reasons.extend(
                f"managed hook '{hook.get('id')}' is no longer in the catalog"
                for hook in repo.get("hooks") or []
                if _is_managed(hook, set(), hooks_relpath) and hook.get("id") not in known_ids
            )
    return reasons


def excluded_hooks_present(config: dict, excluded_ids: set[str]) -> list[str]:
    present: list[str] = []
    for repo in config.get("repos") or []:
        if isinstance(repo, dict) and repo.get("repo") == "local":
            present.extend(
                f"managed hook '{hook['id']}' excluded by technology selection"
                for hook in repo.get("hooks") or []
                if isinstance(hook, dict) and hook.get("id") in excluded_ids
            )
    return present
