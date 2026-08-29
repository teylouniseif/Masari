#!/usr/bin/env bash
#
# Keeps a Claude Code Remote Control session alive so the box stays reachable
# from claude.ai / mobile.
#
# `claude --remote-control` is an *interactive* command: with no TTY it falls
# back to --print and exits immediately ("Input must be provided either through
# stdin or as a prompt argument"). So the session is launched under `script`,
# which allocates a pty, and `setsid`, which detaches it from this script's
# process group so it survives cron reaping the job.
#
# Install (checks every 10 minutes, and on reboot):
#   */10 * * * * /home/saif/AI/Masari/scripts/claude_rc_watchdog.sh
#   @reboot      /home/saif/AI/Masari/scripts/claude_rc_watchdog.sh
#
# Worst-case unreachable window after a crash is therefore ~10 minutes.
#
set -euo pipefail

# cron runs with a minimal PATH (typically /usr/bin:/bin), which does not
# include ~/.local/bin where claude is installed. Without this the launch fails
# with "claude: command not found" and the box silently stays unreachable.
export PATH="${HOME:-/home/saif}/.local/bin:$PATH"

REPO_DIR="/home/saif/AI/Masari"
SESSION_NAME="${CLAUDE_RC_SESSION_NAME:-$(hostname -s)-rc}"
LOG_FILE="$REPO_DIR/logs/claude_rc_watchdog.log"
RC_OUTPUT_LOG="$REPO_DIR/logs/claude_rc_session.log"
PID_FILE="$REPO_DIR/logs/claude_rc.pid"
LOCK_FILE="$REPO_DIR/logs/claude_rc_watchdog.lock"

mkdir -p "$REPO_DIR/logs"

log() { echo "$(date -Iseconds)  $*" >> "$LOG_FILE"; }

# Serialize so a slow or hung start cannot let the next tick launch a second
# session. Exit quietly if another run holds the lock.
exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

# A live session is a pid that still exists, is actually the claude binary or
# its `script` pty wrapper, *and* carries our session name.
#
# Checking the cmdline alone is not enough: any process that merely mentions
# the session name matches, including the shell running this very script and
# the pgrep pipeline itself. That false positive is the dangerous direction --
# the watchdog concludes a session exists, and the box goes unreachable
# precisely when it is supposed to self-heal. Matching on comm rules it out,
# since only the real processes are named `claude` or `script`.
is_alive() {
  local pid="$1"
  [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] || return 1
  [[ -r "/proc/$pid/cmdline" ]] || return 1

  local comm
  comm="$(cat "/proc/$pid/comm" 2>/dev/null || true)"
  [[ "$comm" == "claude" || "$comm" == "script" ]] || return 1

  tr '\0' ' ' < "/proc/$pid/cmdline" | grep -q -- "--remote-control $SESSION_NAME"
}

if [[ -f "$PID_FILE" ]] && is_alive "$(cat "$PID_FILE" 2>/dev/null || true)"; then
  exit 0
fi

# No tracked session. Adopt one started by hand before starting a duplicate.
existing=""
while read -r candidate; do
  if is_alive "$candidate"; then
    existing="$candidate"
    break
  fi
done < <(pgrep -f -- "--remote-control $SESSION_NAME" 2>/dev/null || true)

if [[ -n "$existing" ]]; then
  echo "$existing" > "$PID_FILE"
  log "adopted existing session pid=$existing name=$SESSION_NAME"
  exit 0
fi

cd "$REPO_DIR"

# Inherited from a parent Claude session, this silently disables transcript
# saving in the child. Cron will not set it, but clear it so a hand-run from
# inside a Claude session behaves the same as the cron one.
unset CLAUDE_CODE_CHILD_SESSION

# `script -qec CMD /dev/null` runs CMD on a pty and discards the typescript;
# the session's own (ANSI-heavy) output goes to RC_OUTPUT_LOG for debugging.
#
# `9>&-` closes the lock fd in the child. Without it the long-lived session
# inherits fd 9 and holds the flock for its entire life, so every later run
# fails `flock -n` and exits before it can check anything -- the watchdog goes
# permanently blind while looking like it is working.
setsid script -qec "claude --remote-control $SESSION_NAME" /dev/null \
  </dev/null >"$RC_OUTPUT_LOG" 2>&1 9>&- &
pid=$!

echo "$pid" > "$PID_FILE"
log "started remote control session pid=$pid name=$SESSION_NAME"

# Confirm it actually came up rather than dying on a bad login/credential.
sleep 10
if is_alive "$pid"; then
  log "session $SESSION_NAME healthy (pid=$pid)"
else
  log "ERROR: session $SESSION_NAME exited within 10s; see $RC_OUTPUT_LOG"
  rm -f "$PID_FILE"
  exit 1
fi
