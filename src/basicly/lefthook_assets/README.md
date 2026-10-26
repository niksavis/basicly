# Lefthook distribution

`manifest.json` pins upstream Lefthook 2.2.2. Every supported platform uses the official
release archive from `github.com/evilmartians/lefthook`, without a change. This directory
holds the pin, the MIT licence and this page. It holds no executable and no archive, and
nothing here is compiled.

Each manifest entry carries the upstream HTTPS URL and the SHA-256 hash that upstream
publishes in `lefthook_checksums.txt`. `lefthook_artifacts.py` downloads every entry into
the ignored cache `build/lefthook/2.2.2/` and verifies each hash before any build or
install reads it. A hash that disagrees refuses the preparation and keeps the previously
prepared cache. Preparation is explicit:

```sh
uv run python src/basicly/lefthook_artifacts.py --download
uv run python src/basicly/lefthook_artifacts.py --from-dir <directory>
```

Wheel and source builds never download. They read the prepared cache and refuse when it is
missing, so a consumer install needs no network and no global Lefthook.

## Windows argument handling

Official Lefthook 2.2.2 runs a job on Windows as the raw command line
`"sh" -c "<job>"`. Windows removes a double quote inside `<job>` before `sh` sees it. Two
consequences follow, and both are upstream behavior that a global Lefthook install has
too:

- A job whose `run` string contains a literal double quote reaches `sh` with that quote
  removed.
- `{staged_files}` and `{all_files}` escape a filename that holds an apostrophe as
  `'name'"'"'s.py'`. The embedded double quotes are removed, so `sh` reports a syntax
  error and the hook fails.

basicly's own managed job is not exposed to this. It uses no filename placeholder, so no
filename reaches the command line. It carries its specification in the environment, which
Windows passes through unchanged, and `lefthook-runtime.py` reads the staged file list
from `git diff --cached -z` itself. `tests/test_hook_compatibility.py` measures both
routes on native Windows.

To change the pinned version, replace the version, the URLs and the hashes in
`manifest.json` from the upstream release and its `lefthook_checksums.txt`, then run the
download command again.
