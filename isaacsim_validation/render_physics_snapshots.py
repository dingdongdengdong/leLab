"""Render static USD snapshots exported from measured Isaac PhysX states."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--run-dir", required=True, type=Path)
args, _ = parser.parse_known_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp(
    {
        "headless": True,
        "renderer": "RaytracedLighting",
        "width": 1280,
        "height": 720,
    }
)

import omni.replicator.core as rep  # noqa: E402
import omni.usd  # noqa: E402
from isaacsim.core.utils.extensions import enable_extension  # noqa: E402
from pxr import Usd, UsdGeom, UsdLux  # noqa: E402
from visuals import image_has_detail, validate_direct_grasp_frames  # noqa: E402

enable_extension("omni.kit.renderer.capture")
app.update()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prim_named(stage: Usd.Stage, name: str):
    matches = [prim for prim in stage.Traverse() if prim.GetName() == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one prim named {name!r}, found {len(matches)}")
    return matches[0]


def _camera_pose(stage: Usd.Stage, prim, *, closeup: bool) -> tuple[list[float], list[float]]:
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=True,
    )
    box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    minimum = [float(value) for value in box.GetMin()]
    maximum = [float(value) for value in box.GetMax()]
    center = [(low + high) / 2.0 for low, high in zip(minimum, maximum, strict=True)]
    span = max(maximum[index] - minimum[index] for index in range(3))
    radius = max(span, 0.08)
    factor = 2.4 if closeup else 2.2
    eye = [center[0] + factor * radius, center[1] - factor * radius, center[2] + radius]
    return eye, center


def _capture(
    output: Path,
    stage: Usd.Stage,
    prim,
    *,
    closeup: bool,
    fixed_pose: tuple[list[float], list[float]] | None = None,
) -> dict:
    eye, target = fixed_pose or _camera_pose(stage, prim, closeup=closeup)
    temporary = Path(tempfile.mkdtemp(prefix="superarm-static-render-"))
    try:
        with rep.new_layer():
            camera = rep.create.camera(position=eye, look_at=target, focal_length=35)
            product = rep.create.render_product(camera, (1280, 720))
            writer = rep.WriterRegistry.get("BasicWriter")
            writer.initialize(output_dir=str(temporary), rgb=True)
            writer.attach([product])
            for _ in range(8):
                rep.orchestrator.step(delta_time=0.0, rt_subframes=8)
            writer.detach()
        frames = sorted(temporary.glob("rgb*.png"))
        if not frames:
            raise RuntimeError("static Replicator render did not create an RGB frame")
        shutil.copyfile(frames[-1], output)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    if not image_has_detail(output):
        raise RuntimeError(f"static snapshot render has no visible detail: {output}")
    return {
        "path": str(output),
        "bytes": output.stat().st_size,
        "eye": eye,
        "target": target,
        "method": "static_replicator_from_physics_snapshot",
    }


def _visual_boundary(profile: str) -> str:
    return {
        "raw": (
            "Raw retains the generated full hand shell without LeLab attachment alignment; it is diagnostic only."
        ),
        "aligned": "Aligned applies LeLab wrist and joint-5 transforms and retains every generated hand visual.",
        "learning": (
            "Learning applies LeLab alignment, keeps moving proximal/distal finger shells, and removes only "
            "unbound closed-loop linkage meshes that cannot follow the simplified serial articulation."
        ),
        "served": (
            "Served matches the LeLab showroom URDF tree after hand visuals are removed for its MJCF overlay."
        ),
    }[profile]


def main() -> int:
    run_dir = args.run_dir.resolve()
    report_path = run_dir / "isaac-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    try:
        if report.get("status") != "NUMERIC_PASS":
            raise RuntimeError(f"numeric snapshot report is not ready: {report.get('status')}")
        snapshots = report.get("physics_snapshots", {}).get("hand_states", [])
        if [item.get("name") for item in snapshots] != ["open", "half_close", "close"]:
            raise RuntimeError("numeric report does not contain open, half-close, and close snapshots")

        hand_frames = []
        fixed_hand_pose = None
        last_stage = None
        for snapshot in snapshots:
            snapshot_path = Path(snapshot["path"])
            if not snapshot_path.is_file():
                raise RuntimeError(f"physics snapshot is missing: {snapshot_path}")
            if not omni.usd.get_context().open_stage(str(snapshot_path)):
                raise RuntimeError(f"Isaac could not open physics snapshot: {snapshot_path}")
            for _ in range(8):
                app.update()
            stage = omni.usd.get_context().get_stage()
            UsdLux.DomeLight.Define(stage, "/World/SuperArmSnapshotLight").CreateIntensityAttr(700.0)
            hand_root = _prim_named(stage, "r_wrist_interface")
            if fixed_hand_pose is None:
                fixed_hand_pose = _camera_pose(stage, hand_root, closeup=True)
            frame = _capture(
                run_dir / f"hand_{snapshot['name']}.png",
                stage,
                hand_root,
                closeup=True,
                fixed_pose=fixed_hand_pose,
            )
            hand_frames.append(
                {
                    "name": snapshot["name"],
                    "snapshot": str(snapshot_path),
                    **frame,
                }
            )
            last_stage = stage

        report["visual_motion"] = validate_direct_grasp_frames(hand_frames)
        report["visual_motion"]["frames"] = hand_frames
        if last_stage is None:
            raise RuntimeError("no snapshot stage was opened")
        root_prim = last_stage.GetPrimAtPath(report["import"]["prim_path"])
        if not root_prim.IsValid():
            raise RuntimeError("final snapshot has no robot root prim")
        report["screenshots"] = [
            _capture(run_dir / "whole_robot.png", last_stage, root_prim, closeup=False)
        ]
        report["visual_boundary"] = _visual_boundary(report["profile"])
        report["status"] = "PASS"
        report["phase"] = "complete"
        report.pop("error", None)
        _write_json(report_path, report)
        return 0
    except Exception as exc:
        report["status"] = "FAIL"
        report["phase"] = "rendering_static_physics_snapshots"
        report["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(report_path, report)
        raise
    finally:
        app.close()


raise SystemExit(main())
