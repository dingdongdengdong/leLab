#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --asset-root DIR --entrypoint FILE --run-dir DIR --host 127.0.0.1 --port PORT --token-file FILE" >&2
  exit 2
}

asset_root=""
entrypoint=""
run_dir=""
host="127.0.0.1"
port="8765"
token_file=""
while (($#)); do
  case "$1" in
    --asset-root) asset_root=${2:-}; shift 2 ;;
    --entrypoint) entrypoint=${2:-}; shift 2 ;;
    --run-dir) run_dir=${2:-}; shift 2 ;;
    --host) host=${2:-}; shift 2 ;;
    --port) port=${2:-}; shift 2 ;;
    --token-file) token_file=${2:-}; shift 2 ;;
    *) usage ;;
  esac
done

[[ -n "$asset_root" && -n "$entrypoint" && -n "$run_dir" && -n "$token_file" ]] || usage
[[ "$host" == "127.0.0.1" ]] || { echo "managed bridge binds only 127.0.0.1" >&2; exit 2; }
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || { echo "invalid port" >&2; exit 2; }

asset_root=$(realpath "$asset_root")
entrypoint=$(realpath "$entrypoint")
run_dir=$(realpath "$run_dir")
token_file=$(realpath "$token_file")
[[ -d "$asset_root" && -f "$entrypoint" && -d "$run_dir" && -f "$token_file" ]] || usage
case "$entrypoint" in "$asset_root"/*) ;; *) echo "entrypoint must be beneath asset root" >&2; exit 2 ;; esac
case "$token_file" in
  "$run_dir"|"$run_dir"/*)
    echo "token file must remain outside the read-write run directory" >&2
    exit 2
    ;;
esac
entrypoint_relative=${entrypoint#"$asset_root"/}

module_root=$(python3 - <<'PY'
from pathlib import Path
import isaacsim_validation

print(Path(isaacsim_validation.__file__).parent.resolve())
PY
)
[[ -d "$module_root" ]] || { echo "could not resolve isaacsim_validation package" >&2; exit 2; }

paths_overlap() {
  local left=${1%/}
  local right=${2%/}
  [[ "$left" == "$right" || "$left" == "$right/"* || "$right" == "$left/"* ]]
}
if paths_overlap "$run_dir" "$asset_root" || paths_overlap "$run_dir" "$module_root"; then
  echo "read-write run directory must not overlap read-only sources" >&2
  exit 2
fi

image=${ISAAC_SIM_IMAGE:-nvcr.io/nvidia/isaac-sim:6.0.0}
run_key=$(python3 - <<'PY'
import secrets
print(secrets.token_hex(6))
PY
)
container_name="superarm-isaac-control-$run_key"
metadata="$run_dir/container-metadata.json"

cleanup() {
  docker rm -f "$container_name" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM

python3 - "$metadata" "$container_name" "$image" "$host" "$port" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
path.write_text(json.dumps({
    "container_name": sys.argv[2],
    "image": sys.argv[3],
    "host": sys.argv[4],
    "port": int(sys.argv[5]),
    "container_log": "container.log",
}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

docker run --name "$container_name" --gpus all --network host \
  --entrypoint /isaac-sim/python.sh \
  -e ACCEPT_EULA=Y \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e PYTHONUNBUFFERED=1 \
  -e PYTHONPATH=/workspace/isaacsim_validation \
  -v "$module_root:/workspace/isaacsim_validation/isaacsim_validation:ro" \
  -v "$asset_root:/workspace/asset:ro" \
  -v "$run_dir:/workspace/run:rw" \
  -v "$token_file:/run/secrets/isaac_bridge_token:ro" \
  -v isaac_sim_60_cache:/root/.cache/ov \
  -v isaac_nucleus_60_cache:/root/.local/share/ov/data \
  "$image" -m isaacsim_validation.control_bridge \
  --asset-root /workspace/asset \
  --entrypoint "/workspace/asset/$entrypoint_relative" \
  --run-dir /workspace/run \
  --host "$host" --port "$port" \
  --token-file /run/secrets/isaac_bridge_token \
  >"$run_dir/container.log" 2>&1
