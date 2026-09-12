# module-size-waiver: cohesion: one module's contract in one place, and the six cases basicly-nwx4ku

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import dropin

_RATCHET = Path(__file__).parent.parent / ".scripts" / "ratchet.py"
_spec = importlib.util.spec_from_file_location("ratchet_for_dropin", _RATCHET)
assert _spec and _spec.loader
ratchet = importlib.util.module_from_spec(_spec)
sys.modules["ratchet_for_dropin"] = ratchet
_spec.loader.exec_module(ratchet)


def _fragment(repo: Path, name: str, body: str) -> Path:
    directory = repo / dropin.FRAGMENT_DIR
    directory.mkdir(exist_ok=True)
    path = directory / f"{name}.toml"
    path.write_text(body, encoding="utf-8")
    return path


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(  # nosec B603 B607
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    )
    return proc.stdout.strip()


def _repo_with_two_branches(root: Path) -> tuple[Path, str]:

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
    assert dropin.fragment_paths(tmp_path) == ()
    assert dropin.compose(tmp_path, "noqa_debt", frozen={"S603": 4}, count=4) == dropin.Baseline(
        {"S603": 4}, 4
    )


def test_fragments_are_read_in_filename_order(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-zzz", "[ratchet.noqa_debt]\ncount_delta = 1\n")
    _fragment(tmp_path, "basicly-aaa", "[ratchet.noqa_debt]\ncount_delta = 1\n")

    assert [path.name for path in dropin.fragment_paths(tmp_path)] == [
        "basicly-aaa.toml",
        "basicly-zzz.toml",
    ]


def test_two_lanes_adding_one_suppression_each_compose_to_both(tmp_path: Path) -> None:
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
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = {"S603" = -4}\n')

    composed = dropin.compose(tmp_path, "noqa_debt", frozen={"S603": 4, "S607": 1}, count=5)

    assert composed.frozen == {"S607": 1}


def test_a_fragment_for_another_gate_is_ignored(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", "[ratchet.comment_density]\ncount_delta = 9\n")

    assert dropin.compose(tmp_path, "noqa_debt", frozen={}, count=3) == dropin.Baseline({}, 3)


def test_unparseable_toml_is_refused_naming_the_fragment(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", "this is not toml\n")

    with pytest.raises(dropin.FragmentError, match=r"basicly\.d/basicly-one\.toml"):
        dropin.documents(tmp_path)


def test_a_total_where_a_delta_belongs_is_refused(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = {"S603" = "16"}\n')

    with pytest.raises(dropin.FragmentError, match="must be an integer delta"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_a_bool_is_refused_even_though_it_is_an_int(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", "[ratchet.noqa_debt]\ncount_delta = true\n")

    with pytest.raises(dropin.FragmentError, match="must be an integer delta"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_a_fractional_delta_moves_a_recorded_share(tmp_path: Path) -> None:
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
    _fragment(tmp_path, "basicly-one", "[ratchet.comment_density]\ncount_delta = 1\n")
    _fragment(tmp_path, "basicly-two", "[ratchet.comment_density]\ncount_delta = 1\n")

    composed = dropin.compose(tmp_path, "comment_density", frozen={}, count=2, fractional=True)

    assert composed == dropin.Baseline({}, 4)


def test_a_share_paid_off_to_zero_is_dropped_like_a_count(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", '[ratchet.comment_density]\nfrozen = {"a.py" = -55.0}\n')

    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 55.0}, count=0, fractional=True
    )

    assert composed.frozen == {}


def test_a_whole_delta_is_accepted_where_shares_are_recorded(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", '[ratchet.comment_density]\nfrozen = {"a.py" = -1}\n')

    composed = dropin.compose(
        tmp_path, "comment_density", frozen={"a.py": 55.0}, count=0, fractional=True
    )

    assert composed.frozen["a.py"] == pytest.approx(54.0)


def test_a_fractional_delta_is_refused_by_a_counting_ratchet(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = {"E402" = 1.5}\n')

    with pytest.raises(dropin.FragmentError, match="must be an integer delta"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_the_count_stays_whole_even_where_the_entries_are_shares(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", "[ratchet.comment_density]\ncount_delta = 1.0\n")

    with pytest.raises(dropin.FragmentError, match="count_delta must be an integer delta"):
        dropin.compose(tmp_path, "comment_density", frozen={}, count=0, fractional=True)


def test_a_string_share_is_refused_naming_the_kind_expected(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", '[ratchet.comment_density]\nfrozen = {"a.py" = "53.6"}\n')

    with pytest.raises(dropin.FragmentError, match="must be a numeric delta"):
        dropin.compose(tmp_path, "comment_density", frozen={}, count=0, fractional=True)


def test_a_frozen_key_that_is_not_a_table_is_refused(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-one", '[ratchet.noqa_debt]\nfrozen = "S603"\n')

    with pytest.raises(dropin.FragmentError, match="must be a table"):
        dropin.compose(tmp_path, "noqa_debt", frozen={}, count=0)


def test_a_frozen_delta_that_raises_a_recorded_baseline_is_refused(tmp_path: Path) -> None:

    _fragment(tmp_path, "basicly-x", '[ratchet.comment_density]\nfrozen = {"a.py" = 0.7}\n')

    with pytest.raises(dropin.FragmentError) as excinfo:
        dropin.compose(tmp_path, "comment_density", frozen={"a.py": 55.9}, count=0, fractional=True)

    message = str(excinfo.value)
    assert "basicly-x" in message and "55.9" in message and "56.6" in message


def test_a_frozen_delta_that_invents_an_unlisted_baseline_is_refused(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-x", '[ratchet.module_size]\nfrozen = {"new.py" = 500}\n')

    with pytest.raises(dropin.FragmentError, match="closed list does not name"):
        dropin.compose(tmp_path, "module_size", frozen={"old.py": 9000}, count=0)


def test_a_falling_delta_is_untouched(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-x", '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n')

    composed = dropin.compose(tmp_path, "module_size", frozen={"old.py": 9000}, count=0)

    assert composed.frozen == {"old.py": 8500}
    assert composed.rebaselined == {}


def test_a_rebaseline_is_allowed_named_and_counted(tmp_path: Path) -> None:
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
    _fragment(tmp_path, "basicly-x", '[ratchet.comment_density]\nrebaselined = {"a.py" = 0.7}\n')

    with pytest.raises(dropin.FragmentError, match="rebaseline_reason"):
        dropin.compose(tmp_path, "comment_density", frozen={"a.py": 55.9}, count=0, fractional=True)


def test_a_tracking_gate_takes_a_rising_delta(tmp_path: Path) -> None:

    _fragment(tmp_path, "basicly-x", '[ratchet.noqa_debt]\nfrozen = {"S603" = 1}\n')

    composed = dropin.compose(
        tmp_path, "noqa_debt", frozen={"S603": 4}, count=0, may_only=dropin.MAY_ONLY_TRACK
    )

    assert composed.frozen == {"S603": 5}


def test_a_stale_measurement_is_refused_naming_the_base_it_was_taken_at(tmp_path: Path) -> None:

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

    repo, _ = _repo_with_two_branches(tmp_path)
    _fragment(repo, "basicly-x", '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n')

    composed = dropin.compose(repo, "module_size", frozen={"old.py": 9000}, count=0)

    assert composed == dropin.Baseline({"old.py": 8500}, 0)


def test_a_base_the_head_contains_applies_unchanged(tmp_path: Path) -> None:

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

    _fragment(
        tmp_path,
        "basicly-x",
        '[ratchet]\nbase_commit = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"\n\n'
        '[ratchet.module_size]\nfrozen = {"old.py" = -500}\n',
    )

    composed = dropin.compose(tmp_path, "module_size", frozen={"old.py": 9000}, count=0)

    assert composed == dropin.Baseline({"old.py": 8500}, 0)


def test_a_base_that_is_not_a_commit_ish_string_is_refused(tmp_path: Path) -> None:
    _fragment(tmp_path, "basicly-x", "[ratchet]\nbase_commit = 7\n\n[ratchet.module_size]\n")

    with pytest.raises(dropin.FragmentError, match="base_commit must be the commit"):
        dropin.compose(tmp_path, "module_size", frozen={}, count=0)


def test_a_stale_measurement_for_another_gate_does_not_refuse_this_one(tmp_path: Path) -> None:

    repo, divergent = _repo_with_two_branches(tmp_path)
    _fragment(
        repo,
        "basicly-x",
        f'[ratchet]\nbase_commit = "{divergent}"\n\n[ratchet.comment_density]\ncount_delta = 1\n',
    )

    assert dropin.compose(repo, "module_size", frozen={}, count=3) == dropin.Baseline({}, 3)
