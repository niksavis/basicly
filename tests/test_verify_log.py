"""What a failing check said, asserted against the two rules it has to hold at once.

A `pytest` gate failed once and left `{"detail": "output streamed rather than captured"}`.
Six later runs of the same command were green, so the identity was gone with the terminal
(basicly-zlqn7e). The log ends that — without bending either rule that made the artifact
refuse to hold output in the first place:

* **the artifact stays verdict metadata only**, so this is a sibling file and
  `tests/test_verify_artifact.py` still asserts the artifact holds no output;
* **a tool's stdout can carry a secret**, so the text goes through `redact` on the way in
  and lands only in the self-ignored directory the run records already live in.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import verify, verify_log
from basicly.config import VerifyCheck

if TYPE_CHECKING:
    import pytest


def test_a_failing_check_streams_and_still_leaves_its_words_in_a_file(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    """The whole point, through `run_check`: a tee, not a choice between the two.

    Both halves asserted together, because either alone is the defect. Without the stream an
    operator watching a 132-second gate sees nothing; without the file the failure exists
    only in a scrollback, which is how one flake became unnameable.
    """
    check = VerifyCheck(
        name="demo",
        command=(sys.executable, "-c", "print('the marker'); raise SystemExit(3)"),
        modes=frozenset({"full"}),
    )
    result = verify.run_check(check, tmp_path, "full")

    assert result.status == "fail"
    assert result.returncode == 3
    assert "the marker" in capfd.readouterr().out, "the check no longer streams"
    assert result.detail == "what it said: .basicly/usage/verify-fail-demo.log"
    assert "the marker" in (tmp_path / verify_log.LOG_DIR / "verify-fail-demo.log").read_text()
    # The caller's own copy stays empty on the streamed path, which is the contract
    # `verify_artifact` depends on: a gate's output belongs on the terminal, not in memory.
    assert result.output == ""


def test_a_passing_check_leaves_no_log_and_no_detail(tmp_path: Path) -> None:
    """38 checks pass on a green run; a file per passing check is 38 files nobody reads."""
    check = VerifyCheck(
        name="quiet", command=(sys.executable, "-c", "print('fine')"), modes=frozenset({"full"})
    )
    result = verify.run_check(check, tmp_path, "full")
    assert result.status == "pass"
    assert result.detail == ""
    assert not verify_log.log_path(tmp_path, "quiet").exists()


def test_the_tail_is_kept_and_the_head_is_accounted_for(tmp_path: Path) -> None:
    """A whole parallel pytest transcript is megabytes; the last screens name the failure."""
    long = "".join(f"line {n}\n" for n in range(200_000))
    assert len(long) > verify_log.TAIL_BYTES
    path = verify_log.write(tmp_path, "pytest", long)
    assert path is not None
    text = path.read_text()
    assert len(text) < len(long)
    assert text.endswith("line 199999\n"), "the tail is not the end of the run"
    # The loss is stated rather than silent, so a reader knows they are holding a tail.
    assert "characters are not kept" in text.splitlines()[0]


def test_a_short_output_is_kept_whole_and_says_nothing_about_a_tail(tmp_path: Path) -> None:
    """The common case. A truncation notice on an untruncated file would be a lie."""
    path = verify_log.write(tmp_path, "ruff", "one problem\n")
    assert path is not None
    assert path.read_text() == "one problem\n"


def test_a_secret_in_the_output_does_not_reach_the_file(tmp_path: Path) -> None:
    """The rule `verify_artifact` refused to hold output *for*, honoured here instead.

    Assembled by concatenation so this file's own literal cannot trip `secret-scan`.
    """
    leaked = "AKIA" + "IOSFODNN7EXAMPLE"
    path = verify_log.write(tmp_path, "pytest", f"boom\nAWS_ACCESS_KEY_ID={leaked}\n")
    assert path is not None
    text = path.read_text()
    assert leaked not in text
    assert "boom" in text, "redaction ate the diagnostic as well as the secret"


def test_an_empty_output_writes_no_file_at_all(tmp_path: Path) -> None:
    """Answering None rather than a path: a reader must never be sent to an empty file."""
    for empty in ("", "   \n\t\n"):
        assert verify_log.write(tmp_path, "pytest", empty) is None
    assert not verify_log.log_path(tmp_path, "pytest").exists()


def test_a_check_name_that_is_not_a_filename_still_gets_a_file(tmp_path: Path) -> None:
    """Check names are a repo's own `[[verify.checks]]` strings, not sanitised for a path."""
    path = verify_log.write(tmp_path, "pyright/linux plus", "detail\n")
    assert path is not None
    assert path.parent == tmp_path / verify_log.LOG_DIR
    assert "/" not in path.name and " " not in path.name
    assert path.read_text() == "detail\n"


def test_the_pointer_is_repo_relative_so_it_is_not_a_machine_path(tmp_path: Path) -> None:
    """It lands in a file other people read, and this repo bans machine paths in those."""
    path = verify_log.write(tmp_path, "pytest", "boom\n")
    line = verify_log.pointer(path, tmp_path)
    assert line == "what it said: .basicly/usage/verify-fail-pytest.log"
    assert str(tmp_path) not in line


def test_nothing_kept_means_no_pointer_rather_than_a_dangling_one() -> None:
    """A `detail` naming a file that does not exist is worse than the streamed message."""
    assert verify_log.pointer(None, Path("/anywhere")) == ""


def test_an_unwritable_directory_never_costs_the_verdict(tmp_path: Path) -> None:
    """`verify_artifact`'s rule, for the same reason: the caller already has the verdict."""
    blocker = tmp_path / verify_log.LOG_DIR
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("this is a file where the directory must go", encoding="utf-8")
    assert verify_log.write(tmp_path, "pytest", "boom\n") is None
