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


def _vault_show(args: argparse.Namespace) -> dict[str, object]:
    found = vault_shapes.valid_events(vault_shapes.read(args.vault, args.ref))
    return vault_fold.view(vault_fold.fold(found).record(args.record))


def _state(args: argparse.Namespace) -> Path:
    return Path(
        args.state or vault_clone.state_root(os.environ, Path.home(), windows=os.name == "nt")
    )


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


HANDLERS: dict[str, Callable[[argparse.Namespace, Clock], dict[str, object]]] = {
    "vault-check": lambda a, _c: vault_shapes.report(a.vault, a.ref),
    "vault-show": lambda a, _c: _vault_show(a),
    "fetch": _fetch_vault,
    "comment-vault": _queue_comment,
    "hook-vault": lambda a, c: vault_hooks.hook(a.event, _clone(a), _state(a), clock=c),
    "status-vault": lambda a, c: vault_hooks.status(_clone(a), clock=c),
    "fsck-vault": lambda a, c: vault_repair.check(_clone(a), _state(a), vars(a), c),
    "reclone-vault": lambda a, c: vault_repair.reclone(_clone(a), clock=c),
    "claim-vault": lambda a, c: vault_claim.claim(
        vault_claim.Ask(
            _clone(a),
            _state(a),
            a.record,
            a.session or os.environ.get("BASICLY_TRACKER_SESSION", ""),
            c,
        ),
        deadline=a.deadline,
    ),
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
