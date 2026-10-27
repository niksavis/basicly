# Task context delivery

Declared role and work-unit skills now travel as a context manifest rather than
pasted skill bodies. Each entry records its declaration reason, host projection,
UTF-8 byte count, SHA256 digest and retrieval argv. The manifest explicitly marks
these bodies deferred. Codex prefers `.agents/skills`; other runners prefer
`.claude/skills`, with the existing alternate projection as a fallback.

`basicly skill-read <projected-path> --sha256 <digest>` returns the exact UTF-8
entry only when its bytes match. Missing, changed, malformed and out-of-checkout
references fail with a recovery instruction. Paths are restricted to projected
skill entries; the command is not an arbitrary file reader. A reference contains
an argv array so callers do not interpolate a path into shell syntax.

The dispatch asks the owner to retrieve applicable procedures before using them,
then follow their selective references. Required guidance that cannot be retrieved
blocks work. An index is never described as already delivered instructions.

Task descriptions, acceptance, requirements and comments retain their existing
complete delivery. Historical comments can contain active corrections, so this
change does not blindly truncate them. Oversized task envelopes still deliver the
full contract. Further progressive delivery needs a proven current-facts contract
before moving historical clarification out of immediate context.

The manifest measures engine-delivered references and source bytes, not actual
model tokens, cache hits or guidance independently injected by a host. Claude
role frontmatter may still preload declared skills. This change removes the
engine's duplicate paste; it does not claim to suppress native host loading.
Native activation, retrieval, task quality and acquisition-cost trials remain
acceptance work for the existing redesign children.
