# Report format

Each agent's reply ends with one json block. Save each reply as `DIR/LENS.md`, then run the script.

| Field | Used by | Meaning |
| --- | --- | --- |
| lens | all | The lens name; equals the file name. |
| verdict | swarm, teardown | pass or fail on the lens rubric. |
| findings | all | A list of severity, claim, evidence, fix. |
| grades | hill-climb | One number from 0 to 10 for every property of the climb. |
| vote, reason, strongest_counter | council | The chosen option, why, and the best case against it. |

The script rules:

- A missing file is "not run". A file with no json block is "malformed". Both make `check` exit 1.
- Evidence counts as verified when it holds a `path:line`, `ran:`, or a dollar sign and a space, at the start or after a space.
- A `verdict` other than pass or fail is malformed.
- A group heading shows the highest severity; each claim under it keeps its own.
- Findings with the same `path:line` are grouped. The group keeps every claim, lists every lens that found one, and takes the highest severity. Agreement of two lenses raises confidence.
- Command evidence (a dollar prompt or `ran:`) verifies a finding but never groups it, because two commands rarely point at one defect.
- The `verdict` column shows each lens's pass or fail.
