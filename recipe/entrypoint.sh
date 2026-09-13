#!/usr/bin/env bash
set -euo pipefail
for ((i=0; i<3600; i++)); do
  if [[ -f /state/release ]]; then exec vllm serve "$@"; fi
  sleep 1
done
echo 'Tempo barrier timed out before coordinated release' >&2
exit 124
