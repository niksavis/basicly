---
id: git-discipline
description: How to commit safely in a hook-gated repo.
category: commands
priority: high
applies_to: [all]
tags: [git, commits, hooks]
status: active
title: Git Discipline
---

- Run `git commit` as its own command; never chain state-dependent follow-ups (issue-tracker updates, tagging, `git push`) after it on one line — a hook rejection leaves the chain half-run.
- Commits are gated: messages must be Conventional Commits with a trailing beads issue id (`commit-msg` hooks enforce both) — use the `conventional-commits` skill to format one, and the `tool-br` skill to claim the issue first.
- When a hook rejects a commit, fix the reported cause and re-commit; do not reword to dodge the check.
