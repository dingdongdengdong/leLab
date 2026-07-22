#!/usr/bin/env bash
set -euo pipefail

usd=${1:?usage: run_asset_validator.sh <usd-path> <report-path>}
report=${2:?usage: run_asset_validator.sh <usd-path> <report-path>}
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
usd=$(realpath "$usd")
report=$(realpath -m "$report")

case "$usd" in "$project_root"/*) ;; *) echo "USD must be inside $project_root" >&2; exit 2 ;; esac
case "$report" in "$project_root"/*) ;; *) echo "report must be inside $project_root" >&2; exit 2 ;; esac

usd_container="/workspace/project/${usd#"$project_root"/}"
report_container="/workspace/project/${report#"$project_root"/}"
mkdir -p "$(dirname "$report")"
chmod 0777 "$(dirname "$report")"
rm -f "$report"

set +e
docker run --rm --gpus all --network host \
  --entrypoint /bin/bash \
  -e ACCEPT_EULA=Y \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e PYTHONUNBUFFERED=1 \
  -v "$project_root:/workspace/project:rw" \
  -v isaac_sim_60_cache:/root/.cache/ov \
  -v isaac_nucleus_60_cache:/root/.local/share/ov/data \
  nvcr.io/nvidia/isaac-sim:6.0.0 \
  -lc "/isaac-sim/python.sh /workspace/project/isaacsim_validation/validate_usd_asset.py \
    --usd '$usd_container' --output '$report_container'"
container_status=$?
set -e

if [[ ! -s "$report" ]]; then
  echo "Isaac Asset Validator did not write a report" >&2
  exit 2
fi
if [[ "$container_status" -ne 0 ]]; then
  exit "$container_status"
fi
python3 - "$report" <<'PY'
import json
import sys

report = json.load(open(sys.argv[1], encoding="utf-8"))
raise SystemExit(0 if report["verdict"]["passed"] else 2)
PY
