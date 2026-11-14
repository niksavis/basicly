from __future__ import annotations

import os
import pickle
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from basicly import kit_namespace

PARENT = """from pathlib import Path
from importlib.util import spec_from_file_location, module_from_spec
from sys import modules
HOME = Path(__file__).resolve().parent
def child():
    key = "basicly_tracker_kit_child"
    if key in modules:
        return modules[key]
    spec = spec_from_file_location(key, HOME / "child.py")
    module = module_from_spec(spec)
    modules[key] = module
    spec.loader.exec_module(module)
    return module
"""


def _source(home: Path, child: str) -> None:
    home.mkdir(parents=True)
    (home / "parent.py").write_text(PARENT, encoding="utf-8")
    (home / "child.py").write_text(child, encoding="utf-8")


def test_roots_keep_lazy_siblings_dataclasses_and_real_registry_separate(tmp_path: Path) -> None:
    roots = [tmp_path / "first", tmp_path / "second"]
    for home, value in zip(roots, [1, 2], strict=True):
        _source(
            home,
            "from dataclasses import dataclass\n@dataclass\nclass Event:\n    value: int\n"
            + f"VALUE = {value}\n",
        )
    first, second = [kit_namespace.snapshot(home).load("parent") for home in roots]

    one, two = first.child(), second.child()

    assert one.VALUE == 1
    assert two.VALUE == 2
    assert first.child() is one
    assert kit_namespace.snapshot(roots[0]).load("child") is one
    assert second.child() is two
    assert one.Event is not two.Event
    assert sys.modules[one.Event.__module__] is one
    assert pickle.loads(pickle.dumps(one.Event(7))) == one.Event(7)


def test_lazy_siblings_use_captured_source_after_the_source_folder_moves(tmp_path: Path) -> None:
    home = tmp_path / "kit"
    _source(home, "VALUE = 'captured'\n")
    parent = kit_namespace.snapshot(home).load("parent")
    moved = home.rename(tmp_path / "moved")
    (moved / "child.py").write_text("VALUE = 'changed'\n", encoding="utf-8")

    assert parent.child().VALUE == "captured"
    assert kit_namespace.snapshot(moved).load("parent").child().VALUE == "changed"


def test_changed_source_at_one_root_gets_a_new_generation(tmp_path: Path) -> None:
    _source(tmp_path / "kit", "VALUE = 1\n")
    home = tmp_path / "kit"
    old = kit_namespace.snapshot(home).load("parent")
    (home / "child.py").write_text("VALUE = 2\n", encoding="utf-8")

    new = kit_namespace.snapshot(home).load("parent")

    assert old.child().VALUE == 1
    assert new.child().VALUE == 2


def test_runtime_resource_changes_do_not_reuse_the_old_policy(tmp_path: Path) -> None:
    home = tmp_path / "kit"
    _source(
        home,
        "import json\nfrom pathlib import Path\n"
        "VALUE = json.loads(Path(__file__).with_name('public-template.json').read_text())\n",
    )
    policy = home / "public-template.json"
    policy.write_text('{"policy":1}', encoding="utf-8")
    before = kit_namespace.snapshot(home).load("parent").child()
    policy.write_text('{"policy":2}', encoding="utf-8")

    after = kit_namespace.snapshot(home).load("parent").child()

    assert before.VALUE == {"policy": 1}
    assert after.VALUE == {"policy": 2}


def test_identical_python_at_different_roots_reads_each_roots_policy(tmp_path: Path) -> None:
    body = (
        "import json\nfrom pathlib import Path\n"
        "VALUE = json.loads(Path(__file__).with_name('public-template.json').read_text())\n"
    )
    for name, value in (("first", 1), ("second", 2)):
        home = tmp_path / name
        _source(home, body)
        (home / "public-template.json").write_text(str(value), encoding="utf-8")

    first = kit_namespace.snapshot(tmp_path / "first").load("parent")
    second = kit_namespace.snapshot(tmp_path / "second").load("parent")

    assert first.child().VALUE == 1
    assert second.child().VALUE == 2


def test_source_changes_during_capture_refuse_before_module_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "kit"
    _source(home, "VALUE = 1\n")
    original = Path.read_bytes

    def changed(path: Path) -> bytes:
        data = original(path)
        if path == home / "child.py":
            path.write_text("VALUE = 2\n", encoding="utf-8")
        return data

    monkeypatch.setattr(Path, "read_bytes", changed)

    with pytest.raises(kit_namespace.KitImportError, match="changed during loading"):
        kit_namespace.snapshot(home)


def test_a_failed_sibling_cannot_poison_a_later_load(tmp_path: Path) -> None:
    home = tmp_path / "kit"
    _source(home, "raise ValueError('invalid sibling')\n")
    parent = kit_namespace.snapshot(home).load("parent")
    with pytest.raises(ValueError, match="invalid sibling"):
        parent.child()
    with pytest.raises(ValueError, match="invalid sibling"):
        parent.child()
    _source(tmp_path / "other", "VALUE = 'unrelated'\n")

    assert kit_namespace.snapshot(tmp_path / "other").load("parent").child().VALUE == "unrelated"


def test_a_missing_resource_refuses_without_reusing_another_roots_policy(tmp_path: Path) -> None:
    body = (
        "from pathlib import Path\n"
        "VALUE = Path(__file__).with_name('public-template.json').read_text()\n"
    )
    for name in ("first", "second"):
        home = tmp_path / name
        _source(home, body)
        (home / "public-template.json").write_text(name, encoding="utf-8")
    first = kit_namespace.snapshot(tmp_path / "first").load("parent")
    second = kit_namespace.snapshot(tmp_path / "second").load("parent")
    assert first.child().VALUE == "first"
    resource = tmp_path / "second/public-template.json"
    saved = resource.rename(resource.with_name("saved-template.json"))

    with pytest.raises(FileNotFoundError):
        second.child()
    saved.rename(resource)
    assert second.child().VALUE == "second"


def test_simultaneous_lazy_imports_publish_one_complete_module(tmp_path: Path) -> None:
    home = tmp_path / "kit"
    _source(home, "VALUE = object()\nRUNS = globals().get('RUNS', 0) + 1\n")
    parent = kit_namespace.snapshot(home).load("parent")
    start = threading.Barrier(8)

    def load() -> object:
        start.wait(timeout=10)
        module = parent.child()
        assert module.RUNS == 1
        return module.VALUE

    with ThreadPoolExecutor(max_workers=8) as workers:
        values = list(workers.map(lambda _: load(), range(8)))

    assert all(value is values[0] for value in values)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="this filesystem probe needs POSIX FIFOs")
def test_a_kit_cannot_supply_source_through_a_fifo(tmp_path: Path) -> None:
    home = tmp_path / "kit"
    _source(home, "VALUE = 1\n")
    fifo = home / "unexpected.py"
    os.mkfifo(fifo)
    errors: list[OSError] = []

    def supply() -> None:
        try:
            with fifo.open("wb") as stream:
                stream.write(b"VALUE = 2\n")
        except OSError as exc:
            errors.append(exc)

    writer = threading.Thread(target=supply)
    writer.start()
    try:
        with pytest.raises(kit_namespace.KitImportError, match="regular files"):
            kit_namespace.snapshot(home)
    finally:
        reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
        writer.join(timeout=10)
        os.close(reader)
        assert not writer.is_alive()
    assert not errors
