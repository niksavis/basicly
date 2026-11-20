from __future__ import annotations

import importlib.util
import json
import subprocess  # nosec B404
import sys
from pathlib import Path
from types import ModuleType

import pytest

KIT = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "tracker"
CLI = KIT / "cli.py"


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


forms = _load("tracker_vault_arguments_forms", "vault_arguments.py")


def _cli(*argv: str) -> dict:
    done = subprocess.run(  # nosec B603
        [sys.executable, str(CLI), *argv], capture_output=True, text=True, check=False
    )
    return json.loads(done.stdout)


@pytest.mark.parametrize("verb", sorted(forms.FORMS))
def test_no_vault_form_usage_repeats_its_verb(verb: str) -> None:
    usage = forms.FORMS[verb]().format_usage()

    assert "{" + verb + "}" not in usage


def test_the_local_comment_takes_the_comment_text_flag(tmp_path: Path) -> None:
    ledger = str(tmp_path / "ledger")
    record = _cli("create", ledger, "--prefix", "acme", "--title", "x")["record"]

    _cli("comment", ledger, record, "--text", "by flag")
    _cli("comment", ledger, record, "by position")

    texts = [entry["text"] for entry in _cli("show", ledger, record)["comment_log"]]
    assert texts == ["by flag", "by position"]


def test_a_refused_vault_comment_has_its_comment_form_named(
    capsys: pytest.CaptureFixture[str],
) -> None:
    argv = ["comment", "--vault", "V", "--text", "x", "comment", "acme-1"]

    with pytest.raises(SystemExit) as refused:
        forms.comment().parse_args(argv)

    assert refused.value.code == 2
    assert "the form is: cli.py comment RECORD --vault URL --text TEXT" in capsys.readouterr().err
