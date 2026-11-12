from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"

WRITER_A = "a" * 32
WRITER_B = "b" * 32


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


shapes = _load("basicly_tracker_kit_vault_shapes", "vault_shapes.py")
cli = _load("tracker_cli_vault_shapes", "cli.py")


class Vault:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.env = {
            **os.environ,
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.invalid",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.invalid",
        }
        root.mkdir()
        self.git("init", "-q", "-b", "main")

    def git(self, *args: str) -> str:
        done = subprocess.run(
            ["git", *args], cwd=self.root, env=self.env, check=True, capture_output=True, text=True
        )
        return done.stdout.strip()

    def write(self, path: str, text: str, *, append: bool = False) -> None:
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a" if append else "w", encoding="utf-8") as handle:
            handle.write(text)

    def commit(self, message: str) -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def genesis(self) -> str:
        for name in ("vault.toml", "FORMAT.md", "reader.py", "README.md"):
            self.write(name, f"{name}\n")
        return self.commit("genesis")


def _line(**fields: object) -> str:
    return json.dumps(fields, sort_keys=True) + "\n"


def _segment(writer: str, *events: dict[str, object]) -> str:
    return _line(kind="segment", writer=writer) + "".join(_line(**e) for e in events)


def _verdicts(vault: Vault) -> dict[str, str]:
    return {v.commit: v.shape for v in shapes.check(vault.root)}


@pytest.fixture
def vault(tmp_path: Path) -> Vault:
    made = Vault(tmp_path / "vault")
    made.genesis()
    return made


def test_genesis_format_batch_and_archive_commits_are_valid(vault: Vault) -> None:
    path_a = f"log/{WRITER_A}/00001.jsonl"
    vault.write(path_a, _segment(WRITER_A, {"kind": "claim", "writer": WRITER_A, "record": "x"}))
    first = vault.commit("batch")
    vault.write(path_a, _line(kind="comment", writer=WRITER_A, record="x"), append=True)
    second = vault.commit("append")
    vault.write("FORMAT.md", "FORMAT.md v2\n")
    fmt = vault.commit("format")
    (vault.root / "log" / "archive").mkdir()
    vault.git("mv", path_a, "log/archive/00001.jsonl")
    archived = vault.commit("archive")

    found = _verdicts(vault)

    assert [found[c] for c in (first, second, fmt, archived)] == [
        "batch",
        "batch",
        "format",
        "archive",
    ]
    assert len(shapes.valid_events(shapes.check(vault.root))) == 2


@pytest.mark.parametrize(
    ("case", "rule"),
    [
        ("forged-writer", "a line names a writer other than its segment writer"),
        ("web-edit", "an existing line was changed or removed"),
        ("two-writers", "a batch appends to one writer's segments"),
        ("headerless", "a new segment must start with a header naming its writer"),
        ("torn", "a line was left without its newline"),
        ("outside-log", "a batch appends to one writer's segments"),
    ],
)
def test_a_commit_of_any_other_shape_is_quarantined_alone(
    vault: Vault, case: str, rule: str
) -> None:
    path_a = f"log/{WRITER_A}/00001.jsonl"
    vault.write(path_a, _segment(WRITER_A, {"kind": "claim", "writer": WRITER_A, "record": "x"}))
    vault.commit("honest")
    if case == "forged-writer":
        vault.write(path_a, _line(kind="unassign", writer=WRITER_B, record="x"), append=True)
    elif case == "web-edit":
        vault.write(
            path_a, _segment(WRITER_A, {"kind": "claim", "writer": WRITER_A, "record": "y"})
        )
    elif case == "two-writers":
        vault.write(path_a, _line(kind="comment", writer=WRITER_A), append=True)
        vault.write(f"log/{WRITER_B}/00001.jsonl", _segment(WRITER_B))
    elif case == "headerless":
        vault.write(f"log/{WRITER_B}/00001.jsonl", _line(kind="comment", writer=WRITER_B))
    elif case == "torn":
        vault.write(path_a, json.dumps({"kind": "comment", "writer": WRITER_A}), append=True)
    else:
        vault.write("notes.txt", "x\n")
    bad = vault.commit(case)
    vault.write(
        f"log/{WRITER_B}/00001.jsonl", _segment(WRITER_B, {"kind": "comment", "writer": WRITER_B})
    )
    vault.git("add", "-A")
    vault.git("commit", "-q", "--allow-empty", "-m", "later honest batch")
    later = vault.git("rev-parse", "HEAD")

    verdicts = {v.commit: v for v in shapes.check(vault.root)}

    assert verdicts[bad].shape == "quarantined"
    assert rule in verdicts[bad].rule
    assert verdicts[later].valid or case in ("headerless", "two-writers")


def test_a_bypass_merge_is_quarantined_and_the_first_parent_chain_continues(vault: Vault) -> None:
    vault.git("checkout", "-q", "-b", "side")
    vault.write(f"log/{WRITER_B}/00001.jsonl", _segment(WRITER_B))
    vault.commit("side batch")
    vault.git("checkout", "-q", "main")
    vault.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A))
    honest = vault.commit("main batch")
    vault.git("merge", "-q", "--no-ff", "--no-edit", "side")
    merge = vault.git("rev-parse", "HEAD")

    verdicts = {v.commit: v for v in shapes.check(vault.root)}

    assert verdicts[honest].shape == "batch"
    assert verdicts[merge].shape == "quarantined"
    assert "merge" in verdicts[merge].rule
    assert len(verdicts) == 3


def test_nothing_after_a_valid_ended_event_counts(vault: Vault) -> None:
    path_a = f"log/{WRITER_A}/00001.jsonl"
    vault.write(path_a, _segment(WRITER_A, {"kind": "ended", "writer": WRITER_A}))
    ended = vault.commit("end")
    vault.write(path_a, _line(kind="claim", writer=WRITER_A, record="x"), append=True)
    after = vault.commit("late batch")

    verdicts = {v.commit: v for v in shapes.check(vault.root)}

    assert verdicts[ended].shape == "batch"
    assert verdicts[after].shape == "after-ended"
    assert all(e.get("record") != "x" for e in shapes.valid_events(list(verdicts.values())))


def test_proof_t_fork_shapes_quarantines_exactly_the_bad_commits(tmp_path: Path) -> None:
    vault = Vault(tmp_path / "vault")
    good = [vault.genesis()]
    path_a = f"log/{WRITER_A}/00001.jsonl"
    vault.write(path_a, _segment(WRITER_A, {"kind": "claim", "writer": WRITER_A, "record": "r"}))
    good.append(vault.commit("batch 1"))
    vault.write(path_a, _line(kind="comment", writer=WRITER_A, record="r"), append=True)
    good.append(vault.commit("batch 2"))
    vault.write(path_a, _line(kind="unassign", writer=WRITER_B, record="r"), append=True)
    bad = [vault.commit("forged")]
    text = (vault.root / path_a).read_text(encoding="utf-8").replace('"r"', '"s"', 1)
    vault.write(path_a, text)
    bad.append(vault.commit("web edit"))
    vault.git("checkout", "-q", "-b", "side")
    vault.write(f"log/{WRITER_B}/00001.jsonl", _segment(WRITER_B))
    vault.commit("side")
    vault.git("checkout", "-q", "main")
    vault.git("merge", "-q", "--no-ff", "--no-edit", "side")
    bad.append(vault.git("rev-parse", "HEAD"))
    vault.write(path_a, _line(kind="ended", writer=WRITER_A), append=True)
    good.append(vault.commit("ended"))
    vault.write(path_a, _line(kind="comment", writer=WRITER_A), append=True)
    bad.append(vault.commit("after ended"))

    shown = _verdicts(vault)

    assert {c for c, shape in shown.items() if shape in ("quarantined", "after-ended")} == set(bad)
    assert {c for c, shape in shown.items() if shape not in ("quarantined", "after-ended")} == set(
        good
    )


def test_vault_check_prints_each_commit_through_the_cli(
    vault: Vault, capsys: pytest.CaptureFixture[str]
) -> None:
    vault.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A))
    vault.commit("batch")

    code = cli.main(["vault-check", str(vault.root)])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert [c["shape"] for c in shown["commits"]] == ["genesis", "batch"]
    assert shown["quarantined"] == []


def test_vault_check_refuses_an_unreadable_vault_by_name(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(["vault-check", str(tmp_path / "missing")])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_REFUSED
    assert "vault-check could not read" in shown["refused"]
