from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _load(file_name: str, module_name: str) -> Any:

    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _HERE / file_name)
    if spec is None or spec.loader is None:
        raise ImportError(f"the tracker kit's {file_name} is missing from beside vault_cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


vault_shapes = _load("vault_shapes.py", "basicly_tracker_kit_vault_shapes")
vault_fold = _load("vault_fold.py", "basicly_tracker_kit_vault_fold")
vault_clone = _load("vault_clone.py", "basicly_tracker_kit_vault_clone")
vault_queue = _load("vault_queue.py", "basicly_tracker_kit_vault_queue")
vault_share = _load("vault_share.py", "basicly_tracker_kit_vault_share")
vault_repair = _load("vault_repair.py", "basicly_tracker_kit_vault_repair")
vault_hooks = _load("vault_hooks.py", "basicly_tracker_kit_vault_hooks")
vault_claim = _load("vault_claim.py", "basicly_tracker_kit_vault_claim")
vault_commit_hook = _load("vault_commit_hook.py", "basicly_tracker_kit_vault_commit_hook")
vault_config = _load("vault_config.py", "basicly_tracker_kit_vault_config")
vault_init = _load("vault_init.py", "basicly_tracker_kit_vault_init")
vault_view = _load("vault_view.py", "basicly_tracker_kit_vault_view")
vault_write = _load("vault_write.py", "basicly_tracker_kit_vault_write")

READS = frozenset({"list", "ready", "show"})


def _vault_show(args: argparse.Namespace) -> dict[str, object]:
    found = vault_shapes.valid_events(vault_shapes.read(args.vault, args.ref))
    return vault_fold.view(vault_fold.fold(found).record(args.record))


def _state_root() -> Path:
    return vault_clone.state_root(os.environ, Path.home(), windows=os.name == "nt")


def _state(args: argparse.Namespace) -> Path:
    return Path(args.state or _state_root())


Clock = Callable[[], float]


def _fetch_vault(args: argparse.Namespace, clock: Clock) -> dict[str, object]:
    state = _state(args)
    clone = vault_clone.locate(state, args.vault)
    fetched = vault_clone.fetch(clone, clock=clock, timeout=args.timeout)
    return {**fetched, "shared": vault_hooks.due_share(clone, state, clock)}


def _queue_comment(args: argparse.Namespace, clock: Clock) -> dict[str, object]:
    state = _state(args)
    clone = vault_clone.locate(state, args.vault)
    now = any(os.environ.get(name) for name in ("CI", "BASICLY_TRACKER_EPHEMERAL"))
    origin = vault_queue.Origin(os.environ.get("BASICLY_TRACKER_SESSION", ""), now, clock)
    queued = vault_queue.write(
        clone, [vault_queue.comment(args.record, args.text)], state=state, origin=origin
    )
    return {**queued, "shared": vault_hooks.due_share(clone, state, clock, now=now)}


def _clone(args: argparse.Namespace) -> Any:
    return vault_clone.locate(_state(args), args.vault)


def _asked(args: argparse.Namespace) -> str:
    if args.vault:
        return str(args.vault)
    if not sys.stdin.isatty():
        return ""
    print(
        "Git address of an empty repository for the vault "
        f"(suggested name: {vault_init.SUGGESTED_NAME}): ",
        end="",
        file=sys.stderr,
    )
    return input().strip()


def _repository_root(start: Path) -> Path | None:
    return next((p for p in (start, *start.parents) if (p / ".git").exists()), None)


def _init_vault(args: argparse.Namespace, _clock: Clock) -> dict[str, object]:
    url = _asked(args)
    if not url:
        return {
            "exit": vault_init.FIX,
            "fix": "name the empty repository: vault-init --vault GIT-ADDRESS",
            "suggested_name": vault_init.SUGGESTED_NAME,
        }
    shown = vault_init.init(url, confirmed=args.yes)
    root = _repository_root(Path.cwd())
    if shown.get("pushed") and args.prefix and root is not None:
        target = root / vault_config.CONFIG_PATH
        target.write_text(vault_config.text(url, args.prefix), encoding="utf-8")
        shown["config"] = str(vault_config.CONFIG_PATH)
    return shown


def _claim_vault(args: argparse.Namespace, clock: Clock) -> dict[str, object]:
    ask = vault_claim.Ask(
        _clone(args),
        _state(args),
        args.record,
        args.session or os.environ.get("BASICLY_TRACKER_SESSION", ""),
        clock,
    )
    shown = vault_claim.claim(ask, deadline=args.deadline)
    if shown.get("exit") == vault_claim.UNREACHABLE and vault_init.no_genesis(args.vault):
        return {
            "exit": vault_init.NO_GENESIS,
            "record": args.record,
            "unsettled": "the vault has no genesis commit",
            "next": f"vault-init --vault {args.vault}",
        }
    return shown


def _unassign_vault(args: argparse.Namespace, clock: Clock) -> dict[str, object]:
    session = args.session or os.environ.get(vault_write.SESSION, "")
    now = any(os.environ.get(name) for name in vault_write.IMMEDIATE)
    target = vault_write.Target(_clone(args), _state(args), session, now, clock)
    return vault_write.release(target, args.record, take=args.take)


def configured(start: Path, asked: str) -> Any:
    config = vault_config.load(Path(start).resolve())
    if config is None and asked:
        raise vault_config.ConfigError(
            f"{asked} reads the vault that {vault_config.CONFIG_PATH} names, and none names "
            f"one here; run it without {asked}"
        )
    return config


def presented(ledger: Path, clock: Clock, *, all_projects: bool, asked: str) -> Any:
    config = configured(ledger, asked)
    if config is None:
        return None
    clone = vault_clone.locate(_state_root(), config.vault)
    template = Path(ledger) if Path(ledger).is_dir() else None
    want = vault_view.Want(config.prefix, all_projects, template)
    return vault_view.present(clone, want, clock=clock), clone


def view_of(args: argparse.Namespace, clock: Clock) -> Path:
    every = bool(getattr(args, "all_projects", False))
    asked = "--all-projects" if every else ""
    reached = presented(args.directory, clock, all_projects=every, asked=asked)
    return args.directory if reached is None else reached[0]


def shown(ledger: Path, records: list, clock: Clock) -> list:
    folder, clone = presented(ledger, clock, all_projects=False, asked="--json")
    return vault_view.show(folder, clone, records)


HANDLERS: dict[str, Callable[[argparse.Namespace, Clock], dict[str, object]]] = {
    "init-vault": _init_vault,
    "vault-check": lambda a, _c: vault_shapes.report(a.vault, a.ref),
    "vault-show": lambda a, _c: _vault_show(a),
    "fetch": _fetch_vault,
    "comment-vault": _queue_comment,
    "hook-vault": lambda a, c: vault_hooks.hook(a.event, _clone(a), _state(a), clock=c),
    "status-vault": lambda a, c: vault_hooks.status(_clone(a), clock=c),
    "fsck-vault": lambda a, c: vault_repair.check(_clone(a), _state(a), vars(a), c),
    "reclone-vault": lambda a, c: vault_repair.reclone(_clone(a), clock=c),
    "claim-vault": _claim_vault,
    "unassign-vault": _unassign_vault,
    "sync-vault": lambda a, c: vault_share.share(
        _clone(a), _state(a), plan=vault_share.Plan(c, urgent=a.urgent, deadline_s=a.deadline)
    ),
    "commit-msg-vault": lambda a, c: vault_commit_hook.check(
        vault_commit_hook.Commit(
            _clone(a),
            _state(a),
            Path(a.message),
            a.session or os.environ.get("BASICLY_TRACKER_SESSION", ""),
            c,
        )
    ),
}
