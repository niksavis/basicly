from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    script_path = (
        Path(__file__).resolve().parents[2]
        / ".basicly"
        / "core"
        / "hooks"
        / "internal-info-scan.py"
    )
    spec = importlib.util.spec_from_file_location("internal_info_scan_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(root: Path, body: str) -> None:
    (root / "basicly.local.toml").write_text(body, encoding="utf-8")


def test_script_carries_no_denylist_of_its_own() -> None:

    module = _load_module()
    assert module.load_rules(Path("/nonexistent-repo-root")) == []


def test_unconfigured_repo_is_inert(tmp_path: Path) -> None:
    module = _load_module()
    assert module.load_rules(tmp_path) == []


def test_loads_named_rules(tmp_path: Path) -> None:
    module = _load_module()
    _write_config(
        tmp_path,
        '[[privacy.denied]]\nname = "corp-domain"\ntoken = "Internal.Example"\n'
        '[[privacy.denied]]\nname = "machine-user"\ntoken = "example-user"\n',
    )
    assert module.load_rules(tmp_path) == [
        ("corp-domain", "internal.example"),
        ("machine-user", "example-user"),
    ]


def test_invalid_toml_yields_no_rules(tmp_path: Path) -> None:
    module = _load_module()
    _write_config(tmp_path, "[[privacy.denied]\nname =")
    assert module.load_rules(tmp_path) == []


def test_malformed_entries_are_skipped(tmp_path: Path) -> None:
    module = _load_module()
    _write_config(
        tmp_path,
        '[[privacy.denied]]\nname = "no-token"\n'
        '[[privacy.denied]]\ntoken = "no-name"\n'
        '[[privacy.denied]]\nname = "good"\ntoken = "tok"\n',
    )
    assert module.load_rules(tmp_path) == [("good", "tok")]


def test_privacy_table_of_wrong_shape_yields_no_rules(tmp_path: Path) -> None:
    module = _load_module()
    _write_config(tmp_path, '[privacy]\ndenied = "corp-domain"\n')
    assert module.load_rules(tmp_path) == []


_RULES = [("corp-domain", "internal.example"), ("machine-user", "example-user")]


def test_flags_a_denied_token_and_reports_the_rule_name() -> None:
    module = _load_module()
    assert module.rule_hit("contact dev@internal.example for access", _RULES) == "corp-domain"


def test_match_is_case_insensitive() -> None:
    module = _load_module()
    assert module.rule_hit("Host: INTERNAL.EXAMPLE", _RULES) == "corp-domain"


def test_clean_line_is_not_flagged() -> None:
    module = _load_module()
    assert module.rule_hit("assert sanitize_label('github.com') == 'github-com'", _RULES) is None


def test_first_matching_rule_wins() -> None:
    module = _load_module()
    assert module.rule_hit("example-user@internal.example", _RULES) == "corp-domain"


def test_pragma_silences_a_reviewed_false_positive() -> None:
    module = _load_module()
    line = f"see https://example.org/blog/internal.example/  # {_load_module().ALLOW_PRAGMA}"
    assert module.rule_hit(line, _RULES) is None


def test_no_rules_means_no_hit() -> None:
    module = _load_module()
    assert module.rule_hit("example-user@internal.example", []) is None
