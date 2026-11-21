#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="/home/saif/AI/Masari"
LOG_FILE="$REPO_DIR/logs/alpaca_sandbox_cron.log"

cd "$REPO_DIR"
source "$REPO_DIR/.venv/bin/activate"

{
  echo "===== $(date -Iseconds) [monitor] ====="
  python test_alpaca_optionsplay_sandbox_execution.py --monitor --submit
  echo
} >> "$LOG_FILE" 2>&1
