#!/usr/bin/env bash
# Flip-flop for this project's always-on Cloud Scheduler jobs (prod-keepwarm,
# neon-keepalive). Both jobs are PAUSED by default -- run `on` only for an
# active demo/dev window, then `off` again to avoid standing invocation cost.
set -euo pipefail

PROJECT="dealhunter-prod-260812"
LOCATION="asia-south1"
JOBS=(prod-keepwarm neon-keepalive)

usage() {
  echo "Usage: $0 {on|off|status}"
  echo "  on     - resume all scheduler jobs (keeps prod warm + Neon DB awake)"
  echo "  off    - pause all scheduler jobs (zero scheduled invocation cost)"
  echo "  status - show current state of each job"
  exit 1
}

[[ $# -eq 1 ]] || usage

case "$1" in
  on)
    for job in "${JOBS[@]}"; do
      gcloud scheduler jobs resume "$job" --project="$PROJECT" --location="$LOCATION"
    done
    ;;
  off)
    for job in "${JOBS[@]}"; do
      gcloud scheduler jobs pause "$job" --project="$PROJECT" --location="$LOCATION"
    done
    ;;
  status)
    gcloud scheduler jobs list --project="$PROJECT" --location="$LOCATION" \
      --format="table(name,schedule,state)"
    ;;
  *)
    usage
    ;;
esac
