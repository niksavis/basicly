# Session state in herdr

Status: design draft 2, public form, 2026-11-17. Tracker: the "Session state" tasks under `basicly-qpr2xmr.3`.
Draft 1 was reviewed privately by a multi-lens review; this draft answers its findings.

## 1. Problem

A person who runs Claude Code, Codex CLI and GitHub Copilot CLI sessions in herdr tabs must see, without opening a tab, whether each session works, needs an answer, or has finished.

## 2. herdr

- herdr is a terminal runtime for coding agents. Licence: Apache License 2.0 (GitHub `herdrdev/herdr`, read 2026-11-17). It is widely used: about 51,000 stars and daily commits at that date. Measured version: 0.9.5.
- herdr classifies every agent pane as `idle`, `working`, `blocked`, `done` or `unknown` from the screen, by versioned rules (`herdr agent explain <pane>` names the rule). `blocked` means herdr recognized an approval or question; `done` means a turn ended and nobody has looked at the pane yet; `unknown` means an agent is present but not classified.
- `herdr pane report-metadata <pane> --source <id> --state-label <status>=<text> --ttl-ms <n> --seq <n>` stores a display-only label per state and takes no lifecycle authority (herdr 0.9.5 `--help`). The pane id comes first. Labels are capped at 80 characters, the time to live is 1 to 86,400,000 ms, and `seq` must increase across restarts (herdr documentation).
- Measured 2026-11-17 and 2026-11-18 on the measuring pane (probe S2, `session-state-probes/s2-idle-done.py`): a report left `agent_status` unchanged in the states `working`, `done` and `idle`; `herdr pane get` showed the label; a clear removed only the labels of its own `--source`. Labels of several sources coexist, and for one state the latest report shows. On 2026-11-18 a Copilot pane held at its folder trust dialog stayed `blocked` while a label was set and cleared, and the dialog stayed unanswered. One report took 1.6 to 3.9 ms (probe S5).
- Measured 2026-11-18 with the reporter installed: when a Claude Code turn ended while one background shell ran, the Stop hook set `paused: 1 background` on `idle` and `done` (probe S4, partial).
- herdr's vendor integrations bind the session id at session start; the screen decides the state.

## 3. What the person sees

herdr's own badge and state colour stay the primary signal. The reporter adds one short text per state where herdr shows state labels.

| herdr state | Meaning | Label |
| --- | --- | --- |
| working | a turn runs | none |
| blocked | needs the person now | `needs you: Bash git` |
| done | turn ended, not yet seen | `paused: 2 background`, or none |
| idle | turn ended, seen | `paused: 2 background`, or none |
| unknown | herdr cannot tell | none; read it as "check this tab" |

- "paused" exists only for Claude Code, from a Stop that lists background work until the next Stop or prompt.
- Where the labels show in herdr's tab list and pane frame is pending probe S1. If they show in neither, the label moves to the pane title and the rest of the design stays.
- The label names the tool and the first word of its command only. The tab list is visible to anyone at the screen, so arguments, paths and tokens never enter a label.

## 4. Reporter behaviour

| Claude Code hook | Action |
| --- | --- |
| SessionStart | clear the labels |
| UserPromptSubmit | clear the labels |
| PermissionRequest | `blocked` label `needs you: <tool> <first word>`, time to live 10 minutes |
| PostToolUse | clear the labels (the prompt was answered) |
| Stop | with `background_tasks` or `session_crons` listed: `idle` and `done` labels `paused: <n> background`, time to live 30 minutes; otherwise clear |

- `--seq` is the current time in nanoseconds. No counter file.
- Each herdr call has a 1-second timeout. Any error ends the hook with exit code 0, so a hook never slows or blocks a turn.
- The reporter writes nothing to standard output, so it can never emit a permission decision.
- The pane id is taken from `HERDR_PANE_ID` and must match a strict pattern. The herdr binary is `HERDR_BIN_PATH` when it is an absolute executable, else `herdr` on the path.
- Without herdr (`HERDR_ENV` unset) the reporter does nothing.
- A hook run takes about 25 ms with a stub herdr, 30 ms at most over 100 runs (probe S5, `session-state-probes/s5-bench.py`).
- Codex and Copilot: a `blocked` label from their permission hooks, after probe S3 shows their prompts. No paused label, because their stop hooks list no background work.

## 5. Known gaps

- Interrupting a turn ends it without a Stop hook: an old paused label can remain until its time to live or the next prompt.
- Background work that ends between turns does not clear the paused label until the next Stop, the next prompt or the 30-minute time to live. Whether a background completion wakes the session is pending probe S4.
- Prompts herdr does not recognize show no needs-you label; herdr's own detection is the only signal there. Which prompts it recognizes for each vendor is pending probe S3.
- An automatic-mode classifier refusal happens before PermissionRequest and shows nothing.

## 6. Where it lives

- Kit `.basicly/core/kit/orch/`, module `orch_state`: `reporter.py` (hook entry point, command `orch-state-hook`) and `herdr.py` (the herdr adapter), packaged as `packages/basicly-orch`. It runs herdr only through `.basicly/core/kit/orch/runner.py`, the one orch module that the kit-boundary gate lets start a process (owner approval 2026-11-19).
- herdr is an optional adapter of the package. Without herdr there is no working or idle view; the orchestrator kit's `orch who` shows mail and liveness only.
- Installation: separate hook entries beside herdr's own hook file, never inside it, because herdr rewrites its file on update. Before a basicly release carries the package, it is installed as a uv tool, and a person sees and approves the exact settings diff before it is written.

## 7. Probes

| Id | Question | State |
| --- | --- | --- |
| S1 | Where do state labels show in herdr's tab list and pane frame, per state? | open, with a person |
| S2 | Does a report leave `agent_status` unchanged in every state? | passed for working, done, idle and blocked |
| S3 | Does herdr show blocked for each prompt kind of each vendor? | open, with a person |
| S4 | Do background shells and monitors appear in Stop `background_tasks`, and does their completion fire a hook? | a background shell appears; monitors and the wake open |
| S5 | Does a hook run stay under 50 ms? | passed, about 25 ms |
| S6 | Does herdr alert (sound, OS notice, tab highlight) when a pane turns blocked? If not, the reporter sends one OS notice at PermissionRequest | open, with a person |

## 8. Failure modes

- herdr's rules lag a vendor UI change: the state reads unknown, not false.
- A paused label outlives the work: bounded by the next Stop, the next prompt or the 30-minute time to live.
- A needs-you label outlives the answer: cleared at PostToolUse, bounded by the 10-minute time to live.
- No herdr: the reporter does nothing.
