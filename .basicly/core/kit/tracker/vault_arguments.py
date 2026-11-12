from __future__ import annotations

import argparse
from collections.abc import Callable


def _form(verb: str, text: str, command: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=f"cli.py {verb}", description=text)
    parser.add_argument("verb", choices=[verb])
    parser.add_argument("--vault", required=True, help="the vault URL or local repository")
    parser.add_argument("--state", help="the machine state folder (default: per user)")
    parser.set_defaults(command=command)
    return parser


def comment() -> argparse.ArgumentParser:
    parser = _form("comment", "queue a vault comment", "comment-vault")
    parser.add_argument("record", help="the record id")
    parser.add_argument("--text", required=True, help="the comment, redacted before it is queued")
    return parser


def sync() -> argparse.ArgumentParser:
    parser = _form("sync", "share a vault's queue", "sync-vault")
    parser.add_argument("--urgent", action="store_true", help="retry until --deadline passes")
    parser.add_argument("--deadline", type=float, default=10.0, help="seconds for an urgent share")
    return parser


def fsck() -> argparse.ArgumentParser:
    parser = _form("fsck", "report or repair a vault clone", "fsck-vault")
    parser.add_argument("--repair", action="store_true", help="re-queue lines a rewrite dropped")
    parser.add_argument("--accept-rewrite", metavar="HEAD", help="accept the fetched head")
    parser.add_argument("--purge", action="store_true", help="confirm dropping retracted refs")
    return parser


def reclone() -> argparse.ArgumentParser:
    return _form("reclone", "clone a vault again, keeping its refs", "reclone-vault")


def hook() -> argparse.ArgumentParser:
    parser = _form("hook", "fetch or share from an agent hook", "hook-vault")
    parser.add_argument("event", choices=["session-start", "prompt", "stop", "session-end"])
    return parser


def status() -> argparse.ArgumentParser:
    return _form("status", "show the data age without a network call", "status-vault")


FORMS: dict[str, Callable[[], argparse.ArgumentParser]] = {
    "comment": comment,
    "sync": sync,
    "fsck": fsck,
    "reclone": reclone,
    "hook": hook,
    "status": status,
}
