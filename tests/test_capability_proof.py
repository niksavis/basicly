"""Exercised-or-unproven: the capability inventory and the ledgers that witness it.

Split out of `test_release.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14). The boundary is the same one the module itself draws: what this
repo *claims* and what this checkout has *recorded* are answered here, while whether a
release refuses over them stays in `test_release.py` — those tests drive
`release.run_release` and are about the refusal path, not about the claim.

No git repo and no commits here, unlike that fixture: nothing in this module reads a
version, writes a file or cares whether the tree is clean, so a `tmp_path` with planted
ledgers is the whole world it can see.
"""

from __future__ import annotations

import json
from pathlib import Path

from basicly import capability_proof, tracker_usage, usage
from basicly.capability_proof import CAPABILITY_VERIFY_CHECK

REPO_ROOT = Path(__file__).resolve().parents[1]


def _plant(root: Path, relative: Path, counts: dict[str, int]) -> None:
    """Write a counter ledger in the shape its recorder writes it."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({
            key: {"count": count, "last_used": "2026-07-26"} for key, count in counts.items()
        }),
        encoding="utf-8",
    )


def _plant_tracker_ledger(root: Path) -> None:
    """Write one measured tracker surface, the committed half of the evidence."""
    path = root / tracker_usage.LEDGER_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '{"binary":"br","subcommand":"gate report","site":"engine","ok":true}\n',
        encoding="utf-8",
    )


def _declare_check(root: Path, name: str, command: list[str]) -> None:
    """Declare one `[[verify.checks]]` capability."""
    rendered = ", ".join(f'"{arg}"' for arg in command)
    (root / "basicly.toml").write_text(
        f'[[verify.checks]]\nname = "{name}"\ncommand = [{rendered}]\nmodes = ["full"]\n',
        encoding="utf-8",
    )


def test_recorded_executions_unions_all_three_ledgers(tmp_path: Path) -> None:
    """Every ledger on disk is read, and the verify keys cannot collide with a tool name.

    A check named `pytest` and a shell that typed `pytest` are different facts; they
    share this map, so the namespace is what keeps the second from answering for the
    first.
    """
    _plant(tmp_path, usage.USAGE_FILE, {"pytest": 785, "never-run-tool": 0})
    _plant(tmp_path, usage.VERIFY_CHECKS_FILE, {"pytest": 2})
    _plant_tracker_ledger(tmp_path)

    counts = capability_proof.recorded_executions(tmp_path)

    assert counts is not None
    assert counts["pytest"] == 785
    assert counts[f"{usage.VERIFY_CHECK_PREFIX}pytest"] == 2
    assert counts["br gate report"] == 1
    assert "never-run-tool" not in counts


def test_recorded_executions_is_none_when_no_ledger_exists(tmp_path: Path) -> None:
    """The distinction the caller fails closed on: no record at all is not a zero."""
    assert capability_proof.recorded_executions(tmp_path) is None


def test_a_repo_that_declares_no_capability_has_nothing_to_prove(tmp_path: Path) -> None:
    """A consumer with no `[verify]` section published no claim for this gate to hold."""
    assert capability_proof.shipped_capabilities(tmp_path) == ()
    assert capability_proof.unexercised_capabilities(tmp_path) == ()


def test_a_declared_capability_with_no_ledger_at_all_is_unproven(tmp_path: Path) -> None:
    """Fails closed: absence of a record is not evidence of an execution.

    One aggregate reason rather than one per capability, because the thing the human
    fixes is the same for all of them — nothing on this machine has recorded anything.
    """
    _declare_check(tmp_path, "planted", ["ruff"])

    reasons = capability_proof.unexercised_capabilities(tmp_path)

    assert len(reasons) == 1
    assert "no execution ledger" in reasons[0]
    assert usage.VERIFY_CHECKS_FILE.as_posix() in reasons[0]


def test_a_counter_at_zero_is_refused_exactly_as_an_absent_key_is(tmp_path: Path) -> None:
    """A counter the recorder created and never incremented is not an execution.

    The positive control is in the same call: `exercised` carries a count and must not
    be reported, or the gate is refusing everything rather than reading the ledger.
    """
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
    """`uv` running 6091 times says nothing about the check hiding behind it.

    The correction basicly-3yi3 made, held here at the inventory rather than at the
    release: a witness that counts who typed a word stays healthy for a check that was
    deleted outright.
    """
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
    """A tuple of pairs, not a mapping: an inventory that quietly shrinks proves less.

    Same defect as one curated down to nothing — the reason the return type is what it
    is, asserted rather than left to the docstring.
    """
    rendered = '[[verify.checks]]\nname = "same"\ncommand = ["x"]\nmodes = ["full"]\n'
    (tmp_path / "basicly.toml").write_text(rendered * 2, encoding="utf-8")

    assert len(capability_proof.shipped_capabilities(tmp_path)) == 2


def test_this_repos_own_capability_inventory_is_never_empty() -> None:
    """The other half of the empty-inventory rule: here, the gate must have teeth.

    An inventory that names nothing refuses nothing, which is exactly how the import
    contract reported `1 kept, 0 broken` for months. Asserted against the real tree, so
    it fails if `[[verify.checks]]` is emptied or the reader stops finding it.

    It deliberately does *not* assert the inventory is fully exercised: the counters are
    machine-local and git-ignored, so that assertion would pass here and fail in CI.
    """
    capabilities = capability_proof.shipped_capabilities(REPO_ROOT)

    labels = {label for label, _ in capabilities}
    assert {
        f"{CAPABILITY_VERIFY_CHECK} 'pytest'",
        f"{CAPABILITY_VERIFY_CHECK} 'projection-permissions'",
    } <= labels
    # A capability with no witness can never be refused, so the inventory would have
    # teeth in name only.
    assert all(witness for _, witness in capabilities)
