---
id: non-interactive-shell
description: Avoid shell commands that hang waiting on interactive confirmation.
category: tools
priority: medium
applies_to: [all]
tags: [shell, tooling]
status: active
---

- Prefer cross-platform implementations over shell-specific behavior when a choice exists.
- Use non-interactive flags for operations that can hang on a confirmation prompt (`cp -f`, `mv -f`, `rm -f`, package-manager `-y`/`--yes`, `ssh -o BatchMode=yes`) — some environments alias these commands into interactive mode by default.
