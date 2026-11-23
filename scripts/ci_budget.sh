#!/bin/bash
# Fail the job when it ran longer than LIMIT seconds. START and LIMIT come
# from the workflow. This does not retry the job and does not widen a timeout.
set -euo pipefail
elapsed=$(( $(date +%s) - START ))
echo "elapsed ${elapsed}s limit ${LIMIT}s"
if (( elapsed > LIMIT )); then
  echo "::error::job took ${elapsed}s; budget is ${LIMIT}s"
  exit 1
fi
