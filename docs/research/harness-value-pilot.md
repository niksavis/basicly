# Repository harness value pilot

This pilot does not establish basicly's overall value.
Every arm accepted the documentation change.
Full-basicly used more reported tokens and took longer on this prepared task.
Monetary cost remains unknown.

## Measured results

The evaluator command below measured these results.
Each arm ran the same prepared documentation task with the same independent acceptance checks.
The sample contains three interleaved repetitions per arm.
The JSON report retains the exact values and hashes.

| Arm | Accepted attempts | Median task time |
| --- | --- | --- |
| Plain | 3/3 | 21.7 s |
| Gates only | 3/3 | 27.8 s |
| Full basicly | 3/3 | 48.0 s |

| Arm | Mean tokens per accepted task | Median setup time |
| --- | --- | --- |
| Plain | 96,079 | 0.9 s |
| Gates only | 188,597 | 3.3 s |
| Full basicly | 246,972 | 3.3 s |

| Arm | Mean reported cache-read tokens | Observed maximum task time |
| --- | --- | --- |
| Plain | 86,315 | 21.9 s |
| Gates only | 163,029 | 30.5 s |
| Full basicly | 218,496 | 51.8 s |

Task time includes native execution, loading observation and independent acceptance.
Acceptance includes the normal Git commit.
Setup time includes fixture preparation and any harness installation.
The report keeps those measurements separate.
Reported tokens include input and output.
Cache counters are separate fields and must not be added again to that total.
A second calculation used the final token counters from each native session trace.
It matched every attempt's reported total.

## Conditions

Measured runtime versions were basicly 0.38.0 and Codex CLI 0.166.0.
The committed high tier resolved to `gpt-5.6-sol`.
The evaluator pinned medium reasoning effort.
Native traces confirmed the effective model and effort.
They also confirmed the same workspace sandbox, disabled network access and never-approve policy.

The common task came from the existing `docs-only` scenario.
The agent corrected the default timeout and flag in `README.md`.
The grader checked the text and preserved `tool.py`.
Every arm started from the same Git commit and task file hashes.
The installed arms used identical verification settings and Git hook hashes.
The prepared ledger supplied an open task to both installed arms.
The configured Python check compiled `tool.py`.
The ordinary installed hooks remained active.

Native traces showed no automatic project guidance in the plain and gates-only arms.
Full-basicly loaded its complete instruction file and 24 project skills.
These counts were measured from messages before the first assistant response.
The loading check supports direct skill paths and the host's skill-root aliases.

The host prevented native writes to Git metadata.
The task brief therefore told every agent that the evaluator would commit its output.
The grader staged the documentation and any prepared ledger through normal Git commands.
It did not bypass hooks.
This protocol measures prepared work with that finish step.
It does not measure autonomous intake, closure or publication.

## Limits and next action

The task was small and had no seeded implementation defect.
The sample cannot establish reliability on harder tasks or a stable tail latency.
Personal host settings and remote policy remain partly unverified.
An agent can still discover installed files through a tool.
Automatic loading checks do not prove that no such discovery occurred.
The report does not contain complete counts of provider requests, retries or human interventions.

The initial control used an invalid verification command and an unsupported tracker flag.
It also asked the native agent to commit despite the host restriction.
Those attempts remain separate local evidence.
A repaired control also remains separate from this frozen pilot.
The reported batch includes every attempt within that batch.
It contains no repairs or replacement samples.

The CLI reported no monetary costs.
Cost per accepted task therefore remains unknown in every arm.
Unknown values are not zero.
Token totals alone cannot establish subscription charges or API-equivalent cost.

Proceed with the user's scoped local verification policy.
Keep the full suite in continuous integration.
Use this fixture as an initial measurement control before further changes.
Expand the existing scenarios and hosts before removing a workflow path.
This recommendation can fail if local file selection misses a related defect.
Seeded defect controls and the independent CI suite must retain that protection.

## Evidence

The [JSON report](harness-value-pilot.json) contains the schedule, input hashes, source hashes and per-attempt measurements.
The [evaluator guide](../design/harness-baseline-trials.md#compare-repository-harness-use) explains the comparison modes.
Raw transcripts remain local because they contain private host details.
The broader baseline story remains open.
The frozen evaluator hashes identify the measured snapshot.
The release includes a later type annotation correction and separates the loading checks.
Those changes retain the measured runtime validation.

The measured command used frozen snapshots and a private fixture path.
The following command shows its portable argument form.
The environment variables name those frozen inputs and an output directory outside repository instruction trees.

```sh
uv run python .scripts/eval_harness_trials.py \
  --family codex --model "$RESOLVED_HIGH_TIER_MODEL" --effort medium \
  --cases "$PILOT_CASES" \
  --arm "plain=$PLAIN_SNAPSHOT" \
  --arm "gates-only=$GATES_SNAPSHOT" \
  --arm "full-basicly=$FULL_SNAPSHOT" \
  --repetitions 3 --timeout 180 --output "$PILOT_OUTPUT"
```

The JSON records the measured model resolution.
Use the committed tier resolver when selecting a later model.
A different resolution creates a new cohort.
The repetition count and timeout above are assumed pilot limits.
