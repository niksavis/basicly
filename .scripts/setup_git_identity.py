r"""Scaffold per-remote git identities via conditional includes.

Many developers commit to more than one forge with different identities (a
personal GitHub account, a company Bitbucket/GitLab). This helper wires git's
``includeIf "hasconfig:remote.*.url:..."`` mechanism so the right name/email is
selected automatically by a repo's remote URL — without ever setting a global
``user.email`` (leaving it unset keeps an unconfigured repo failing loudly rather
than committing a hostname address).

It carries NO identities of its own; you pass them in. Pair it with the
``identity-guard`` pre-commit hook, which blocks commits made with a missing or
auto-generated identity.

Examples:
    python setup_git_identity.py add --host github.com \
        --name "Ada Lovelace" --email ada@personal.example
    python setup_git_identity.py add --host git.acme.example \
        --name "Lovelace,Ada" --email ada@acme.example --label acme
    python setup_git_identity.py list

Each ``add`` writes an include file ``~/.gitconfig-<label>`` and adds one
``includeIf`` entry to the global config. Re-running the same ``add`` is
idempotent.
"""

from __future__ import annotations

import argparse
import re
import subprocess  # nosec B404
import sys
from pathlib import Path


def sanitize_label(host: str) -> str:
    """Turn a host into a filesystem-safe include-file label (e.g. github-com)."""
    label = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    if not label:
        raise ValueError(f"cannot derive a label from host {host!r}")
    return label


def host_url_glob(host: str) -> str:
    """Return the https URL glob used to match a host's remotes."""
    return f"https://{host}/**"


def includeif_condition(url_glob: str) -> str:
    """Return the includeIf condition string for a remote-URL glob."""
    return f"hasconfig:remote.*.url:{url_glob}"


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)  # nosec


def add_identity(host: str, name: str, email: str, label: str | None = None) -> str:
    """Create the include file and register a conditional include. Return the label."""
    label = label or sanitize_label(host)
    include_file = Path.home() / f".gitconfig-{label}"
    include_ref = f"~/.gitconfig-{label}"  # stored portably; git expands the tilde

    _run(["git", "config", "--file", str(include_file), "user.name", name])
    _run(["git", "config", "--file", str(include_file), "user.email", email])

    condition = includeif_condition(host_url_glob(host))
    key = f"includeIf.{condition}.path"
    existing = _run(["git", "config", "--global", "--get-all", key]).stdout.split("\n")
    if include_ref not in [line.strip() for line in existing]:
        _run(["git", "config", "--global", "--add", key, include_ref])

    return label


def list_identities() -> str:
    """Return the configured includeIf path entries from the global config."""
    result = _run(["git", "config", "--global", "--get-regexp", r"^includeif\..*\.path$"])
    return result.stdout.strip()


def main(argv: list[str] | None = None) -> int:
    """Entry point for the CLI."""
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="register a per-host identity")
    add.add_argument("--host", required=True, help="remote host, e.g. github.com")
    add.add_argument("--name", required=True, help="git user.name for this host")
    add.add_argument("--email", required=True, help="git user.email for this host")
    add.add_argument("--label", help="include-file label (default: derived from host)")

    sub.add_parser("list", help="show configured conditional includes")

    args = parser.parse_args(argv)

    if args.command == "add":
        label = add_identity(args.host, args.name, args.email, args.label)
        print(f"Configured {args.name} <{args.email}> for {args.host} (~/.gitconfig-{label}).")
        return 0

    print(list_identities() or "No conditional includes configured.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
