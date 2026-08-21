"""Fragment composition, which ``test_landing_anchors.py`` deliberately does not cover.

That file is a merge-queue test by design — its docstring says so — because the failure it
pins was in the rebase, not in the arithmetic. This one is the arithmetic: deltas add, the
sum does not depend on the order the fragments land in, an entry that nets to zero is
dropped rather than recorded, and a fragment that declares a total where a delta belongs is
refused loudly instead of being read as a very large delta.

One of the three ratchets records shares rather than counts, so composition is parameterised
on the entry kind; the cases below pin that the widening reaches the per-entry deltas and
stops there — ``count_delta`` counts waivers for that gate too (basicly-05g0).

The commutativity test is the one worth keeping. ``dropin``'s whole claim is that a composed
baseline is landing-order independent, and a regression that made composition order-sensitive
would still pass every other assertion here.
"""

# module-size-waiver: cohesion: one module's contract in one place, and the six cases basicly-nwx4ku
# adds are six distinct verdicts on one guard - refused, absent, ancestor, unresolvable,
# malformed, other-gate - none of which the others imply. Splitting them out is the gate's
# first remedy and was rejected on two counts: the record's demonstration is
# `pytest tests/test_dropin.py -k stale_measurement`, which a move silently turns into a
# zero-selected pass, and the fixture the four real-git cases share would then be duplicated
# or imported across suites where every other suite in this tree spells its own. Nothing
# here restates code: 4813 tokens at 31.5% prose against the 50% cap.

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import dropin

# `.scripts/` is no package, so the gate helper is loaded by path the way its own tests do.
_RATCHET = Path(__file__).parent.parent / ".scripts" / "ratchet.py"
_spec = importlib.util.spec_from_file_location("ratchet_for_dropin", _RATCHET)
assert _spec and _spec.loader
ratchet = importlib.util.module_from_spec(_spec)
sys.modules["ratchet_for_dropin"] = ratchet
_spec.loader.exec_module(ratchet)


def _fragment(repo: Path, name: str, body: str) -> Path:
    """Write ``basicly.d/<name>.toml`` and return it, creating the directory on first use."""
    directory = repo / dropin.FRAGMENT_DIR
    directory.mkdir(exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _git(repo: Path, *args: str) -> str:
    """One git command in *repo*, failing the test rather than the composition."""
    proc = subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _repo_with_two_branches(root: Path) -> tuple[Path, str]:
    """A real repository whose HEAD does not contain the returned commit.

    Two branches off one root commit, checked out on the first: the commit handed back is
    the tip of the *other* one, so it is a real object this repository holds and is still
    not in HEAD's history. That distinction is the whole check — a fabricated sha would
    exercise git's "cannot resolve" path instead, which has to answer the opposite way.
    """
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "user.name", "Harness Test")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "seed.txt").write_text("root\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "root")

    _git(repo, "checkout", "-q", "-b", "sibling")
    (repo / "seed.txt").write_text("sibling\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "sibling")
    divergent = _git(repo, "rev-parse", "HEAD")

    _git(repo, "checkout", "-q", "main")
    return repo, divergent


def test_no_fragment_directory_composes_to_the_recorded_baseline(tmp_path: Path) -> None:
    """With no ``basicly.d`` at all, the recorded table is returned unchanged."""
    assert dropin.fragment_paths(tmp_path) == ()
    assert dropin.compose(tmp_path, "noqa_debt", frozen={"S603": 4}, count=4) == dropin.Baseline(
        {"S603": 4}, 4
    )


def test_fragments_are_read_in_filename_order(tmp_path: Path) -> None:
    """Sorted by name, so composition is byte-identical on any filesystem."""
    _fragment(tmp_path, "basicly-zzz", "[ratchet.noqa_debt]\ncount_delta = 1\n")
    _fragment(tmp_path, "basicly-aaa", "[ratchet.noqa_debt]\ncount_delta = 1\n")

    assert [path.name for path in dropin.fragment_paths(tmp_path)] == [
        "basicly-aaa.toml",
        "basicly-zzz.toml",
    ]


def test_two_lanes_adding_one_suppression_each_compose_to_both(tmp_path: Path) -> None:
    """The case that bounced the 2026-08-08 pass: both deltas survive, neither overwrites."""
    _fragment(
        tmp_path, "basicly-one", '[ratchet.noqa_debt]\ncount_delta = 1\nfrozen = {"S603" = 1}\n'
    )
    _fragment(
        tmp_path, "basicly-two", '[ratchet.noqa_debt]\ncount_delta = 1\nfrozen = {"S607" = 1}\n'
    )

    composed = dropin.compose(
        tmp_path, "noqa_debt", frozen={"S603": 4}, count=10, may_only=dropin.MAY_ONLY_TRACK
    )

    assert composed == dropin.Baseline({"S603": 5, "S607": 1}, 12)


def test_composition_does_not_depend_on_landing_order(tmp_path: Path) -> None:
    """The claim the whole split rests on: addition is commutative, so order is irrelevant."""
    first = '[ratchet.noqa_debt]\ncount_delta = 2\nfrozen = {"S603" = 2}\n'
    second = '[ratchet.noqa_debt]\ncount_delta = 3\nfrozen = {"S603" = -1, "S607" = 1}\n'

    _fragment(tmp_path, "basicly-aaa", first)
    _fragment(tmp_path, "basicly-zzz", second)
    forwards = dropin.compose(
        tmp_path, "noqa_debt", frozen={"S603": 4}, count=10, may_only=dropin.MAY_ONLY_TRACK
    )

    _fragment(tmp_path, "basicly-aaa", second)
    _fragment(tmp_path, "basicly-zzz", first)
    backwards = dropin.compose(
        tmp_path, "noqa_debt", frozen={"S603": 4}, count=10, may_only=dropin.MAY_ONLY_TRACK
    )

    assert forwards == backwards == dropin.Baseline({"S603": 5, "S607": 1}, 15)


def test_an_entry_paid_off_to_zero_is_dropped_rather_than_recorded(tmp_path: Path) -> None:
    """A debt brought to zero leaves the table, matching the rule those tables already state."""
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = {"S603" = -4}\n')

    composed = dropin.compose(tmp_path, "noqa_debt", frozen={"S603": 4, "S607": 1}, count=5)

    assert composed.frozen == {"S607": 1}


def test_a_fragment_for_another_gate_is_ignored(tmp_path: Path) -> None:
    """A fragment names its gate, so one gate's deltas never reach another's baseline."""
    _fragment(tmp_path, "basicly-one", "[ratchet.comment_density]\ncount_delta = 9\n")

    assert dropin.compose(tmp_path, "noqa_debt", frozen={}, count=3) == dropin.Baseline({}, 3)


def test_unparseable_toml_is_refused_naming_the_fragment(tmp_path: Path) -> None:
    """Never skipped: a lane's declaration going quiet is what this directory removes."""
    _fragment(tmp_path, "basicly-one", "this is not toml\n")

    with pytest.raises(dropin.FragmentError, match=r"basicly\.d/basicly-one\.toml"):
        dropin.documents(tmp_path)


def test_a_total_where_a_delta_belongs_is_refused(tmp_path: Path) -> None:
    """A string is not a delta, and reading it as one would silently move the baseline."""
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = {"S603" = "16"}\n')

    with pytest.raises(dropin.FragmentError, match="must be an integer delta"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_a_bool_is_refused_even_though_it_is_an_int(tmp_path: Path) -> None:
    """``isinstance(True, int)`` is true, so the guard has to exclude bools explicitly."""
    _fragment(tmp_path, "basicly-one", "[ratchet.noqa_debt]\ncount_delta = true\n")

    with pytest.raises(dropin.FragmentError, match="must be an integer delta"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_a_fractional_delta_moves_a_recorded_share(tmp_path: Path) -> None:
    """``comment_density`` freezes a percentage, so its per-entry deltas are floats."""
    _fragment(
        tmp_path,
        "basicly-one",
        '[ratchet.comment_density]\ncount_delta = 1\nfrozen = {"a.py" = -1.4}\n',
    )

    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 55.0}, count=2, fractional=True
    )

    assert composed.frozen["a.py"] == pytest.approx(53.6)
    assert composed.count == 3


def test_two_lanes_each_taking_a_waiver_compose_to_both(tmp_path: Path) -> None:
    """The case ``basicly-kr7t`` was blocked on: two density waivers, neither anchor edit."""
    _fragment(tmp_path, "basicly-one", "[ratchet.comment_density]\ncount_delta = 1\n")
    _fragment(tmp_path, "basicly-two", "[ratchet.comment_density]\ncount_delta = 1\n")

    composed = dropin.compose(tmp_path, "comment_density", frozen={}, count=2, fractional=True)

    assert composed == dropin.Baseline({}, 4)


def test_a_share_paid_off_to_zero_is_dropped_like_a_count(tmp_path: Path) -> None:
    """A graduated module leaves the frozen table whichever unit the table is in."""
    _fragment(tmp_path, "basicly-one", '[ratchet.comment_density]\nfrozen = {"a.py" = -55.0}\n')

    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 55.0}, count=0, fractional=True
    )

    assert composed.frozen == {}


def test_a_whole_delta_is_accepted_where_shares_are_recorded(tmp_path: Path) -> None:
    """TOML spells a whole number without a point, and -1 is a share a lane may mean."""
    _fragment(tmp_path, "basicly-one", '[ratchet.comment_density]\nfrozen = {"a.py" = -1}\n')

    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 55.0}, count=0, fractional=True
    )

    assert composed.frozen["a.py"] == pytest.approx(54.0)


def test_a_fractional_delta_is_refused_by_a_counting_ratchet(tmp_path: Path) -> None:
    """The reason *fractional* is a parameter: 1.5 suppressions is not a state to compose."""
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = {"E402" = 1.5}\n')

    with pytest.raises(dropin.FragmentError, match="must be an integer delta"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_the_count_stays_whole_even_where_the_entries_are_shares(tmp_path: Path) -> None:
    """``count_delta`` counts waivers, so widening the entries must not widen it."""
    _fragment(tmp_path, "basicly-one", "[ratchet.comment_density]\ncount_delta = 1.0\n")

    with pytest.raises(dropin.FragmentError, match="count_delta must be an integer delta"):
        dropin.compose(tmp_path, "comment_density", frozen={}, count=0, fractional=True)


def test_a_string_share_is_refused_naming_the_kind_expected(tmp_path: Path) -> None:
    """A fragment that cannot be read as a delta stops the gate, never widens the baseline."""
    _fragment(tmp_path, "basicly-one", '[ratchet.comment_density]\nfrozen = {"a.py" = "53.6"}\n')

    with pytest.raises(dropin.FragmentError, match="must be a numeric delta"):
        dropin.compose(tmp_path, "comment_density", frozen={}, count=0, fractional=True)


def test_a_frozen_key_that_is_not_a_table_is_refused(tmp_path: Path) -> None:
    """``frozen`` holds per-entry deltas, so a scalar there is a malformed fragment."""
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = "S603"\n')

    with pytest.raises(dropin.FragmentError, match="must be a table"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_a_frozen_delta_that_raises_a_recorded_baseline_is_refused(tmp_path: Path) -> None:
    """The hole basicly-e2mz.20 measured: ``composed.get(entry, 0) + delta``, no sign check.

    Live instance at the time of filing — a fragment declared ``+0.7`` on a module whose
    go-live share was 55.9, and the gate enforced 56.6 while ``ratchet.py`` documented that a
    frozen subject may only fall.
    """
    _fragment(tmp_path, "basicly-x", '[ratchet.comment_density]\nfrozen = {"a.py" = 0.7}\n')

    with pytest.raises(dropin.FragmentError) as excinfo:
        dropin.compose(tmp_path, "comment_density", frozen={"a.py": 55.9}, count=0, fractional=True)

    message = str(excinfo.value)
    assert "basicly-x" in message and "55.9" in message and "56.6" in message


def test_a_frozen_delta_that_invents_an_unlisted_baseline_is_refused(tmp_path: Path) -> None:
    """The closed list holds: a fragment may not add what only ``pyproject.toml`` may."""
    _fragment(tmp_path, "basicly-x", '[ratchet.module_size]\nfrozen = {"new.py" = 500}\n')

    with pytest.raises(dropin.FragmentError, match="closed list does not name"):
        dropin.compose(tmp_path, "module_size", frozen={"old.py": 9000}, count=0)


def test_a_falling_delta_is_untouched(tmp_path: Path) -> None:
    """The discriminator: the refusal is on direction, not on the presence of a delta."""
    _fragment(tmp_path, "basicly-x", '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n')

    composed = dropin.compose(tmp_path, "module_size", frozen={"old.py": 9000}, count=0)

    assert composed.frozen == {"old.py": 8500}
    assert composed.rebaselined == {}


def test_a_rebaseline_is_allowed_named_and_counted(tmp_path: Path) -> None:
    """The legitimate shrinking-denominator case keeps a route, and it is countable."""
    _fragment(
        tmp_path,
        "basicly-x",
        '[ratchet.comment_density]\nrebaseline_reason = "code deletion shrank the denominator"\n'
        'rebaselined = {"a.py" = 0.7}\n',
    )

    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 55.9}, count=0, fractional=True
    )

    assert composed.frozen == {"a.py": 56.6}
    assert composed.rebaselined == {"a.py": ("basicly.d/basicly-x.toml",)}


def test_an_accumulated_rebaseline_names_every_fragment_that_loosened_it(
    tmp_path: Path,
) -> None:
    """Keyed by entry, four loosenings of one file read as one (basicly-wpqdag).

    Measured on this repo when the defect was found: `tests/test_loop.py` carried four
    declarations - 311, 146, 13 and 109 - and `merge.py` three, and the summary line said
    `19 rebaselined` against 41 declared. Every entry bound individually, so nothing was
    wrongly admitted; the number an operator reads to judge a file's debt was the count of
    files. The composed baseline is still the sum, which is the control that accumulating the
    *names* did not change the arithmetic.
    """
    for name, delta in (("basicly-a", 0.7), ("basicly-b", 0.3), ("basicly-c", 0.1)):
        _fragment(
            tmp_path,
            name,
            f'[ratchet.comment_density]\nrebaseline_reason = "{name} shrank it"\n'
            f'rebaselined = {{"a.py" = {delta}}}\n',
        )

    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 50.0}, count=0, fractional=True
    )

    assert composed.rebaselined == {
        "a.py": (
            "basicly.d/basicly-a.toml",
            "basicly.d/basicly-b.toml",
            "basicly.d/basicly-c.toml",
        )
    }
    assert composed.frozen == {"a.py": 51.1}


def test_the_reported_count_is_declarations_and_names_the_entries_apart(
    tmp_path: Path,
) -> None:
    """The clause an operator reads. Entries alone is the number that hid the accumulation."""
    for name in ("basicly-a", "basicly-b"):
        _fragment(
            tmp_path,
            name,
            f'[ratchet.comment_density]\nrebaseline_reason = "{name}"\n'
            'rebaselined = {"a.py" = 0.1}\n',
        )
    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 50.0}, count=0, fractional=True
    )

    clause = ratchet.rebaseline_clause(
        ratchet.Ratchet(frozen=composed.frozen, count=0, rebaselined=composed.rebaselined)
    )

    assert clause == ", 2 rebaselined across 1 entry"


def test_a_rebaseline_without_a_reason_is_refused(tmp_path: Path) -> None:
    """An unexplained rebaseline is the silent raise under a new name."""
    _fragment(tmp_path, "basicly-x", '[ratchet.comment_density]\nrebaselined = {"a.py" = 0.7}\n')

    with pytest.raises(dropin.FragmentError, match="rebaseline_reason"):
        dropin.compose(tmp_path, "comment_density", frozen={"a.py": 55.9}, count=0, fractional=True)


def test_a_tracking_gate_takes_a_rising_delta(tmp_path: Path) -> None:
    """noqa-debt's record must equal the tree, so ``+1`` there keeps it true.

    The control for the refusals above: same shape, opposite verdict, decided only by the
    direction the gate declares.
    """
    _fragment(tmp_path, "basicly-x", '[ratchet.noqa_debt]\nfrozen = {"S603" = 1}\n')

    composed = dropin.compose(
        tmp_path, "noqa_debt", frozen={"S603": 4}, count=0, may_only=dropin.MAY_ONLY_TRACK
    )

    assert composed.frozen == {"S603": 5}


# ---------------------------------------------------------------------------
# basicly-nwx4ku: a delta composes in any order, the headroom it was sized
# against does not.
# ---------------------------------------------------------------------------


def test_a_stale_measurement_is_refused_naming_the_base_it_was_taken_at(tmp_path: Path) -> None:
    """The two-lane case, reduced: the tree the number was measured on is not this one.

    `basicly-gvlpxm` and `basicly-kjc5.63` both measured `merge.py` at exactly 2 tokens of
    headroom, both spent that same 2, and the composed tree failed a gate neither branch
    failed. A recorded base turns that into a mechanical refusal.
    """
    repo, divergent = _repo_with_two_branches(tmp_path)
    _fragment(
        repo,
        "basicly-x",
        f'[ratchet]\nbase_commit = "{divergent}"\n\n'
        '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n',
    )

    with pytest.raises(dropin.FragmentError) as excinfo:
        dropin.compose(repo, "module_size", frozen={"old.py": 9000}, count=0)

    message = str(excinfo.value)
    assert divergent in message
    assert "basicly.d/basicly-x.toml" in message
    assert "Re-measure" in message


def test_a_fragment_that_records_no_base_applies_unchanged(tmp_path: Path) -> None:
    """Every fragment written before this field exists records none, and must still land.

    The field is hand-written, so absence is the common case and cannot be a violation —
    refusing it would stop every lane in flight rather than catching a stale measurement.
    Asserted in a real repository, so the fragment is composed on a tree that *could* have
    answered the ancestry question and was simply never asked.
    """
    repo, _ = _repo_with_two_branches(tmp_path)
    _fragment(repo, "basicly-x", '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n')

    composed = dropin.compose(repo, "module_size", frozen={"old.py": 9000}, count=0)

    assert composed == dropin.Baseline({"old.py": 8500}, 0)


def test_a_base_the_head_contains_applies_unchanged(tmp_path: Path) -> None:
    """Ancestry, not equality: work landing on top of a measurement does not stale it.

    The discriminator for the refusal above — same fragment shape, same repository, and the
    verdict turns only on whether HEAD contains the recorded commit.
    """
    repo, _ = _repo_with_two_branches(tmp_path)
    ancestor = _git(repo, "rev-parse", "HEAD")
    (repo / "seed.txt").write_text("later\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "later")
    _fragment(
        repo,
        "basicly-x",
        f'[ratchet]\nbase_commit = "{ancestor}"\n\n'
        '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n',
    )

    composed = dropin.compose(repo, "module_size", frozen={"old.py": 9000}, count=0)

    assert composed == dropin.Baseline({"old.py": 8500}, 0)


def test_a_base_no_repository_can_resolve_is_not_a_refusal(tmp_path: Path) -> None:
    """Git answering "cannot tell" is not the same as answering "no".

    A tree copied without its `.git` is what the suite's own `work_repo` fixture hands the
    gates, and a shallow clone is the same shape in CI. Refusing there would fail on the
    absence of a repository rather than on a stale measurement.
    """
    _fragment(
        tmp_path,
        "basicly-x",
        '[ratchet]\nbase_commit = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"\n\n'
        '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n',
    )

    composed = dropin.compose(tmp_path, "module_size", frozen={"old.py": 9000}, count=0)

    assert composed == dropin.Baseline({"old.py": 8500}, 0)


def test_a_base_that_is_not_a_commit_ish_string_is_refused(tmp_path: Path) -> None:
    """A number or an empty string is not a commit, and reading one as absent would hide it."""
    _fragment(tmp_path, "basicly-x", "[ratchet]\nbase_commit = 7\n\n[ratchet.module_size]\n")

    with pytest.raises(dropin.FragmentError, match="base_commit must be the commit"):
        dropin.compose(tmp_path, "module_size", frozen={}, count=0)


def test_a_stale_measurement_for_another_gate_does_not_refuse_this_one(tmp_path: Path) -> None:
    """A fragment is only checked against the gates it actually moves.

    Otherwise one lane's stale fragment would stop every ratchet gate in the tree, including
    the ones its deltas never touch.
    """
    repo, divergent = _repo_with_two_branches(tmp_path)
    _fragment(
        repo,
        "basicly-x",
        f'[ratchet]\nbase_commit = "{divergent}"\n\n[ratchet.comment_density]\ncount_delta = 1\n',
    )

    assert dropin.compose(repo, "module_size", frozen={}, count=3) == dropin.Baseline({}, 3)
