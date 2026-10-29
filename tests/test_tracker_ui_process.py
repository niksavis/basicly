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
const writer = name => name;
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
        + PAGE[PAGE.index("function contextParts(") : PAGE.index("function grow(")]
        + PAGE[PAGE.index("function contextDescription(") : PAGE.index("function storyForm(")]
        + PAGE[PAGE.index("function commentsBlock(") : PAGE.index("function refinementBlock(")]
        + PAGE[PAGE.index("function notices(") : PAGE.index("function titleBlock(")]
        + PAGE[PAGE.index("function busy()") : PAGE.index("async function watchVersion()")]
        + PAGE[PAGE.index("function kitBanner(") : PAGE.index("function toast(")]
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
assert.match(bar.text, /current review and recorded results/);
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
assert.match(text, /Review: recorded for this card/);
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
assert.match(text, /Review: required/);
assert.match(text, /Confirmation: required/);
assert.match(text, /saved review does not cover the current card/);
assert.ok(!text.includes('recorded for this card'));
""")


def test_legacy_unshaped_card_is_blocked_by_authoritative_review_debt() -> None:
    _run(r"""
const waiting = waitingReason({fields:{}},{ready:false,blocking:['## INVEST Review']});
assert.match(waiting, /still needs an agent review/);
const bar = actionsBar({record:'old-idea',holder:null}, waiting);
assert.equal(find(bar, x => x.tag === 'button' && x.text === 'Claim').disabled, true);
""")


def test_completed_card_keeps_its_recorded_review_and_results_visible() -> None:
    _run(r"""
const shown = {status:'closed',fields:{},process:{revision:'current',owed:[],
  review:{revision:'current',checks:[]},confirmation:{revision:'current'}}};
const cards = notices(shown,{blocking:[]});
assert.equal(cards.length, 1);
assert.match(cards[0].text, /Confirmation: recorded for this card/);
assert.equal(notices({status:'closed',fields:{}},{blocking:[]}).length, 0);
""")


def test_readonly_refinement_handoff_does_not_block_background_refresh() -> None:
    _run(r"""
const pane = {scrollTop:0,contains(){return false;},querySelector(){return null;},
  querySelectorAll(){return [
  {readOnly:true,value:'Refine the selected card'}, {readOnly:false,value:''}];}};
const document = {hidden:false,activeElement:null,getElementById(){return pane;}};
let refreshed = 0;
const load = async () => {refreshed++;};
const toast = () => {};
(async()=>{
  assert.equal(busy(), false);
  await refreshQuietly();
  assert.equal(refreshed, 1);
})();
""")


def test_an_editable_draft_refreshes_the_list_but_never_reopens_the_detail() -> None:
    _run(r"""
const pane = {scrollTop:0,contains(){return false;},querySelector(){return null;},
  querySelectorAll(){return [
  {readOnly:true,value:'Refine the selected card'}, {readOnly:false,value:'Unsaved comment'}];}};
const document = {hidden:false,activeElement:null,getElementById(){return pane;},
  body:{classList:{contains(){return true;}}}};
let refreshed = 0;
let detailReads = 0;
Object.defineProperty(state, 'current', {get(){detailReads++; return {record:'x-1'};}});
const load = async () => {refreshed++;};
const toast = (message) => { throw new Error(message); };
(async()=>{
  assert.equal(busy(), true);
  await refreshQuietly();
  assert.equal(refreshed, 1);
  assert.equal(detailReads, 0);
})();
""")


def test_a_stale_kit_refusal_shows_a_banner_until_the_server_answers_again() -> None:
    _run(r"""
const node = { hidden: true, textContent: '' };
assert.equal(kitBanner(node, 503, 'the kit files changed on disk since this server started'), true);
assert.equal(node.hidden, false);
assert.match(node.textContent, /kit files changed/);
assert.equal(kitBanner(node, 422, 'an ordinary refusal'), false);
assert.equal(node.hidden, true);
assert.equal(kitBanner(node, 503, 'the kit files changed again'), true);
assert.equal(kitBanner(node, 200, undefined), false);
assert.equal(node.hidden, true);
assert.equal(node.textContent, '');
""")


def test_conversation_history_is_collapsed_but_all_comments_remain_accessible() -> None:
    _run(r"""
const block = commentsBlock({comment_log:[
  {text:'First decision',writer:'alice',at:'2026-10-26T10:00:00Z'},
  {text:'Latest evidence',writer:'bob',at:'2026-10-26T11:00:00Z'}]});
const history = find(block, node => node.tag === 'details');
assert.ok(history);
assert.ok(!history.open);
assert.match(history.text, /First decision/);
assert.match(history.text, /Latest evidence/);
assert.match(history.text, /alice/);
assert.match(history.text, /bob/);
const draft = find(block, node => node.tag === 'textarea');
assert.ok(!find(history, node => node === draft));
draft.value = 'A new decision';
find(block, node => node.tag === 'button' && node.text === 'Comment').onclick();
assert.deepEqual(sent, {action:'comments',body:{text:'A new decision'}});
""")


def test_context_entry_is_plain_prose_without_rewriting_unchanged_legacy_cards() -> None:
    _run(r"""
assert.equal(contextDescription('A confusing list needs clear state.'),
  '## Context\n\nA confusing list needs clear state.');
assert.equal(contextDescription('A clear card.', '## Context\nA clear card.'),
  '## Context\nA clear card.');
const legacy = 'When work starts, I want its owner, so I can follow it.';
assert.equal(contextDescription(legacy, legacy), legacy);
""")


def test_context_is_visible_while_supporting_details_stay_accessible() -> None:
    _run(r"""
const parts = contextParts('## Trigger\nA reader needs a short card.'
  + '\n\n## Scope\nExact files\n\n## Reason\nSupporting evidence');
assert.equal(parts.context, 'A reader needs a short card.');
assert.match(parts.details, /Exact files/);
assert.match(parts.details, /Supporting evidence/);
assert.deepEqual(contextParts('A plain explanation.'), {context:'A plain explanation.',details:''});
""")
