- **A `PreToolUse` guard refuses a for-loop over an unsplit scalar.** zsh does not word-split an
  unquoted scalar, so `V="a b c"; for x in $V` runs the body once with the whole string and exits
  0 — writing nothing while reading as success. The guard blocks that shape at tool time and names
  the variable. It matches only an assignment and its loop in the same command, which is complete
  because shell state does not persist between tool calls, and leaves arrays, inline lists, quoted
  expansions and command substitution alone.
