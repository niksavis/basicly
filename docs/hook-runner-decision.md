# Hook Runner Decision: lefthook vs pre-commit

Decision doc for basicly-3s2p (spike surfaced by basicly-x5gh). Question: is
[lefthook](https://github.com/evilmartians/lefthook) objectively a better fit
than [pre-commit](https://pre-commit.com/) as the hook runner basicly projects
into consumer repos, or does pre-commit stay?

**Recommendation: keep pre-commit.** Rationale and the dimension-by-dimension
comparison follow. This doc does not spawn an implementation bead — the
recommendation is status quo. It records the reconsider triggers instead.

## The framing fact

basicly's hooks are already runner-agnostic. `hooks.yaml` is a tool-neutral
spec list whose header states the fields "stay tool-agnostic so another manager
(e.g. lefthook) can be projected later"; every hook script is standalone Python
with no pre-commit API, kept that way deliberately "so they stay reusable by
lefthook or another hook manager". The only pre-commit-specific code is the
projection layer that renders `.pre-commit-config.yaml` (`src/basicly/hooks.py`).

The decisive consequence: **every projected hook runs `uv run python <script>`.**
uv and Python 3.14+ are a hard requirement for a basicly committer regardless of
which runner orchestrates the hooks. The runner only decides *how the git hook
fires the script* — it does not remove the Python/uv dependency, because the
checks themselves are Python.

That single fact nullifies lefthook's headline advantage (a static Go binary,
"no runtime dependency") for basicly specifically: we cannot get to a
Python-free consumer, so a Python-free runner buys nothing.

## Dimension-by-dimension

| Dimension | pre-commit | lefthook | Edge |
| --- | --- | --- | --- |
| Consumer runtime cost | Python tool, but provisioned through the uv the consumer already has (`uv tool run pre-commit` fallback) — no new toolchain | Static binary, but no uv-native acquisition path; needs npm / go / a release download — a new channel | **pre-commit** |
| Cross-platform (Win/WSL) | Works on Windows/macOS/Linux; hooks are plain `uv run python` strings, no shell syntax | Windows/macOS/Linux binaries exist; equally portable | tie |
| Stage coverage (pre-commit, commit-msg, pre-push) | All three; basicly runs `pre-commit install -t <stage>` per stage | All three natively | tie |
| Projection fit | Invested: ruamel round-trip managed-block rewrite (basicly-wd7u) preserves foreign hooks + comments in `.pre-commit-config.yaml` byte-for-byte | Clean `lefthook.yml` schema (commands grouped by stage), arguably a more natural fit for a pure orchestrator — but the managed-block preservation would need re-implementing for the new schema | **pre-commit** (sunk cost); lefthook conceptually cleaner |
| Speed / parallelism | Serial by default; per-run Python startup, small next to `uv run` cold starts | Parallel by default, fast Go startup | lefthook (marginal here) |
| Ecosystem / maturity | Dominant Python-ecosystem standard; contributors already know it | Mature, popular (JS/Ruby/Go), actively maintained | tie |
| Removes the activation footgun? | No — still needs `pre-commit install`; the fresh-repo seam (basicly-x5gh) is already fixed via the uvx fallback + no-`.git` precheck | No — still needs `lefthook install`, and *adds* a binary-acquisition seam | neither; lefthook slightly worse |

## Why pre-commit stays

1. **No new dependency.** pre-commit rides the uv channel consumers already have
   (`uv tool run pre-commit` auto-provisions it ephemerally). lefthook would add
   a Go-binary acquisition problem with no uv-native answer — trading a solved
   provisioning path for an unsolved one.
2. **lefthook's core advantage does not apply.** "No runtime dependency" is moot
   when the checks are `uv run python`; Python/uv stay required either way.
3. **The activation footgun is already closed** (basicly-x5gh) without switching
   runners. lefthook would not remove activation — it moves it.
4. **Migration is real cost for marginal gain.** The only concrete win is
   parallel hook execution, which is small relative to `uv run` startup and to
   basicly's ordering-sensitive gates (identity-guard, secret-scan).

## Migration cost estimate (if we ever switch)

Bounded but a medium epic, front-loaded on the unknown:

- Re-implement the `hooks.py` projection for `lefthook.yml`, including a
  managed-block round-trip that preserves unmanaged entries (today's
  `markdownlint` hook) — re-earning the basicly-wd7u investment in a new schema.
- Solve binary distribution cross-platform (Win/WSL/macOS/Linux) without adding
  a package manager consumers may lack — the biggest risk item.
- Rewrite `install_hooks` / the `basicly install` activation step and the
  runner-selection fallback.
- Update README, `docs/architecture.md`, and the install flow; re-dogfood every
  gate; run a deprecation window for consumers on the old config.

## Reconsider triggers

Revisit this decision if any of these change:

- Consumers stop reliably having uv on PATH (the assumption pre-commit's
  provisioning rides on).
- basicly drops the Python/uv requirement for the checks themselves — then a
  Python-free runner would actually buy something.
- Hook execution speed becomes a real, measured complaint that parallelism would
  fix.
- pre-commit's provisioning seam regresses in a way the uvx fallback cannot
  cover.

Until then the runner-agnostic seam (`hooks.yaml` `manager` field, API-free
scripts) is kept precisely so this stays a cheap decision to reopen.
