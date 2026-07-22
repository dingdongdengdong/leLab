"""Render static USD snapshots exported from measured Isaac PhysX states."""

from __future__ import annotations

import argparse
import json
import shutil
import tempfile
from collections import Counter
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
from pxr import Sdf, Usd, UsdGeom, UsdLux  # noqa: E402
from visuals import (  # noqa: E402
    image_has_detail,
    validate_direct_grasp_frames,
    validate_independent_finger_linkage_sequence,
    validate_passive_linkage_motion_sequence,
    validate_passive_linkage_stage_contract,
    zip_learning_visual_boundary,
)

enable_extension("omni.kit.renderer.capture")
app.update()


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _prim_named(stage: Usd.Stage, name: str):
    matches = [prim for prim in stage.Traverse() if prim.GetName() == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one prim named {name!r}, found {len(matches)}")
    return matches[0]


def _passive_root(stage: Usd.Stage, robot_root: str):
    prefix = robot_root.rstrip("/") + "/"
    matches = [
        prim
        for prim in stage.Traverse()
        if prim.GetName() == "passive_linkage_visuals"
        and (str(prim.GetPath()) == robot_root or str(prim.GetPath()).startswith(prefix))
    ]
    if len(matches) != 1:
        raise RuntimeError(f"expected one passive_linkage_visuals group, found {len(matches)}")
    return matches[0]


def _validate_passive_linkage_stage_contract(stage: Usd.Stage, robot_root: str, contract: dict) -> dict:
    return validate_passive_linkage_stage_contract(
        _passive_linkage_stage_summary(stage, robot_root),
        contract,
    )


def _passive_linkage_stage_summary(stage: Usd.Stage, robot_root: str) -> dict:
    passive_root = _passive_root(stage, robot_root)
    passive_path = str(passive_root.GetPath())
    prefix = passive_path.rstrip("/") + "/"
    passive_prims = [
        prim
        for prim in stage.Traverse()
        if str(prim.GetPath()) == passive_path or str(prim.GetPath()).startswith(prefix)
    ]
    part_prims = [prim for prim in passive_prims if prim.GetName().startswith("part_")]
    parts = []
    for prim in part_prims:
        path = str(prim.GetPath())
        try:
            finger = int(path.split("/passive_linkage_visuals/finger", 1)[1].split("/", 1)[0])
        except (IndexError, ValueError) as exc:
            raise RuntimeError(f"passive visual part is not under a finger group: {path}") from exc
        source_index = _required_attr(prim, "passive_source_index")
        source_prim = _required_attr(prim, "passive_source_prim")
        reference_prim = _required_attr(prim, "passive_reference_prim")
        translate, orient = _local_transform(prim)
        parts.append(
            {
                "finger": finger,
                "source_index": int(source_index),
                "source_prim": source_prim,
                "reference_prim": reference_prim,
                "xform_path": path,
                "translate": translate,
                "orient": orient,
                "metadata_source_index": int(source_index),
                "metadata_source_prim": source_prim,
                "metadata_reference_prim": reference_prim,
                "type_name": str(prim.GetTypeName()),
                "applied_schemas": [str(schema) for schema in prim.GetAppliedSchemas()],
            }
        )
    shell_visual_count = sum("_shell" in prim.GetName().lower() for prim in passive_prims)
    physics_schema_count = sum(
        any(
            marker
            in " ".join(
                [str(prim.GetTypeName()), *(str(schema) for schema in prim.GetAppliedSchemas())]
            ).lower()
            for marker in ("physics", "rigidbody", "rigid_body", "collision", "collider", "mass", "joint")
        )
        for prim in passive_prims
    )
    summary = {
        "parts": parts,
        "visual_part_count": len(parts),
        "parts_per_finger": dict(sorted(Counter(part["finger"] for part in parts).items())),
        "shell_visual_count": shell_visual_count,
        "physics_schema_count": physics_schema_count,
    }
    return summary


def _required_attr(prim, name: str):
    attr = prim.GetAttribute(name)
    if not attr or not attr.HasAuthoredValue():
        raise RuntimeError(f"passive visual part missing {name} metadata: {prim.GetPath()}")
    return attr.Get()


def _local_transform(prim) -> tuple[tuple[float, float, float], tuple[float, float, float, float]]:
    translate = None
    orient = None
    for op in UsdGeom.Xformable(prim).GetOrderedXformOps():
        op_name = op.GetOpName()
        value = op.Get()
        if op_name == "xformOp:translate":
            translate = tuple(float(item) for item in value)
        elif op_name == "xformOp:orient":
            imaginary = value.GetImaginary()
            orient = (
                float(value.GetReal()),
                float(imaginary[0]),
                float(imaginary[1]),
                float(imaginary[2]),
            )
    if translate is None or orient is None:
        raise RuntimeError(f"passive visual part missing local translate/orient xforms: {prim.GetPath()}")
    return translate, orient


def _passive_finger_root(stage: Usd.Stage, robot_root: str, target_finger: int):
    passive_root = _passive_root(stage, robot_root)
    finger_path = passive_root.GetPath().AppendChild(f"finger{target_finger}")
    finger_root = stage.GetPrimAtPath(finger_path)
    if not finger_root.IsValid():
        raise RuntimeError(f"missing passive linkage finger{target_finger} group")
    return finger_root


def _camera_pose(stage: Usd.Stage, prim, *, closeup: bool) -> tuple[list[float], list[float]]:
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        # URDF import writes an extents hint before the ZIP visual overlay is
        # added. It remains valid for the hand-link close-up, but using it for
        # the whole robot can crop the added hand. Recompute composed bounds
        # for the whole-robot frame.
        useExtentsHint=closeup,
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
            camera = rep.create.camera(
                position=eye,
                look_at=target,
                focal_length=35,
                clipping_range=(0.001, 100.0),
            )
            product = rep.create.render_product(camera, (1280, 720))
            writer = rep.WriterRegistry.get("BasicWriter")
            writer.initialize(output_dir=str(temporary), rgb=True)
            writer.attach([product])
            for _ in range(8):
                rep.orchestrator.step(rt_subframes=8)
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
        "zip_learning": zip_learning_visual_boundary(),
    }[profile]


def main() -> int:
    run_dir = args.run_dir.resolve()
    report_path = run_dir / "isaac-report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    try:
        if report.get("status") not in {"NUMERIC_PASS", "PASS"}:
            raise RuntimeError(f"numeric snapshot report is not ready: {report.get('status')}")
        snapshots = report.get("physics_snapshots", {}).get("hand_states", [])
        if [item.get("name") for item in snapshots] != ["open", "half_close", "close"]:
            raise RuntimeError("numeric report does not contain open, half-close, and close snapshots")
        passive_visual_report = None
        if report.get("profile") == "zip_learning":
            independent_snapshots = report.get("physics_snapshots", {}).get("independent_finger_states", [])
            passive_visual_report = {
                "grasp_sequence": validate_passive_linkage_motion_sequence(snapshots),
                "independent_fingers": validate_independent_finger_linkage_sequence(
                    snapshots[0],
                    independent_snapshots,
                ),
                "snapshot_stage_summaries": [],
            }
            report["passive_linkage_visuals"] = passive_visual_report
        else:
            independent_snapshots = []

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
            if passive_visual_report is not None:
                passive_visual_report["snapshot_stage_summaries"].append(
                    {
                        "name": snapshot["name"],
                        "snapshot": str(snapshot_path),
                        **_validate_passive_linkage_stage_contract(
                            stage,
                            report["import"]["prim_path"],
                            snapshot["passive_linkage_contract"],
                        ),
                    }
                )
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
        independent_frames = []
        for snapshot in independent_snapshots:
            snapshot_path = Path(snapshot["path"])
            target_finger = int(snapshot["target_finger"])
            if not snapshot_path.is_file():
                raise RuntimeError(f"independent finger snapshot is missing: {snapshot_path}")
            if not omni.usd.get_context().open_stage(str(snapshot_path)):
                raise RuntimeError(f"Isaac could not open independent finger snapshot: {snapshot_path}")
            for _ in range(8):
                app.update()
            stage = omni.usd.get_context().get_stage()
            last_stage = stage
            UsdLux.DomeLight.Define(stage, "/World/SuperArmIndependentFingerLight").CreateIntensityAttr(700.0)
            if passive_visual_report is not None:
                passive_visual_report["snapshot_stage_summaries"].append(
                    {
                        "name": snapshot["name"],
                        "snapshot": str(snapshot_path),
                        **_validate_passive_linkage_stage_contract(
                            stage,
                            report["import"]["prim_path"],
                            snapshot["passive_linkage_contract"],
                        ),
                    }
                )
            frame = _capture(
                run_dir / f"hand_finger{target_finger}_close.png",
                stage,
                _passive_finger_root(stage, report["import"]["prim_path"], target_finger),
                closeup=True,
            )
            independent_frames.append(
                {
                    "name": snapshot["name"],
                    "target_finger": target_finger,
                    "snapshot": str(snapshot_path),
                    **frame,
                }
            )
        if report.get("profile") == "zip_learning":
            report["independent_finger_visuals"] = {
                "passed": True,
                "frames": independent_frames,
            }
        if last_stage is None:
            raise RuntimeError("no snapshot stage was opened")
        root_prim = last_stage.GetPrimAtPath(Sdf.Path(report["import"]["prim_path"]))
        if not root_prim.IsValid():
            raise RuntimeError("final snapshot has no robot root prim")
        report["screenshots"] = [_capture(run_dir / "whole_robot.png", last_stage, root_prim, closeup=False)]
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
