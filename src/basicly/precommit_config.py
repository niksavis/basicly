"""The consumer's ``.pre-commit-config.yaml``, as a document basicly co-owns.

One responsibility, and it is the document: render the single ``repo: local`` block
that carries the catalog's git hooks into whatever the consumer already has, and read
a parsed config back to say whether that block still matches the catalog. The
round-trip parser belongs here for the same reason — a plain
``safe_load``/``safe_dump`` cycle dropped a consumer's comments and hoisted their
hand-maintained hooks (basicly-wd7u), and that defect is a property of the file's
representation, not of the hooks written into it.

Split out of :mod:`basicly.hooks` when the module-size ratchet caught that module
growing. The boundary is *the document* against *the installation*: nothing here
reads or writes a file, runs pre-commit, or knows where a hook script lives on disk,
and :mod:`basicly.hooks` does all of those and calls in for text. :class:`ManagedHook`
is a structural protocol rather than an import of ``hooks.HookSpec``, which is why
the split leaves no import back into the module it came from.
"""

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
    """The fields a catalog hook contributes to a pre-commit entry.

    ``hooks.HookSpec`` satisfies it structurally, and nothing else here needs to know
    that type exists. Every member is a read-only property rather than a mutable
    attribute, for the reason ``plan_gate.PlannedFields`` states: a plain ``id: str``
    declares a writable slot that a frozen dataclass can never satisfy.
    """

    @property
    def id(self) -> str:
        """The hook id, which is what makes an entry basicly-managed."""
        ...

    @property
    def script(self) -> str:
        """The script filename, resolved against the consumer's core hooks dir."""
        ...

    @property
    def stage(self) -> str:
        """The git stage the hook runs at."""
        ...

    @property
    def pass_filenames(self) -> bool:
        """Whether pre-commit appends the matched filenames to the command."""
        ...

    @property
    def always_run(self) -> bool:
        """Whether the hook runs even when no file it matches changed."""
        ...


def _hook_entry(spec: ManagedHook, hooks_relpath: str) -> dict:
    # pre-commit shell-splits `entry`, so the script path must be quoted to
    # survive spaces or shell metacharacters in a configured core path.
    entry: dict = {
        "id": spec.id,
        "name": spec.id,
        "entry": f"uv run python {shlex.quote(f'{hooks_relpath}/{spec.script}')}",
        "language": "system",
        "stages": [spec.stage],
        "pass_filenames": spec.pass_filenames,
    }
    if spec.always_run:
        entry["always_run"] = True
    return entry


def _managed_local_block(specs: Sequence[ManagedHook], hooks_relpath: str) -> dict:
    """The single ``repo: local`` block that carries basicly's managed hooks."""
    return {"repo": "local", "hooks": [_hook_entry(spec, hooks_relpath) for spec in specs]}


def merge_precommit_config(
    existing: dict | None,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
    strip_ids: set[str] | None = None,
) -> dict:
    """Return a pre-commit config with basicly's managed hooks merged in.

    Managed hooks (matched by id) are stripped from every ``local`` repo and a
    single fresh managed block is appended, so re-running is idempotent and
    foreign repos/hooks are preserved untouched. ``strip_ids`` widens the strip
    set beyond the rendered specs so a hook a technology selection excludes is
    removed rather than stranded.
    """
    config = dict(existing) if isinstance(existing, dict) else {}
    managed_ids = strip_ids or {spec.id for spec in specs}

    kept: list = []
    for repo in config.get("repos") or []:
        if isinstance(repo, dict) and repo.get("repo") == "local":
            hooks = [
                hook
                for hook in (repo.get("hooks") or [])
                if not (isinstance(hook, dict) and hook.get("id") in managed_ids)
            ]
            if hooks:
                kept.append({**repo, "hooks": hooks})
            # A local repo left empty was fully basicly-managed; drop it.
        else:
            kept.append(repo)

    kept.append(_managed_local_block(specs, hooks_relpath))
    config["repos"] = kept
    return config


def _round_trip_yaml() -> YAML:
    """A ruamel round-trip parser that keeps comments, order, and quoting."""
    ryaml = YAML()
    ryaml.preserve_quotes = True
    # pre-commit entries can be long; never fold them across lines.
    ryaml.width = 4096
    return ryaml


def _replace_managed_block(
    config: dict,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
    strip_ids: set[str] | None,
) -> None:
    """Rebuild only basicly's managed block, mutating ``config`` in place.

    Strips basicly's managed hooks from every ``local`` repo and appends one
    fresh managed block, so a round-trip parser keeps every unmanaged repo/hook
    (and its comments) exactly where it was.
    """
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
                if isinstance(hook, dict) and hook.get("id") in managed_ids:
                    del hooks[hi]
        # A local repo left with no hooks was fully basicly-managed; drop it.
        if not hooks:
            del repos[ri]
    repos.append(_managed_local_block(specs, hooks_relpath))


def render_precommit_config(
    existing_text: str | None,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
    strip_ids: set[str] | None = None,
) -> str:
    """Render the merged pre-commit config to deterministic YAML text.

    A fresh file is rendered from scratch. When rewriting an existing file,
    only basicly's managed ``local`` block is rebuilt: every unmanaged repo and
    hook keeps its comments and position byte-for-byte (regression: a plain
    ``yaml.safe_load``/``safe_dump`` round-trip dropped comments and reordered
    hand-maintained hooks — basicly-wd7u).
    """
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
    """Parse a consumer's config text; *config_path* only names it in the error.

    Raises:
        ValueError: *existing_text* is not a mapping, so it is not a pre-commit config
            and rewriting it would destroy whatever it is.
    """
    parsed = yaml.safe_load(existing_text)
    if not isinstance(parsed, dict):
        raise ValueError(f"{config_path}: not a valid pre-commit config (expected a mapping)")
    return parsed


def managed_hook_mismatches(
    config: dict,
    specs: Sequence[ManagedHook],
    hooks_relpath: str,
) -> list[str]:
    """Compare the managed hooks in a parsed config semantically, not textually.

    A managed hook matches when every key/value basicly renders for it is present
    with an equal value — regardless of file formatting, comments, or how the
    consumer groups their ``local`` repos. Extra consumer-added keys are allowed.
    Returns a reason per missing/out-of-sync managed hook; empty means in sync.
    """
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


def excluded_hooks_present(config: dict, excluded_ids: set[str]) -> list[str]:
    """Return a reason per excluded managed hook still wired in the config."""
    present: list[str] = []
    for repo in config.get("repos") or []:
        if isinstance(repo, dict) and repo.get("repo") == "local":
            present.extend(
                f"managed hook '{hook['id']}' excluded by technology selection"
                for hook in repo.get("hooks") or []
                if isinstance(hook, dict) and hook.get("id") in excluded_ids
            )
    return present
