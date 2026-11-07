# Scoping a child

## Find the producer

A scope says where a wrong value is produced. The file that shows the value is usually a different module.
Find the producer before dispatch:

```sh
rg -n '<the literal the defect prints>' <source-dir>
rg -n 'def <the function whose result is wrong>' <source-dir>
```

The file that emits the value is the scope. A renderer usually sits one call above the builder that owns the fact.
Then ask: does this value have more than one consumer, or reach more than one surface?
A projected artifact often has several outputs.
`basicly skills-build` writes `.claude/skills` and `.agents/skills`, and a scope that names only one fails the projection gate.

## Name the child's own file

Name the file that the child creates:

| Output | Scope entry |
| --- | --- |
| changelog fragment | `changelog.d/<epic-id>.<n>.<category>.md` |
| config fragment | `basicly.d/<epic-id>.<n>.toml` |
| projected skill | `.claude/skills/<name>/SKILL.md` |

`<n>` is the child's position in the plan, because children get the ids `<epic-id>.1`, `<epic-id>.2`, and so on.
A glob such as `changelog.d/*.md` or `.claude/skills/**` collides with every other child that matches it.
If a glob matches more than a few files, say why in the plan.
