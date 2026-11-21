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

# How long to wait for the Remote Control handshake after spawning. Observed
# connects land in ~5s; the margin is for a slow or retrying control plane.
RC_CONNECT_TIMEOUT=45

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

# Process liveness is not the same as Remote Control being up. A session whose
# handshake fails ("Session creation failed") stays alive and idle forever, so
# a pid-only check reports it healthy and never retries -- one transient
# server-side failure latches into a permanent outage. Read the actual state
# out of the session's pty log instead.
#
# That log is the raw pty stream, so it is full of cursor-positioning escapes
# that split words at arbitrary points ("/remote-control[19Gis[22Gactive").
# Stripping escapes is not enough; the spacing is unreliable too. Flatten all
# whitespace away and match against the flattened needles.
#
# The stream is append-only and a session can connect and later drop, so the
# marker that appears *last* is the current state, not merely the one present.
rc_state() {
  [[ -r "$RC_OUTPUT_LOG" ]] || { echo unknown; return; }

  local flat up_at down_at
  flat="$(sed -e 's/\x1b\[[0-9;?]*[a-zA-Z]//g' "$RC_OUTPUT_LOG" 2>/dev/null \
          | tr -d '[:space:]')"

  up_at="$(printf '%s' "$flat" \
           | grep -ob -e 'remote-controlisactive' -e 'claude.ai/code/session_' \
           | tail -1 | cut -d: -f1)"
  down_at="$(printf '%s' "$flat" \
             | grep -ob -e 'RemoteControldisconnected' -e 'Sessioncreationfailed' \
             | tail -1 | cut -d: -f1)"

  if [[ -n "$up_at" && ( -z "$down_at" || "$up_at" -gt "$down_at" ) ]]; then
    echo connected
  elif [[ -n "$down_at" ]]; then
    echo failed
  else
    echo unknown
  fi
}

# `setsid` makes the spawned process a process-group leader, so the whole
# script/sh/claude tree is reachable as a negative pid. Fall back to the bare
# pid for an adopted session, which may not lead its own group.
stop_session() {
  local pid="$1"
  kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  sleep 3
  kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
}

# The pid file carries "<pid> <origin>". Origin matters because rc_state reads
# RC_OUTPUT_LOG, which only describes sessions *we* spawned; for one adopted
# from a hand-run terminal that log belongs to some earlier session, so its
# contents would be answering a question about the wrong process. Liveness is
# all we can honestly assert there. A bare pid (the pre-origin format) is read
# as launched, which is the recoverable direction.
if [[ -f "$PID_FILE" ]]; then
  read -r tracked origin < "$PID_FILE" 2>/dev/null || true
  if is_alive "${tracked:-}"; then
    if [[ "${origin:-launched}" == "adopted" ]]; then
      exit 0
    fi
    case "$(rc_state)" in
      connected)
        exit 0
        ;;
      *)
        # Alive but with no live Remote Control connection. Tear it down instead
        # of leaving it parked: it would keep answering the liveness check, and
        # the adopt sweep below would happily re-adopt the same dead weight.
        log "session $SESSION_NAME alive but not connected (pid=$tracked); restarting"
        stop_session "$tracked"
        rm -f "$PID_FILE"
        ;;
    esac
  fi
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
  echo "$existing adopted" > "$PID_FILE"
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

echo "$pid launched" > "$PID_FILE"
log "started remote control session pid=$pid name=$SESSION_NAME"

# Confirm Remote Control actually connected -- not merely that the process
# survived. Spawning succeeds even when the handshake is going to fail, so
# waiting on liveness alone is what previously reported a dead session healthy.
deadline=$((SECONDS + RC_CONNECT_TIMEOUT))
state=unknown
while (( SECONDS < deadline )); do
  if ! is_alive "$pid"; then
    state=exited
    break
  fi
  state="$(rc_state)"
  [[ "$state" == "connected" || "$state" == "failed" ]] && break
  sleep 2
done

case "$state" in
  connected)
    log "session $SESSION_NAME connected (pid=$pid)"
    ;;
  exited)
    log "ERROR: session $SESSION_NAME exited during startup; see $RC_OUTPUT_LOG"
    rm -f "$PID_FILE"
    exit 1
    ;;
  *)
    # Includes the transient control-plane "Session creation failed". Reap it so
    # the next tick starts clean rather than adopting a connectionless session.
    log "ERROR: session $SESSION_NAME did not connect (state=$state); see $RC_OUTPUT_LOG"
    stop_session "$pid"
    rm -f "$PID_FILE"
    exit 1
    ;;
esac
