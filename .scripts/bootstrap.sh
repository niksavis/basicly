#!/bin/sh
# Bootstrap basicly into the current repo without a pre-installed uv/Python.
#
# Usage (from the consumer repo root):
#   curl -fsSL https://raw.githubusercontent.com/niksavis/basicly/main/.scripts/bootstrap.sh | sh
#   curl -fsSL .../bootstrap.sh | sh -s -- --ref v0.3.1 --technologies python,zsh
#
# --ref pins the basicly version (default: main); every other argument passes
# through to `basicly install`. Windows users: see bootstrap.ps1.
set -eu

REPO_URL="https://github.com/niksavis/basicly"
REF="main"

fail() {
    printf 'bootstrap: %s\n' "$*" >&2
    exit 1
}

# Consume --ref; rotate everything else back into "$@" for basicly install.
remaining=$#
while [ "$remaining" -gt 0 ]; do
    arg=$1
    shift
    remaining=$((remaining - 1))
    case "$arg" in
        --ref)
            [ "$remaining" -gt 0 ] || fail "--ref needs a value"
            REF=$1
            shift
            remaining=$((remaining - 1))
            ;;
        --ref=*)
            REF="${arg#--ref=}"
            ;;
        *)
            set -- "$@" "$arg"
            ;;
    esac
done

command -v git >/dev/null 2>&1 || fail "git is required"
git rev-parse --git-dir >/dev/null 2>&1 \
    || fail "run this from inside the consumer git repository"

if ! command -v uv >/dev/null 2>&1; then
    command -v curl >/dev/null 2>&1 || fail "curl is required to install uv"
    printf 'bootstrap: uv not found; installing it from astral.sh\n'
    curl -fsSL https://astral.sh/uv/install.sh | sh
    # The installer defaults to ~/.local/bin (or $UV_INSTALL_DIR); make sure
    # this same run can see the fresh binary.
    PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
    export PATH
    command -v uv >/dev/null 2>&1 \
        || fail "uv was installed but is not on PATH; open a new shell and re-run"
fi

printf 'bootstrap: installing basicly@%s\n' "$REF"
exec uv tool run --from "git+${REPO_URL}@${REF}" basicly install "$@"
