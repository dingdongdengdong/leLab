#!/usr/bin/env bash
set -euo pipefail

profile=${1:?usage: run_isaacsim60_validation.sh <raw|aligned|learning|served|zip_learning> <run-id> [prepared-hand-package]}
run_id=${2:?usage: run_isaacsim60_validation.sh <raw|aligned|learning|served|zip_learning> <run-id> [prepared-hand-package]}
hand_usd_package=${3:-}
case "$profile" in
  raw|aligned|learning|served|zip_learning) ;;
  *) echo "profile must be raw, aligned, learning, served, or zip_learning" >&2; exit 2 ;;
esac

project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
input_dir="$project_root/artifacts/isaacsim_superarm/$run_id/${profile}_input"
run_dir="$project_root/artifacts/isaacsim_superarm/$run_id/${profile}_isaac"
test -f "$input_dir/superarm_amazinghand.urdf"
hand_arg=""
if [[ "$profile" == zip_learning ]]; then
  hand_usd_package=$(realpath "${hand_usd_package:?zip_learning requires a prepared hand package}")
  case "$hand_usd_package" in "$project_root"/*) ;; *) echo "hand package must be inside $project_root" >&2; exit 2 ;; esac
  test -f "$hand_usd_package/prepared-manifest.json"
  hand_arg="--hand-usd-package /workspace/project/${hand_usd_package#"$project_root"/}"
fi
install -d -m 0777 "$run_dir"
chmod 0777 "$run_dir"

container_prefix="superarm-isaac60-${profile}-${run_id,,}"
container_prefix=${container_prefix//[^a-z0-9_.-]/-}
numeric_container_name="${container_prefix}-numeric"
render_container_name="${container_prefix}-render"
docker rm -f "$numeric_container_name" "$render_container_name" >/dev/null 2>&1 || true

set +e
docker run --name "$numeric_container_name" --gpus all --network host \
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
    --profile $profile $hand_arg; status=\$?; chmod -R a+rwX \
    /workspace/project/artifacts/isaacsim_superarm/$run_id/${profile}_isaac \
    2>/dev/null || true; exit \$status" \
  2>&1 | tee "$run_dir/numeric.log"

numeric_status=${PIPESTATUS[0]}
docker rm -f "$numeric_container_name" >/dev/null 2>&1 || true
if (( numeric_status != 0 )); then
  cp "$run_dir/numeric.log" "$run_dir/isaac.log"
  exit "$numeric_status"
fi
numeric_report_status=$(
  python3 - "$run_dir/isaac-report.json" <<'PY'
import json
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
if not report_path.is_file():
    print("MISSING_REPORT")
    raise SystemExit(0)
report = json.loads(report_path.read_text(encoding="utf-8"))
print(report.get("status") or "MISSING_STATUS")
PY
)
case "$numeric_report_status" in
  NUMERIC_PASS|PASS) ;;
  *)
    cp "$run_dir/numeric.log" "$run_dir/isaac.log"
    echo "Numeric Isaac validation report is not NUMERIC_PASS: $numeric_report_status" >&2
    exit 2
    ;;
esac

docker run --name "$render_container_name" --gpus all --network host \
  --entrypoint /bin/bash \
  -e ACCEPT_EULA=Y \
  -e NVIDIA_VISIBLE_DEVICES=all \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -e PYTHONUNBUFFERED=1 \
  -v "$project_root:/workspace/project:rw" \
  -v isaac_sim_60_cache:/root/.cache/ov \
  -v isaac_nucleus_60_cache:/root/.local/share/ov/data \
  nvcr.io/nvidia/isaac-sim:6.0.0 \
  -lc "set +e; /isaac-sim/python.sh \
    /workspace/project/isaacsim_validation/render_physics_snapshots.py \
    --run-dir /workspace/project/artifacts/isaacsim_superarm/$run_id/${profile}_isaac; \
    status=\$?; chmod -R a+rwX \
    /workspace/project/artifacts/isaacsim_superarm/$run_id/${profile}_isaac \
    2>/dev/null || true; exit \$status" \
  2>&1 | tee "$run_dir/render.log"

render_status=${PIPESTATUS[0]}
set -e
docker rm -f "$render_container_name" >/dev/null 2>&1 || true
cat "$run_dir/numeric.log" "$run_dir/render.log" > "$run_dir/isaac.log"
if (( render_status != 0 )); then
  exit "$render_status"
fi

python3 - "$run_dir/isaac-report.json" <<'PY'
import json
import sys
from pathlib import Path

report_path = Path(sys.argv[1])
if not report_path.is_file():
    raise SystemExit(f"Isaac validation did not create {report_path}")
report = json.loads(report_path.read_text(encoding="utf-8"))
if report.get("status") != "PASS":
    raise SystemExit(f"Isaac validation report is not PASS: {report.get('error', report.get('phase'))}")
PY
