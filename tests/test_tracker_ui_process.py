from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

PAGE = (Path(__file__).parents[1] / ".basicly/core/kit/board/web/index.html").read_text(
    encoding="utf-8"
)


def _run(script: str) -> None:
    node = shutil.which("node")
    assert node, "Node is required to exercise the tracker page"
    prelude = """
const assert = require('node:assert/strict');
function el(tag, attrs, ...children) {
  const node = { tag, ...attrs, children: children.flat(Infinity).filter(x => x != null),
    listeners: {}, value: attrs.value || '', append(...next) { this.children.push(...next); },
    replaceChildren(...next) { this.children = next; },
    addEventListener(name, fn) { this.listeners[name] = fn; }, focus() {} };
  if (tag === 'select') node.value = node.children[0].value;
  Object.defineProperty(node, 'text', { get() {
    return this.textContent !== undefined ? this.textContent :
    this.children.map(x => typeof x === 'string' ? x : x.text).join(''); } });
  return node;
}
const state = { me: 'operator' };
const REFINE = 'refine';
const labelsOf = value => String(value || '').split(',');
const sectionOf = name => [name, name.replace(/^## /, '')];
const open = () => {};
const edit = () => {};
const dependencyForm = () => {};
const assignForm = () => {};
let sent;
const act = (action, body) => { sent = { action, body }; };
function find(node, predicate) {
  if (predicate(node)) return node;
  for (const child of node.children || []) {
    const found = typeof child === 'object' && find(child, predicate);
    if (found) return found;
  }
}
"""
    source = (
        prelude
        + PAGE[PAGE.index("function doneBlock(") : PAGE.index("function needsList(")]
        + PAGE[PAGE.index("function since(") : PAGE.index("function dependencyVerdict(")]
        + PAGE[PAGE.index("function waitingReason(") : PAGE.index("function titleBlock(")]
        + script
    )
    result = subprocess.run([node, "-e", source], text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stderr


def test_person_can_cancel_raw_idea_with_reason_without_claiming_completion() -> None:
    _run(r"""
const bar = actionsBar({record:'idea',holder:null}, 'review required');
find(bar, x => x.tag === 'button' && x.text === 'Close…').onclick();
const outcome = find(bar, x => x.tag === 'select');
const reason = find(bar, x => x['aria-label'] === 'Closure reason');
const submit = find(bar, x => x.tag === 'button' && x.text === 'Close as completed');
assert.equal(outcome.value, 'completed');
assert.equal(submit.disabled, true);
outcome.value = 'cancelled'; outcome.listeners.change();
assert.match(bar.text, /does not satisfy dependencies/);
assert.equal(submit.textContent, 'Cancel work');
reason.value = '   '; reason.listeners.input();
assert.equal(submit.disabled, true);
reason.value = 'Superseded by a different request'; reason.listeners.input();
assert.equal(submit.disabled, false);
submit.onclick();
assert.deepEqual(sent, {action:'close',body:{reason:reason.value,resolution:'cancelled'}});
""")


def test_completed_choice_posts_explicit_resolution_and_explains_gate() -> None:
    _run(r"""
const bar = actionsBar({record:'ready',holder:{name:'operator'}}, '');
find(bar, x => x.tag === 'button' && x.text === 'Close…').onclick();
assert.match(bar.text, /current INVEST review and recorded results/);
const reason = find(bar, x => x['aria-label'] === 'Closure reason');
reason.value = 'Delivered and verified'; reason.listeners.input();
find(bar, x => x.tag === 'button' && x.textContent === 'Close as completed').onclick();
assert.equal(sent.body.resolution, 'completed');
""")


def test_current_review_and_actual_confirmation_show_readable_evidence() -> None:
    _run(r"""
const shown = {fields:{},process:{revision:'current',owed:[],
  review:{revision:'current',invest:{valuable:'People can capture an idea'},conversation:[7],
    checks:[{criterion:'The idea is saved',command:['python','check.py'],expected:'one record'}]},
  confirmation:{revision:'current',checks:[{criterion:'The idea is saved',
    result:'one record observed',exit_code:0}]}}};
const text = doneBlock(shown).text;
assert.match(text, /INVEST review: recorded for this card/);
assert.match(text, /Confirmation: recorded for this card/);
assert.match(text, /People can capture an idea/);
assert.match(text, /Conversation references: 7/);
assert.match(text, /Expected: one record/);
assert.match(text, /Recorded result: one record observed \(exit 0\)/);
assert.ok(!text.includes('required.'));
""")


def test_stale_evidence_and_filled_fields_cannot_display_current_confirmation() -> None:
    _run(r"""
const shown = {fields:{acceptance_criteria:'a filled criterion'},process:{revision:'new',
  owed:['## INVEST Review'],review:{revision:'old'},confirmation:{revision:'old'}}};
const text = doneBlock(shown).text;
assert.match(text, /INVEST review: required/);
assert.match(text, /Confirmation: required/);
assert.match(text, /saved review does not cover the current card/);
assert.ok(!text.includes('recorded for this card'));
""")


def test_legacy_unshaped_card_is_blocked_by_authoritative_review_debt() -> None:
    _run(r"""
const waiting = waitingReason({fields:{}},{ready:false,blocking:['## INVEST Review']});
assert.match(waiting, /still needs INVEST Review/);
const bar = actionsBar({record:'old-idea',holder:null}, waiting);
assert.equal(find(bar, x => x.tag === 'button' && x.text === 'Claim').disabled, true);
""")
