# T3 Code: what basicly and the meta harness learn from it

Checked on 2026-11-19 against <https://github.com/pingdotgg/t3code> at commit `c5badf4e408ef55d1352040e2fb557d8f36d411a` (main, 2026-11-19) and release `v0.1.10` (2026-11-17). Short form below: `c5badf4e`.
Record: basicly-upnhikm. The meta harness is basicly-qpr2xmr. Its requirements version 4 are the first comments on that record.

## Result

- Keep decision D2 of the meta harness: "T3 Code is a reference, not a dependency."
  T3 Code conflicts with three requirements that decide whether to build or to adopt (table below): the terminal-first goal, R5 memory and R8 sessions.
  It is a graphical interface. It keeps its own SQLite store, so the agents share no memory. It keeps sessions in its own server, not in herdr panes.
  Its Claude path uses the Claude Agent SDK. Whether the SDK may run on a subscription login is not established.
- Adopt three patterns into the meta harness requirements (R1, and R2 or R7 for the ceiling): capability flags that the code reads, replay fixtures recorded from the real vendor client, and a permission ceiling that refuses a turn by name.
- Refuse two T3 Code defaults: the ChatGPT OAuth broker for Codex, and "Full access" as the first permission mode.

## Fit against the meta harness requirements

| Requirement | Fit | Evidence |
| --- | --- | --- |
| Goal: terminal-first | conflicts | E4, E15 |
| R0 legal, Codex | credentials fit with `codex login`; vendor permission not established | E10 |
| R0 legal, Claude | not established | E2 |
| R1 adapters | fits | E12 |
| R2 isolation | not established | E4 |
| R5 memory | conflicts | E5, E14 |
| R6 messaging | fits in part | E8 |
| R7 approvals | fits, except the default mode | E6, E7, E16 |
| R8 sessions under herdr | conflicts | E4, E5 |
| R11 daily use from run records | not established | E5 |

- **Goal.** The `t3` command starts the server and opens the web app (`README.md:56`). `AGENTS.md:3` calls T3 Code "a minimal GUI".
- **R0, Claude.** Anthropic text forbids third-party plan routing "on behalf of their users". This quote comes from requirements v3 on basicly-qpr2xmr (2026-11-04). It was not re-read for this note. Whether a personal Agent SDK session on a subscription falls under that text is open. Record basicly-qpr2xmr.2 owns this question.
- **R2.** The server owns the agent processes. No boundary like bubblewrap was found in the files read. The search was manual and covered only the files named in this note.
- **R8.** T3 Code keeps sessions in its own server. The meta harness plan keeps each engine in a herdr pane with a resume command.
- **R11.** T3 Code writes no basicly run records, so the 14-day count cannot read its sessions.

## What T3 Code is

| Fact | Value | Source |
| --- | --- | --- |
| Licence | MIT, T3 Tools Inc. | `LICENSE` |
| Created | 2026-02-08 | GitHub API `repos/pingdotgg/t3code` |
| Stars | 66,270 | GitHub API, 2026-11-19 |
| Contributors | about 479 | GitHub API pagination header |
| Latest stable | v0.1.10, 2026-11-17 | `gh release list` |
| Nightly releases | 11 from 2026-11-17 to 2026-11-19 | `gh release list` |
| Stack | TypeScript, Effect, node-pty | `apps/server/package.json` |
| Store | SQLite, `userdata/statev2.sqlite` | `apps/server/src/serviceLauncher.ts:1024` |
| Clients | desktop (Electron), web, iOS, Android | `README.md`, `AGENTS.md` |
| Agent drivers | 10, plus a generic ACP registry driver | `apps/server/src/provider/builtInDrivers.ts` |

The README says: "T3 Code does not resell tokens. It drives the agents you are already signed in to."

## Evidence

"Source" means read in the code at `c5badf4e`. "Docs" means read in the repository's `docs/` folder at the same commit. The vendor protocol documents were not read. A fresh researcher re-checked E1, E2, E3, E5, E6, E7, E8, E10, E11, E12 and E13 at the same commit.

| # | Claim | Evidence | Rung |
| --- | --- | --- | --- |
| E1 | Codex runs through `codex app-server` over stdio JSON-RPC. The client types are generated from the schema. | `apps/server/src/provider/codexLaunchArgs.ts:14`, `packages/effect-codex-app-server/src/client.ts` | source |
| E2 | Claude runs through the Claude Agent SDK (`@anthropic-ai/claude-agent-sdk` ^0.3.321), with `resume`, `sessionId` and a `canUseTool` callback. | `apps/server/package.json`, `apps/server/src/orchestration-v2/Adapters/ClaudeAdapterV2.ts:67` | source |
| E3 | Copilot has no driver of its own. It runs through the generic Agent Client Protocol (ACP) registry adapter. | `builtInDrivers.ts`, `packages/provider-acp-registry/src/server/copilotAcp.ts` | source |
| E4 | The server owns the agent processes, terminals, Git and files. The clients use an authenticated RPC. | `docs/internals/overview.md`, `packages/contracts/src/rpc.ts` | docs |
| E5 | State is event-sourced. One SQLite transaction commits the events, the projections, the command receipt and the outbox. A worker runs the side effects after the commit. | `docs/internals/overview.md:50-51`, `apps/server/src/orchestration-v2/EventSink.ts` | docs and source |
| E6 | An approval request goes from the adapter to the user, and `respondToRuntimeRequest` returns the answer. The provider's own option ids pass through unchanged. | `RuntimeRequestService.ts:118`, `docs/internals/adding-a-provider.md` | source and docs |
| E7 | The initial default permission mode is "Full access". | `docs/user/permission-modes.md:10` | docs |
| E8 | An agent can start a sub-agent on any provider through an MCP server that the app owns. The child gets only its task prompt, not the parent history. | `docs/orchestration-v2/orchestrator-mcp-server.md:20-21` | docs |
| E9 | Each thread can work in its own Git worktree, with a diff review per turn and checkpoints. | `README.md`, `ThreadLaunchService.ts`, `apps/server/src/checkpointing` | docs and source |
| E10 | Codex has two login modes: the user's own `codex login`, or "Connect with ChatGPT". The second mode runs an OAuth flow, stores the tokens and serves them over a local socket. | `docs/user/providers-codex.md`, `CodexChatGptAuth.ts`, `CodexManagedTokenBroker.ts`, `ProviderCredentialStore.ts` | source and docs |
| E11 | Claude uses the login and configuration of Claude Code. A second account uses `CLAUDE_CONFIG_DIR`. | `docs/user/providers-claude.md` | docs |
| E12 | An adapter implements `ProviderAdapterV2`: ensure, resume, start, steer and interrupt a turn, answer a request, read a snapshot, roll back, fork, and an event stream. It reports capability flags. Orchestration reads the flags, never the driver kind. | `packages/provider-core/src/server/ProviderAdapter.ts:729-820`, `docs/internals/adding-a-provider.md:20` | source and docs |
| E13 | Adapter tests replay fixtures that were recorded from the real provider. Eight recording scripts exist. | `apps/server/scripts/record-*-replay-fixture.ts`, `testkit/fixtures` | source |
| E14 | No memory that the agents share was found. Continuity comes from thread history and portable handoff files. | `docs/user/portable-handoffs.md` | docs |
| E15 | T3 Code adds its own runtime instructions to Codex and dispatches Claude skills. No project rules catalog was found. | `CodexDeveloperInstructions.ts`, `Drivers/ClaudeSkillDispatch.ts` | source |
| E16 | A runtime policy sets a permission ceiling. The orchestrator refuses a dispatched turn above the ceiling. The policy also catches a run resumed after a restart on a thread whose mode predates the policy. | `apps/server/src/orchestration-v2/RuntimePolicy.ts:111` (`RuntimePolicyAboveCeilingError`) | source |

E14 and E15 are absence claims from a manual search of the files named. They are not proof.

## What to adopt

| # | Pattern | Where | Effect |
| --- | --- | --- | --- |
| Y1 | Capability flags that the code reads (E12) | requirement R1 | R1 already declares the unsupported operations. With flags, the code refuses a missing operation by name. |
| Y2 | Replay fixtures recorded from the real client (E13) | the R1 contract test | Replay tests use no subscription. They supplement the nightly live test and do not replace it. |
| Y3 | A permission ceiling that refuses by name (E16) | requirements R2 and R7 | A session cannot start above the ceiling, so nobody has to remember to set a safe mode. |

T3 Code also gives a child session only its task prompt (E8). basicly already plans this as concept A20 in `docs/research/deepseek-harness-plugin-review.md` (record basicly-oo60vym), so this note adds nothing to it.

## What not to adopt

| # | Pattern | Reason |
| --- | --- | --- |
| N1 | The ChatGPT OAuth broker (E10) | R0 says the meta harness never brokers or stores a vendor credential. |
| N2 | "Full access" as the first default (E7) | basicly defaults to safety. Y3 makes the safe mode a refusal, not a setting. |

## Not established

- Whether the Claude Agent SDK may run on a Claude subscription login. The vendor terms were not read.
- How the Codex adapter resumes a thread after a restart. The adapter was not read.
- What T3 Connect, the remote relay, sends through T3 servers. It was not read.
- Whether T3 Code can run under a bubblewrap boundary.
- The vendor protocol documents for Codex app-server, the Agent SDK and ACP. All protocol claims above are T3 Code's own use, read in its code.
- The stable release cadence before v0.1.10.
