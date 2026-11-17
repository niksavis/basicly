#!/usr/bin/env bash
set -euo pipefail

usage() {
  printf 'usage: MAIL_SHELL_TOOL=<shell mail script> %s --check send-read|locks|state\n' "$0" >&2
  exit 2
}

[[ $# -eq 2 && $1 == --check ]] || usage
check="$2"
case "$check" in send-read | locks | state) ;; *) usage ;; esac
shell_tool="${MAIL_SHELL_TOOL:-}"
if [[ -z $shell_tool || ! -f $shell_tool ]]; then
  printf 'refused: set MAIL_SHELL_TOOL to the shell mail script to compare\n' >&2
  exit 2
fi
repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
py_dir="$repo/.agents/skills/agent-mail/scripts"
[[ -f $py_dir/agent_mail.py ]] || { printf 'refused: %s has no agent_mail.py\n' "$py_dir" >&2; exit 2; }

work="$(mktemp -d)"
trap 'find "$work" -depth -delete' EXIT
cp -f "$shell_tool" "$work/mail"
chmod +x "$work/mail"
mkdir -p "$work/home"
python3 "$py_dir/agent_mail.py" init "$work/home" >/dev/null
export ORCH_MAIL_ROOT="$work/home/.orchestrator"

B() { "$work/mail" "$@" </dev/null; }
P() { python3 "$py_dir/agent_mail.py" "$@" </dev/null; }
has() { local out; out="$("${@:2}" 2>&1)" || true; [[ $out == *"$1"* ]]; }
fail() { printf '%s failed: %s\n' "$check" "$1" >&2; exit 1; }
expect_rc() {
  local want="$1"; shift
  local rc=0
  "$@" >/dev/null 2>&1 || rc=$?
  [[ $rc -eq $want ]] || fail "$* exited $rc, expected $want"
}

B join --as alpha >/dev/null
P join --as beta >/dev/null 2>&1

case "$check" in
  send-read)
    printf 'from shell\n' | "$work/mail" send alpha beta s2p >/dev/null
    has s2p P peek beta || fail "python does not list the shell message"
    has 'from shell' P read beta || fail "python does not read the shell message"
    has 'no new message' P peek beta || fail "python did not mark the shell message read"
    printf 'from python\n' | python3 "$py_dir/agent_mail.py" send beta alpha p2s >/dev/null
    has p2s B peek alpha || fail "shell does not list the python message"
    has 'from python' B read alpha || fail "shell does not read the python message"
    has 'no new message' B peek alpha || fail "shell did not mark the python message read"
    ;;
  locks)
    expect_rc 0 B lock res alpha shell
    expect_rc 3 P lock res beta python
    expect_rc 1 P unlock res beta
    expect_rc 0 B unlock res alpha
    expect_rc 0 P lock res beta python
    expect_rc 3 B lock res alpha shell
    expect_rc 1 B unlock res alpha
    expect_rc 0 P unlock res beta
    expect_rc 0 B lock brk alpha shell
    expect_rc 0 P unlock brk beta --break
    has 'no lock is held' B locks || fail "a broken lock is still listed"
    ;;
  state)
    B status alpha one >/dev/null
    P status beta two >/dev/null
    [[ "$(B who)" == "$(P who)" ]] || fail "who differs between the tools"
    printf 'wake\n' | python3 "$py_dir/agent_mail.py" send beta alpha wake >/dev/null
    expect_rc 0 B wait alpha 3
    printf 'wake\n' | "$work/mail" send alpha beta wake >/dev/null
    expect_rc 0 P wait beta 3
    [[ "$(B log 10)" == "$(P log 10)" ]] || fail "log differs between the tools"
    ;;
esac
printf '%s ok\n' "$check"
