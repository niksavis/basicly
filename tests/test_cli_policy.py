"""Tests for the ``basicly policy checkpoint`` CLI wiring (basicly-shgo).

The command gates ``--approve`` on an interactive TTY or a one-time confirm
code. These tests fake the tracker and stdin so they assert only that wiring:
a non-interactive approve challenges (exit 1) and a matching code approves.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import cli, policy
from basicly.config import PolicyConfig


class _Proc:
    def __init__(self, stdout: str = "") -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = 0


class _FakeBr:
    """Stateful br stand-in whose comment writes are visible to later reads."""

    def __init__(self) -> None:
        self.comments: list[str] = []

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:2] == ["comments", "list"]:
            return _Proc(json.dumps([{"text": t} for t in self.comments]))
        if args[:2] == ["comments", "add"]:
            self.comments.append(args[-1])
            return _Proc("")
        if args[:1] == ["show"]:
            # An open, childless session root — enough for active_grant's
            # expiry check and the grant-approval session walk.
            return _Proc(json.dumps([{"status": "open", "dependents": []}]))
        raise AssertionError(f"unexpected br call: {args}")


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(policy, "_run_br", _FakeBr())


def _no_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)


def test_checkpoint_approve_non_interactive_challenges(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a TTY and without a code, approve refuses and prints a re-run line."""
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    rc = cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "--confirm cafe1234" in err


def test_checkpoint_approve_with_valid_code_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Re-running with the issued code records approval and exits 0."""
    _no_tty(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "cafe1234")
    assert cli.main(["policy", "checkpoint", "basicly-x", "ship", "--approve"]) == 1
    capsys.readouterr()
    rc = cli.main([
        "policy",
        "checkpoint",
        "basicly-x",
        "ship",
        "--approve",
        "--confirm",
        "cafe1234",
    ])
    assert rc == 0
    assert "APPROVED" in capsys.readouterr().out


# --- basicly policy grant (basicly-kjc5.3, design D3) --------------------------


def _tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)


def _allow_autonomy(monkeypatch: pytest.MonkeyPatch, level: str = "L3") -> None:
    config = PolicyConfig(required_gates=("verify",), max_rework=2, autonomy=level)
    monkeypatch.setattr(cli, "load_policy_config", lambda _r: config)


def test_grant_issue_interactive_then_show_and_revoke(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A TTY caller issues under the ceiling; show reports it; revoke clears it."""
    _tty(monkeypatch)
    _allow_autonomy(monkeypatch)
    assert cli.main(["policy", "grant", "root", "--level", "L2", "--token-budget", "5000"]) == 0
    assert "ISSUED L2" in capsys.readouterr().out

    assert cli.main(["policy", "grant", "root"]) == 0
    out = capsys.readouterr().out
    assert "grant: L2" in out and "token budget 5000" in out

    assert cli.main(["policy", "grant", "root", "--revoke"]) == 0
    capsys.readouterr()
    assert cli.main(["policy", "grant", "root"]) == 1
    assert "grant: NONE" in capsys.readouterr().out


def test_grant_issue_non_interactive_challenges(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An agent without a TTY cannot self-issue: it gets a relay code and exit 1."""
    _no_tty(monkeypatch)
    _allow_autonomy(monkeypatch)
    monkeypatch.setattr(policy, "_new_code", lambda: "feed5678")
    rc = cli.main(["policy", "grant", "root", "--level", "L1"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "CONFIRMATION REQUIRED" in err
    assert "--confirm feed5678" in err


def test_grant_issue_refused_at_default_ceiling(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """With the default [policy] autonomy = L0 every issuance is refused."""
    _tty(monkeypatch)
    rc = cli.main(["policy", "grant", "root", "--level", "L1"])
    assert rc == 1
    assert "autonomy ceiling" in capsys.readouterr().err
