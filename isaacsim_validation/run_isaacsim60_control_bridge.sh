#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 --asset-root DIR --entrypoint FILE --run-dir DIR --host 127.0.0.1 --port PORT --token-file FILE [--image IMAGE] [--rl-display :N] [--no-webrtc] [--passive-linkage-visuals]" >&2
  exit 2
}

asset_root=""
entrypoint=""
run_dir=""
host="127.0.0.1"
port="8765"
token_file=""
enable_webrtc="1"
image=${ISAAC_SIM_IMAGE:-nvcr.io/nvidia/isaac-sim:6.0.0}
rl_display=""
passive_linkage_visuals="0"
while (($#)); do
  case "$1" in
    --asset-root) asset_root=${2:-}; shift 2 ;;
    --entrypoint) entrypoint=${2:-}; shift 2 ;;
    --run-dir) run_dir=${2:-}; shift 2 ;;
    --host) host=${2:-}; shift 2 ;;
    --port) port=${2:-}; shift 2 ;;
    --token-file) token_file=${2:-}; shift 2 ;;
    --image) image=${2:-}; shift 2 ;;
    --rl-display) rl_display=${2:-}; shift 2 ;;
    --no-webrtc) enable_webrtc="0"; shift ;;
    --passive-linkage-visuals) passive_linkage_visuals="1"; shift ;;
    *) usage ;;
  esac
done

[[ -n "$asset_root" && -n "$entrypoint" && -n "$run_dir" && -n "$token_file" ]] || usage
[[ -n "$image" ]] || usage
[[ "$host" == "127.0.0.1" ]] || { echo "managed bridge binds only 127.0.0.1" >&2; exit 2; }
[[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || { echo "invalid port" >&2; exit 2; }
if [[ -n "$rl_display" ]]; then
  [[ "$enable_webrtc" == "0" ]] || { echo "RL RGB rendering cannot share a WebRTC process" >&2; exit 2; }
  [[ "$image" == "nvcr.io/nvidia/isaac-sim:6.0.1" ]] || {
    echo "RL RGB rendering requires nvcr.io/nvidia/isaac-sim:6.0.1" >&2
    exit 2
  }
  [[ "$rl_display" =~ ^:[0-9]+(\.[0-9]+)?$ ]] || { echo "invalid RL X11 display" >&2; exit 2; }
  display_number=${rl_display#:}
  display_number=${display_number%%.*}
  [[ -S "/tmp/.X11-unix/X$display_number" ]] || {
    echo "RL X11 display socket not found: /tmp/.X11-unix/X$display_number" >&2
    exit 2
  }
  xdpyinfo -display "$rl_display" >/dev/null 2>&1 || {
    echo "RL X11 display is not responding: $rl_display" >&2
    exit 2
  }
fi

asset_root=$(realpath "$asset_root")
entrypoint=$(realpath "$entrypoint")
run_dir=$(realpath "$run_dir")
token_file=$(realpath "$token_file")
[[ -d "$asset_root" && -f "$entrypoint" && -d "$run_dir" && -f "$token_file" ]] || usage
case "$entrypoint" in "$asset_root"/*) ;; *) echo "entrypoint must be beneath asset root" >&2; exit 2 ;; esac
if [[ "$passive_linkage_visuals" == "1" ]]; then
  [[ -f "$asset_root/python/superarm_isaac_runtime/passive_linkage.py" ]] || {
    echo "passive-linkage solver is missing from the distribution" >&2
    exit 2
  }
  [[ -f "$asset_root/python/superarm_isaac_runtime/passive_linkage_usd.py" ]] || {
    echo "passive-linkage USD runtime is missing from the distribution" >&2
    exit 2
  }
  [[ -f "$asset_root/usd/superarm_amazinghand/zip_hand_payloads/instances.usda" ]] || {
    echo "passive-linkage instances.usda is missing from the distribution" >&2
    exit 2
  }
fi
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

webrtc_signal_port=${ISAACSIM_SIGNAL_PORT:-49100}
webrtc_stream_port=${ISAACSIM_STREAM_PORT:-47998}
webrtc_public_ip=${ISAACSIM_HOST:-}
[[ "$webrtc_signal_port" =~ ^[0-9]+$ ]] && ((webrtc_signal_port >= 1 && webrtc_signal_port <= 65535)) || { echo "invalid WebRTC signaling port" >&2; exit 2; }
[[ "$webrtc_stream_port" =~ ^[0-9]+$ ]] && ((webrtc_stream_port >= 1 && webrtc_stream_port <= 65535)) || { echo "invalid WebRTC media port" >&2; exit 2; }
webrtc_args=()
if [[ "$enable_webrtc" == "1" ]]; then
  webrtc_args=(
    --webrtc
    --webrtc-signal-port "$webrtc_signal_port"
    --webrtc-stream-port "$webrtc_stream_port"
  )
  if [[ -n "$webrtc_public_ip" ]]; then
    webrtc_args+=(--webrtc-public-ip "$webrtc_public_ip")
  fi
fi
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
terminate() {
  cleanup
  exit 143
}
trap cleanup EXIT
trap terminate INT TERM

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

container_gid=$(id -g)
docker_args=(
  --name "$container_name"
  --gpus all
  --network host
  --entrypoint /isaac-sim/python.sh
  -e ACCEPT_EULA=Y
  -e NVIDIA_VISIBLE_DEVICES=all
  -e NVIDIA_DRIVER_CAPABILITIES=all
  -e PYTHONUNBUFFERED=1
  -e PYTHONDONTWRITEBYTECODE=1
  -e PYTHONPATH=/workspace/isaacsim_validation
  -v "$module_root:/workspace/isaacsim_validation/isaacsim_validation:ro"
  -v "$asset_root:/workspace/asset:ro"
  -v "$run_dir:/workspace/run:rw"
  -v "$token_file:/run/secrets/isaac_bridge_token:ro"
)
bridge_args=()
if [[ "$passive_linkage_visuals" == "1" ]]; then
  bridge_args+=(--passive-linkage-visuals)
fi
if [[ -n "$rl_display" ]]; then
  cache_root=${ISAAC_SIM_RL_CACHE_ROOT:-"$HOME/.cache/lelab/isaac_sim/6.0.1"}
  cache_dirs=(omni-user omni-cache kit ov glcache compute)
  for cache_dir in "${cache_dirs[@]}"; do
    mkdir -p "$cache_root/$cache_dir"
    chmod 0777 "$cache_root/$cache_dir"
  done
  docker_args+=(
    --user "1234:$container_gid"
    -e ISAAC_SIM_DISPLAY=1
    -e "DISPLAY=$rl_display"
    -e QT_X11_NO_MITSHM=1
    -e OMNI_USER_DIR=/tmp/omni-user
    -e OMNI_CACHE_DIR=/tmp/omni-cache
    -v /tmp/.X11-unix:/tmp/.X11-unix:rw
    -v "$cache_root/omni-user:/tmp/omni-user"
    -v "$cache_root/omni-cache:/tmp/omni-cache"
    -v "$cache_root/kit:/isaac-sim/kit/cache"
    -v "$cache_root/ov:/root/.cache/ov"
    -v "$cache_root/glcache:/root/.cache/nvidia/GLCache"
    -v "$cache_root/compute:/root/.nv/ComputeCache"
  )
  if [[ -n "${ISAAC_SIM_XAUTHORITY:-${XAUTHORITY:-}}" ]]; then
    xauthority=${ISAAC_SIM_XAUTHORITY:-${XAUTHORITY:-}}
    docker_args+=(
      -e XAUTHORITY=/tmp/isaac-xauthority
      -v "$xauthority:/tmp/isaac-xauthority:ro"
    )
  fi
  bridge_args+=(--replicator-rgb)
else
  # The teleoperation token remains host-owned 0600, so retain the existing
  # isolated root bridge path for compatibility with the validated 6.0.0 flow.
  docker_args+=(
    --user "0:$container_gid"
    -v isaac_sim_60_cache:/root/.cache/ov
    -v isaac_nucleus_60_cache:/root/.local/share/ov/data
  )
fi

docker run "${docker_args[@]}" "$image" -m isaacsim_validation.control_bridge \
  --asset-root /workspace/asset \
  --entrypoint "/workspace/asset/$entrypoint_relative" \
  --run-dir /workspace/run \
  --host "$host" --port "$port" \
  --token-file /run/secrets/isaac_bridge_token \
  "${webrtc_args[@]}" \
  "${bridge_args[@]}" \
  >"$run_dir/container.log" 2>&1 &
docker_pid=$!
wait "$docker_pid"
