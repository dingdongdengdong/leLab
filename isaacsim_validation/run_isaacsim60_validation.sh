#!/usr/bin/env bash
set -euo pipefail

profile=${1:?usage: run_isaacsim60_validation.sh <raw|aligned|served> <run-id>}
run_id=${2:?usage: run_isaacsim60_validation.sh <raw|aligned|served> <run-id>}
case "$profile" in
  raw|aligned|served) ;;
  *) echo "profile must be raw, aligned, or served" >&2; exit 2 ;;
esac

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
input_dir="$project_root/artifacts/isaacsim_superarm/$run_id/${profile}_input"
run_dir="$project_root/artifacts/isaacsim_superarm/$run_id/${profile}_isaac"
test -f "$input_dir/superarm_amazinghand.urdf"
install -d -m 0777 "$run_dir"
chmod 0777 "$run_dir"

container_name="superarm-isaac60-${profile}-${run_id,,}"
container_name=${container_name//[^a-z0-9_.-]/-}
docker rm -f "$container_name" >/dev/null 2>&1 || true

docker run --name "$container_name" --gpus all --network host \
  --entrypoint /bin/bash \
  -e ACCEPT_EULA=Y \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e PYTHONUNBUFFERED=1 \
  -v "$project_root:/workspace/project:rw" \
  -v isaac_sim_60_cache:/root/.cache/ov \
  -v isaac_nucleus_60_cache:/root/.local/share/ov/data \
  nvcr.io/nvidia/isaac-sim:6.0.0 \
  -lc "set +e; /isaac-sim/python.sh /workspace/project/isaacsim_validation/run_validation.py \
    --urdf /workspace/project/artifacts/isaacsim_superarm/$run_id/${profile}_input/superarm_amazinghand.urdf \
    --run-dir /workspace/project/artifacts/isaacsim_superarm/$run_id/${profile}_isaac \
    --profile $profile; status=\$?; chmod -R a+rwX \
    /workspace/project/artifacts/isaacsim_superarm/$run_id/${profile}_isaac \
    2>/dev/null || true; exit \$status" \
  2>&1 | tee "$run_dir/isaac.log"

status=${PIPESTATUS[0]}
docker rm -f "$container_name" >/dev/null 2>&1 || true
exit "$status"
