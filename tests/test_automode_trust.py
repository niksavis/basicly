from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import automode_trust
from tests.test_cli import run_basicly

if TYPE_CHECKING:
    import pytest

SINGLE_REPO = "Trusted repo: github.com/acme/widget (the repository this setup ran in)"


def _settings(home: Path, environment: list[str] | None = None, *, raw: str = "") -> Path:
    path = home / ".claude" / "settings.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    document = (
        {"model": "opus"} if environment is None else {"autoMode": {"environment": environment}}
    )
    path.write_text(raw or json.dumps(document, indent=2), encoding="utf-8")
    return path


def test_a_list_that_trusts_one_named_repository_is_reported_with_the_fix(tmp_path: Path) -> None:
    path = _settings(tmp_path, ["Source control: the working repository", SINGLE_REPO])

    found = automode_trust.single_repository_findings(tmp_path)

    assert len(found) == 1
    assert str(path) in found[0]
    assert f'its entry "{SINGLE_REPO}" names only acme/widget' in found[0]
    assert "reads every other repository as external" in found[0]
    assert 'add "$defaults"' in found[0]
    assert '"Source control: github.com/acme and all repos under it"' in found[0]
    assert "/auto-mode-setup again" in found[0]


def test_a_bare_owner_and_name_entry_is_reported_with_that_owner(tmp_path: Path) -> None:
    _settings(tmp_path, ["**Source control**: acme/app and its configured remotes"])

    found = automode_trust.single_repository_findings(tmp_path)

    assert len(found) == 1
    assert '"Source control: acme and all repos under it"' in found[0]


def test_a_repository_named_twice_is_named_once_with_the_host_it_was_given(
    tmp_path: Path,
) -> None:
    _settings(tmp_path, ["**Trusted repo**: acme/widget (public, github.com/acme/widget)"])

    found = automode_trust.single_repository_findings(tmp_path)

    assert "names only acme/widget. " in found[0]
    assert '"Source control: github.com/acme and all repos under it"' in found[0]


def test_a_list_with_defaults_and_an_account_wide_entry_is_clean(tmp_path: Path) -> None:
    _settings(tmp_path, ["$defaults", "Source control: github.com/acme and all repos under it"])

    assert automode_trust.single_repository_findings(tmp_path) == []


def test_an_account_wide_entry_without_defaults_is_clean(tmp_path: Path) -> None:
    _settings(tmp_path, ["Source control: github.com/acme and all repos under it"])

    assert automode_trust.single_repository_findings(tmp_path) == []


def test_a_named_repository_outside_a_trust_entry_is_clean(tmp_path: Path) -> None:
    _settings(tmp_path, ["Trusted domains: docs.python.org", "Cloud buckets: s3 acme/app-logs"])

    assert automode_trust.single_repository_findings(tmp_path) == []


def test_no_settings_file_is_clean(tmp_path: Path) -> None:
    assert automode_trust.single_repository_findings(tmp_path) == []


def test_settings_without_auto_mode_are_clean(tmp_path: Path) -> None:
    _settings(tmp_path)

    assert automode_trust.single_repository_findings(tmp_path) == []


def test_unreadable_json_is_reported_by_name(tmp_path: Path) -> None:
    path = _settings(tmp_path, raw='{"autoMode": {"environment": [')

    found = automode_trust.single_repository_findings(tmp_path)

    assert len(found) == 1
    assert f"cannot read {path}" in found[0]


def test_the_check_never_writes_the_settings_file(tmp_path: Path) -> None:
    path = _settings(tmp_path, [SINGLE_REPO])
    past = 1_000_000_000
    os.utime(path, ns=(past, past))
    before = path.read_bytes()

    assert automode_trust.single_repository_findings(tmp_path)

    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == past
    assert sorted(p.name for p in tmp_path.rglob("*")) == [".claude", "settings.json"]


def test_permissions_check_warns_on_a_single_repository_list(
    work_repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    _settings(home, [SINGLE_REPO])
    monkeypatch.setenv("HOME", str(home))

    result = run_basicly(work_repo, "permissions-check")

    assert result.returncode == 0, result.stderr
    assert "auto mode trusts one repository" in result.stdout + result.stderr
    assert "Projected permissions deny-list is up to date." in result.stdout
