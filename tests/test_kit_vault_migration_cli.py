from __future__ import annotations

import json
import os
import subprocess  # nosec B404
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_canonical import PRIVATE, _source, events
from tests.test_kit_vault_migrate import _head, vault_migrate
from tests.test_kit_vault_share import bare
from tests.test_kit_vault_view import cli

__all__ = ["bare"]

pytestmark = pytest.mark.usefixtures("migration_identity")


@pytest.fixture(autouse=True)
def _isolated_working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    code = cli.main(["migrate", *args])
    return code, json.loads(capsys.readouterr().out)


def test_preview_reports_canonical_counts_without_fetching_or_changing_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)
    before = {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()}
    state = tmp_path / "state"

    def refuse_fetch(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("preview fetched a vault")

    monkeypatch.setattr(vault_migrate.vault_clone, "fetch", refuse_fetch)
    code, report = _run(
        capsys,
        str(source),
        "--to-vault",
        "--preview",
        "--state",
        str(state),
        "--redact-name",
        PRIVATE,
    )
    assert code == 0
    assert report["preview"] is True
    assert (report["events"], report["canonical_events"], report["records"]) == (4, 4, 1)
    assert report["reminted"] == 1
    assert PRIVATE not in json.dumps(report)
    assert len(report["digest"]) == 64
    assert not state.exists()
    assert {path.name: path.read_bytes() for path in source.iterdir() if path.is_file()} == before


def test_preview_uses_the_same_canonical_digest_as_an_import(
    tmp_path: Path,
    bare: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)
    options = [str(source), "--to-vault", "--redact-name", PRIVATE]
    _, preview = _run(capsys, *options, "--preview")
    code, imported = _run(
        capsys, *options, "--vault", str(bare), "--state", str(tmp_path / "state"), "--no-map-kept"
    )
    assert code == 0, imported
    assert imported["mapping"] == "not-kept"
    assert imported["digest"] == preview["digest"]


def test_preview_defaults_to_the_current_ledger_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "standalone-ledger"
    _source(source)
    monkeypatch.chdir(source)
    code, report = _run(capsys, "--to-vault", "--preview")
    assert code == 0, report
    assert (report["records"], report["events"]) == (1, 4)


def test_import_saves_only_ids_and_field_names_in_an_explicit_private_map(
    tmp_path: Path,
    bare: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    found = _source(source)
    mapping = tmp_path / "private-map.json"
    state = tmp_path / "state"
    options = [
        str(source),
        "--to-vault",
        "--vault",
        str(bare),
        "--state",
        str(state),
        "--redact-name",
        PRIVATE,
        "--private-map",
        str(mapping),
    ]
    code, report = _run(capsys, *options)
    assert code == 0, report
    rows = json.loads(mapping.read_text())["events"]
    assert len(rows) == len(found)
    assert {row["old"] for row in rows} == {event.id for event in found}
    assert all(set(row) == {"old", "new", "fields"} for row in rows)
    assert PRIVATE not in mapping.read_text()
    clone = vault_migrate.vault_clone.locate(state, str(bare))
    valid = vault_migrate.vault_clone.valid_events(clone)
    landed = vault_migrate.vault_view.carried(valid, vault_migrate.vault_view.Want("demo"))
    assert {row["new"] for row in rows} == {event.id for event in landed}
    assert landed[0].payload["assignee"] == "contributor"
    assert found[0].id not in json.dumps(valid)
    assert found[0].id not in json.dumps(report)
    before = _head(bare)
    content = mapping.read_bytes()
    code, again = _run(capsys, *options, "--overwrite-private-map")
    assert code == 0
    assert again["already_migrated"] is True
    assert _head(bare) == before
    assert mapping.read_bytes() == content
    assert events.read_events(source)[0] == found


def test_import_requires_a_mapping_choice_before_it_fetches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)

    def refuse_fetch(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("an invalid invocation fetched a vault")

    monkeypatch.setattr(vault_migrate.vault_clone, "fetch", refuse_fetch)
    code, report = _run(capsys, str(source), "--to-vault")
    assert code == 1
    assert "--private-map FILE or --no-map-kept" in report["refused"]


def test_unknown_source_format_refuses_by_name(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as refused:
        cli.main(["migrate", "--to-vault", "--preview", "--from", "unknown-format"])
    assert refused.value.code == 2
    assert "unknown-format" in capsys.readouterr().err


def test_preview_accepts_br_as_its_own_format_key(
    capsys: pytest.CaptureFixture[str],
) -> None:
    fixture = Path(__file__).parent / "fixtures" / "vault-migrate" / "br.jsonl"
    code, report = _run(capsys, "--to-vault", "--preview", "--from", "br", "--source", str(fixture))
    assert code == 0, report
    assert report["records"] == 2
    assert report["canonical_events"] == report["events"]


def test_preview_redacts_the_git_identity_by_default_and_scans_to_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    monkeypatch.setattr(migration_cli, "identity", lambda _cwd: {"name": PRIVATE})

    code, report = _run(capsys, str(source), "--to-vault", "--preview")

    assert code == 0
    assert PRIVATE not in json.dumps(report)
    assert report["personal_data"] == {"name": 0, "other email": 0}


def test_the_identity_reads_git_config_and_never_a_blank_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(tmp_path / "repo")], check=True, env=clean)
    for key, value in (("user.name", "Ada Lovelace"), ("user.email", "ada@example.org")):
        repo = str(tmp_path / "repo")
        subprocess.run(["git", "-C", repo, "config", key, value], check=True, env=clean)
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    monkeypatch.setattr(migration_cli, "_login", lambda: "")

    found = migration_cli.identity(tmp_path / "repo")

    assert found["name and email"] == "Ada Lovelace <ada@example.org>"
    assert found["email"] == "ada@example.org"
    assert "login" not in found


def test_the_scan_respects_word_bounds() -> None:
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    scan = migration_cli.scanner({"login": "ada"})

    assert scan([{"text": "github.com/adavis and ada"}])["login"] == 1


def test_the_login_is_redacted_but_the_public_handle_in_a_github_url_stays() -> None:
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    args = migration_cli.argparse.Namespace(redact_name=[])
    rule = migration_cli._redactor(args, None, {"login": "ada"})
    scan = migration_cli.scanner({"login": "ada"})
    text = "ada wrote https://github.com/ada/repo and git@github.com:ada/repo.git"

    assert rule(text) == (
        "contributor wrote https://github.com/ada/repo and git@github.com:ada/repo.git"
    )
    assert scan([{"text": rule(text)}])["login"] == 0


@pytest.mark.parametrize("path", ["/home/ada/src/app.py", "C:\\Users\\ada\\src\\app.py"])
def test_a_home_path_is_redacted_by_the_default_path_rules(path: str) -> None:
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    redact = vault_migrate.sys.modules.get("basicly_tracker_kit_redact") or migration_cli._load(
        "redact"
    )
    args = migration_cli.argparse.Namespace(redact_name=[])
    rule = migration_cli._redactor(args, redact.text, {"login": "ada"})

    assert "ada" not in rule(f"seen in {path}")


@pytest.mark.parametrize(
    "path",
    ["HOME=home/hopper/src", "\\home\\hopper\\src", "/home/hopper/src", "/Users/hopper/src"],
)
def test_a_home_folder_unlike_the_login_never_reaches_the_vault(path: str) -> None:
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    redact = migration_cli._load("redact")
    args = migration_cli.argparse.Namespace(redact_name=[])
    rule = migration_cli._redactor(args, redact.text, {"login": "ada"})
    scan = migration_cli.scanner({"login": "ada", "home": "/home/hopper"})

    cleaned = rule(f"seen in {path}")

    assert "hopper" not in cleaned
    assert scan([{"text": cleaned}])["home"] == 0
    assert scan([{"text": f"seen in {path}"}])["home"] >= 1


def test_stacked_path_markers_collapse_to_one_home_marker() -> None:
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    args = migration_cli.argparse.Namespace(redact_name=[])
    stacked = "<redacted:home-path><redacted:posix-absolute-path><redacted:home-path>/work"

    def inherited(_value: str) -> str:
        return stacked

    rule = migration_cli._redactor(args, inherited, {"login": "ada"})

    assert rule("any") == "<redacted:home-path>/work"


def test_migrate_refuses_by_name_when_the_git_identity_does_not_resolve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "source"
    _source(source)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-such-config"))

    code, report = _run(capsys, str(source), "--to-vault", "--preview")

    assert code == 1
    assert "user.name or user.email does not resolve" in report["refused"]
    assert "git config user.name" in report["refused"]


def test_the_identity_is_read_from_the_source_repository_not_the_working_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "project" / "ledger"
    _source(source)
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    seen: list[Path] = []
    real = migration_cli.identity

    def record(folder: Path) -> dict[str, str]:
        seen.append(folder)
        return real(folder)

    monkeypatch.setattr(migration_cli, "identity", record)

    code, _report = _run(capsys, str(source), "--to-vault", "--preview")

    assert code == 0
    assert seen == [source.resolve()]


def test_a_short_login_or_host_is_not_redacted_as_a_common_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    monkeypatch.setattr(migration_cli, "_login", lambda: "dev")
    monkeypatch.setattr(migration_cli.platform, "node", lambda: "box")

    found = migration_cli.identity(tmp_path)

    assert "login" not in found
    assert "host" not in found
    assert found["name"] == "Migration Tester"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "mail <redacted:machine-username>.surname@employer.com today",
            "mail <redacted:email> today",
        ),
        ("mail first.last@employer.co.uk today", "mail <redacted:email> today"),
        ("pinned basicly@v0.46 and claude@2.1.329", "pinned basicly@v0.46 and claude@2.1.329"),
        ("tester@example.invalid and git@github.com", "tester@example.invalid and git@github.com"),
        ("123+ada@users.noreply.github.com", "123+ada@users.noreply.github.com"),
        ("@pytest.mark.parametrize", "@pytest.mark.parametrize"),
    ],
)
def test_an_email_outside_the_public_domains_is_redacted_and_counted(
    text: str, expected: str
) -> None:
    migration_cli = vault_migrate.sys.modules["basicly_tracker_kit_vault_migration_cli"]
    args = migration_cli.argparse.Namespace(redact_name=[])
    rule = migration_cli._redactor(args, None, {"login": "nobody-here"})
    scan = migration_cli.scanner({"login": "nobody-here"})

    assert rule(text) == expected
    assert scan([{"text": rule(text)}])["other email"] == 0
    leaked = 0 if text == expected else 1
    assert scan([{"text": text}])["other email"] == leaked
