from __future__ import annotations

import json
from pathlib import Path

from basicly import capability_proof, usage
from basicly.capability_proof import CAPABILITY_VERIFY_CHECK

REPO_ROOT = Path(__file__).resolve().parents[1]


def _plant(root: Path, relative: Path, counts: dict[str, int]) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            key: {"count": count, "last_used": "2026-07-26"} for key, count in counts.items()
        }),
        encoding="utf-8",
    )


def _declare_check(root: Path, name: str, command: list[str]) -> None:
    rendered = ", ".join(f'"{arg}"' for arg in command)
    (root / "basicly.toml").write_text(
        f'[[verify.checks]]\nname = "{name}"\ncommand = [{rendered}]\nmodes = ["full"]\n',
        encoding="utf-8",
    )


def test_recorded_executions_unions_both_ledgers(tmp_path: Path) -> None:

    _plant(tmp_path, usage.USAGE_FILE, {"pytest": 785, "never-run-tool": 0})
    _plant(tmp_path, usage.VERIFY_CHECKS_FILE, {"pytest": 2})

    counts = capability_proof.recorded_executions(tmp_path)

    assert counts is not None
    assert counts["pytest"] == 785
    assert counts[f"{usage.VERIFY_CHECK_PREFIX}pytest"] == 2
    assert "never-run-tool" not in counts


def test_recorded_executions_is_none_when_no_ledger_exists(tmp_path: Path) -> None:
    assert capability_proof.recorded_executions(tmp_path) is None


def test_a_repo_that_declares_no_capability_has_nothing_to_prove(tmp_path: Path) -> None:
    assert capability_proof.shipped_capabilities(tmp_path) == ()
    assert capability_proof.unexercised_capabilities(tmp_path) == ()


def test_a_declared_capability_with_no_ledger_at_all_is_unproven(tmp_path: Path) -> None:

    _declare_check(tmp_path, "planted", ["ruff"])

    reasons = capability_proof.unexercised_capabilities(tmp_path)

    assert len(reasons) == 1
    assert "no execution ledger" in reasons[0]
    assert usage.VERIFY_CHECKS_FILE.as_posix() in reasons[0]


def test_a_counter_at_zero_is_refused_exactly_as_an_absent_key_is(tmp_path: Path) -> None:

    rendered = '[[verify.checks]]\nname = "{0}"\ncommand = ["x"]\nmodes = ["full"]\n'
    (tmp_path / "basicly.toml").write_text(
        rendered.format("planted") + rendered.format("exercised"), encoding="utf-8"
    )
    _plant(
        tmp_path,
        usage.VERIFY_CHECKS_FILE,
        {"planted": 0, "exercised": 3},
    )

    reasons = capability_proof.unexercised_capabilities(tmp_path)

    assert len(reasons) == 1
    assert f"{CAPABILITY_VERIFY_CHECK} 'planted'" in reasons[0]


def test_the_witness_is_the_checks_name_not_the_binary_it_wraps(tmp_path: Path) -> None:

    _declare_check(tmp_path, "wired-or-deleted", ["uv", "run", "python", ".scripts/x.py"])
    _plant(tmp_path, usage.USAGE_FILE, {"uv": 6091, "python": 900})

    labels_to_witnesses = dict(capability_proof.shipped_capabilities(tmp_path))

    assert labels_to_witnesses == {
        f"{CAPABILITY_VERIFY_CHECK} 'wired-or-deleted'": (
            f"{usage.VERIFY_CHECK_PREFIX}wired-or-deleted"
        )
    }
    assert capability_proof.unexercised_capabilities(tmp_path) != ()


def test_two_checks_sharing_a_name_both_stay_in_the_inventory(tmp_path: Path) -> None:

    rendered = '[[verify.checks]]\nname = "same"\ncommand = ["x"]\nmodes = ["full"]\n'
    (tmp_path / "basicly.toml").write_text(rendered * 2, encoding="utf-8")

    assert len(capability_proof.shipped_capabilities(tmp_path)) == 2


def test_this_repos_own_capability_inventory_is_never_empty() -> None:

    capabilities = capability_proof.shipped_capabilities(REPO_ROOT)

    labels = {label for label, _ in capabilities}
    assert {
        f"{CAPABILITY_VERIFY_CHECK} 'pytest'",
        f"{CAPABILITY_VERIFY_CHECK} 'projection-permissions'",
    } <= labels
    assert all(witness for _, witness in capabilities)
