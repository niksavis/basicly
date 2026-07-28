from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    """Load the internal-info-scan hook module from its script path."""
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


# --- the denylist must never be in the shipped script ------------------------


def test_script_carries_no_denylist_of_its_own() -> None:
    """The shipped hook must hold no tokens: it is copied into consumer repos.

    The whole point of reading a gitignored config is that a gate hard-coding the
    strings it suppresses publishes them. Asserting the module exposes no literal
    token list is what keeps a later 'just add a default' from undoing that.
    """
    module = _load_module()
    assert module.load_rules(Path("/nonexistent-repo-root")) == []


def test_unconfigured_repo_is_inert(tmp_path: Path) -> None:
    """No config file at all yields no rules, so the gate cannot block anything."""
    module = _load_module()
    assert module.load_rules(tmp_path) == []


# --- config loading is tolerant, never a commit-time crash -------------------


def test_loads_named_rules(tmp_path: Path) -> None:
    """Each [[privacy.denied]] entry becomes a (name, lowercased token) rule."""
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
    """Unparseable config must not fail the commit — it disables the gate instead."""
    module = _load_module()
    _write_config(tmp_path, "[[privacy.denied]\nname =")
    assert module.load_rules(tmp_path) == []


def test_malformed_entries_are_skipped(tmp_path: Path) -> None:
    """An entry missing a name or token is dropped, the well-formed one survives."""
    module = _load_module()
    _write_config(
        tmp_path,
        '[[privacy.denied]]\nname = "no-token"\n'
        '[[privacy.denied]]\ntoken = "no-name"\n'
        '[[privacy.denied]]\nname = "good"\ntoken = "tok"\n',
    )
    assert module.load_rules(tmp_path) == [("good", "tok")]


def test_privacy_table_of_wrong_shape_yields_no_rules(tmp_path: Path) -> None:
    """A scalar where the array of tables belongs is ignored, not a traceback."""
    module = _load_module()
    _write_config(tmp_path, '[privacy]\ndenied = "corp-domain"\n')
    assert module.load_rules(tmp_path) == []


# --- matching -----------------------------------------------------------------

_RULES = [("corp-domain", "internal.example"), ("machine-user", "example-user")]


def test_flags_a_denied_token_and_reports_the_rule_name() -> None:
    """A hit returns the rule *name*; the token itself must never be echoed."""
    module = _load_module()
    assert module.rule_hit("contact dev@internal.example for access", _RULES) == "corp-domain"


def test_match_is_case_insensitive() -> None:
    """Tokens are proper nouns, so casing must not be a way past the gate."""
    module = _load_module()
    assert module.rule_hit("Host: INTERNAL.EXAMPLE", _RULES) == "corp-domain"


def test_clean_line_is_not_flagged() -> None:
    """Ordinary content passes."""
    module = _load_module()
    assert module.rule_hit("assert sanitize_label('github.com') == 'github-com'", _RULES) is None


def test_first_matching_rule_wins() -> None:
    """A line tripping two rules reports one, deterministically the first."""
    module = _load_module()
    assert module.rule_hit("example-user@internal.example", _RULES) == "corp-domain"


def test_pragma_silences_a_reviewed_false_positive() -> None:
    """A public URL containing a denied substring is the case that needs an escape."""
    module = _load_module()
    line = f"see https://example.org/blog/internal.example/  # {_load_module().ALLOW_PRAGMA}"
    assert module.rule_hit(line, _RULES) is None


def test_no_rules_means_no_hit() -> None:
    """With an empty rule set even matching text passes, so an unconfigured repo is free."""
    module = _load_module()
    assert module.rule_hit("example-user@internal.example", []) is None
