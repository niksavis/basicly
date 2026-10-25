# Lefthook distribution

The manifest pins upstream Lefthook 2.2.2, its MIT licence, source and archives.
Linux and macOS use the official archives without changes.

Windows uses `basicly-windows.1`. The source is the pinned upstream archive plus
`windows.patch`. The patch replaces the raw Windows `CmdLine` override with Go's
argument-list API. Lefthook still expands placeholders and applies its own job filters.
The patch preserves literal arguments and expanded filenames without rewriting user
configuration. The verbose version includes the patch revision.

The patched Windows archives are vendored under `windows/`. The manifest records their
SHA-256 hashes and retains the original upstream URLs and hashes for provenance.
Distributions keep every archive under the existing `2.2.2/` layout and exclude duplicate
Git copies. Rebuilding from a source distribution uses its prepared archives.
Normal artifact preparation copies these files and verifies them. Wheel and source
builds never download or compile implicitly. Consumers need neither Go nor a global
Lefthook installation.

To reproduce both Windows architectures, prepare the upstream source archive first,
then run from the repository root:

```sh
uv run python .scripts/build_lefthook_windows.py --out /tmp/lefthook-rebuild --check
```

The rebuild requires Git and Go. It selects the pinned Go toolchain through `GOTOOLCHAIN`;
Go can download that toolchain and the modules pinned by the upstream `go.sum`.
Supply those caches in advance for an offline rebuild. The command verifies the source
and patch hashes, disables CGO and workspace settings, removes build paths and build IDs,
and writes deterministic gzip archives. `--check` compares their hashes and bytes with
the committed pins. A different existing output is refused.

Compatibility CI rebuilds the pins and tests native Windows behavior. Remove this patch
only after an upstream release passes the same literal and placeholder regression tests.
