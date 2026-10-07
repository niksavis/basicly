from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PAGE = (Path(__file__).parents[1] / ".basicly/core/kit/board/web/index.html").read_text(
    encoding="utf-8"
)
SLICES = (
    ("async function pick(", "function drawTabs("),
    ("function go(", "async function act("),
    ("function back(", "function clearDraft("),
    ("function parseRoute(", "const search ="),
)
PRELUDE = """
const assert = require('node:assert/strict');
const TABS = [['ready', 'Ready'], ['working', 'In progress'], ['closed', 'Closed']];
const state = { tab: 'ready', current: null };
const counts = { loads: 0, drawn: 0, reloads: 0 };
const touched = [];
const opened = [];
const pane = { replaceChildren() {} };
const document = {
  body: { classList: { remove() {} } },
  getElementById(id) { touched.push(id); return pane; },
};
const el = () => ({});
const drawList = () => { counts.drawn += 1; };
const attempt = async (work) => work();
const closedRecords = async () => [];
const load = () => { counts.loads += 1; };
async function open(record) { opened.push(record); state.current = { record }; }
const entries = ['#/ready'];
let at = 0;
let pending = Promise.resolve();
const fire = () => { pending = routed(); };
const location = {
  get hash() { return entries[at]; },
  set hash(value) { entries.splice(at + 1); entries.push(value); at += 1; fire(); },
  reload() { counts.reloads += 1; },
};
const history = { back() { at -= 1; fire(); }, forward() { at += 1; fire(); } };
"""


def _run(script: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required to exercise the tracker page"
    source = PRELUDE + "".join(PAGE[PAGE.index(a) : PAGE.index(b)] for a, b in SLICES)
    wrapped = (
        source
        + "(async () => {\n"
        + script
        + "\n})().catch((e) => { console.error(e); process.exit(1); });"
    )
    result = subprocess.run([node, "-e", wrapped], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_url_names_the_view_and_a_reload_restores_it() -> None:
    _run(r"""
await pick('working'); await pending;
assert.equal(location.hash, '#/working');
go('b-1'); await pending;
assert.equal(location.hash, '#/working/record/b-1');
state.tab = 'ready'; state.current = null;
await routed();
assert.equal(state.tab, 'working');
assert.deepEqual(opened.at(-1), 'b-1');
assert.deepEqual(parseRoute('#/record/b-2'), { view: null, record: 'b-2' });
assert.deepEqual(parseRoute('#/bogus'), { view: null, record: null });
assert.equal(routeHash('closed', 'a/b'), '#/closed/record/a%2Fb');
assert.deepEqual(parseRoute(routeHash('closed', 'a/b')), { view: 'closed', record: 'a/b' });
""")


def test_back_and_forward_restore_the_view_without_a_page_load() -> None:
    _run(r"""
await pick('working'); await pending;
go('b-1'); await pending;
back(); await pending;
assert.equal(location.hash, '#/working');
assert.equal(state.current, null);
history.back(); await pending;
assert.equal(state.current.record, 'b-1');
history.back(); await pending;
assert.equal(state.current, null);
history.back(); await pending;
assert.equal(state.tab, 'ready');
history.forward(); await pending;
assert.equal(state.tab, 'working');
assert.equal(counts.loads, 0);
assert.equal(counts.reloads, 0);
""")


def test_the_shell_keeps_its_navigation_while_views_change() -> None:
    _run(r"""
await pick('working'); await pending;
go('b-1'); await pending;
back(); await pending;
history.back(); await pending;
await pick('closed'); await pending;
assert.ok(counts.drawn > 0);
assert.deepEqual([...new Set(touched)], ['detail-pane']);
""")
