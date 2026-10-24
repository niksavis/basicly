#!/bin/sh
set -eu

REPO_URL="https://github.com/niksavis/basicly"
REF="latest"

fail() {
    printf 'bootstrap: %s\n' "$*" >&2
    exit 1
}

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
    PATH="${UV_INSTALL_DIR:-$HOME/.local/bin}:$PATH"
    export PATH
    command -v uv >/dev/null 2>&1 \
        || fail "uv was installed but is not on PATH; open a new shell and re-run"
fi

if [ "$REF" = latest ]; then
    command -v curl >/dev/null 2>&1 || fail "curl is required to resolve the latest release"
    release_url=$(curl -fsSL -o /dev/null -w '%{url_effective}' "$REPO_URL/releases/latest")
    REF=${release_url##*/}
fi
printf '%s\n' "$REF" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    || fail "--ref must name a release such as v0.27.0; source branches require artifact preparation"
version=${REF#v}
wheel="$REPO_URL/releases/download/$REF/basicly-$version-py3-none-any.whl"
printf 'bootstrap: installing basicly@%s\n' "$REF"
exec uv tool run --from "$wheel" basicly install "$@"
