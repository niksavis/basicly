# Lens sets by artifact

Use the set for the artifact type. Add one lens for a known risk, up to the swarm cap of 6. Take each rubric from [the swarm lenses](../../swarm/references/lenses.md).

| Artifact | Lenses |
| --- | --- |
| diff or code | correctness, security, tests, simplicity, poka-yoke |
| plan | feasibility, spec, recovery, poka-yoke, facts |
| design | architecture, ux, poka-yoke, recovery, simplicity |
| skill or instruction | spec, wording, poka-yoke, facts, simplicity |
| document | facts, wording, spec |

For a diff, also run the host's security review when one exists, such as `/security-review` in Claude Code.
