"""Import and exercise a staged SuperArm URDF inside Isaac Sim 6.0."""

from __future__ import annotations

import argparse
import json
import math
import shutil
import tempfile
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--urdf", required=True, type=Path)
parser.add_argument("--run-dir", required=True, type=Path)
parser.add_argument("--profile", choices=("raw", "aligned", "learning", "served"), required=True)
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

import numpy as np  # noqa: E402
import omni.replicator.core as rep  # noqa: E402
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from contracts import ARM_JOINTS, HAND_JOINTS, grasp_to_urdf_targets  # noqa: E402
from import_config import urdf_import_settings  # noqa: E402
from isaacsim.core.api import World  # noqa: E402
from isaacsim.core.experimental.prims import Articulation  # noqa: E402
from isaacsim.core.utils.extensions import enable_extension  # noqa: E402
from pxr import Usd, UsdGeom, UsdLux, UsdPhysics  # noqa: E402
from visuals import image_has_detail, validate_direct_grasp_frames  # noqa: E402

enable_extension("isaacsim.asset.importer.urdf")
enable_extension("omni.kit.renderer.capture")
app.update()

from isaacsim.asset.importer.urdf import URDFImporter, URDFImporterConfig  # noqa: E402


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _flat(values) -> list[float]:
    if hasattr(values, "numpy"):
        values = values.numpy()
    array = np.asarray(values, dtype=np.float64)
    if array.ndim > 1:
        array = array[0]
    return [float(value) for value in array.tolist()]


def _bounds(stage: Usd.Stage, prim) -> tuple[list[float], list[float], list[float]]:
    cache = UsdGeom.BBoxCache(
        Usd.TimeCode.Default(),
        [UsdGeom.Tokens.default_, UsdGeom.Tokens.render],
        useExtentsHint=True,
    )
    box = cache.ComputeWorldBound(prim).ComputeAlignedRange()
    minimum = [float(value) for value in box.GetMin()]
    maximum = [float(value) for value in box.GetMax()]
    center = [(low + high) / 2.0 for low, high in zip(minimum, maximum, strict=True)]
    return minimum, maximum, center


def _camera_pose(stage: Usd.Stage, prim, *, closeup: bool) -> tuple[list[float], list[float]]:
    minimum, maximum, center = _bounds(stage, prim)
    span = max(maximum[index] - minimum[index] for index in range(3))
    radius = max(span, 0.08)
    factor = 2.4 if closeup else 2.2
    eye = [center[0] + factor * radius, center[1] - factor * radius, center[2] + radius]
    return eye, center


def _capture(path: Path, stage: Usd.Stage, prim, *, closeup: bool) -> dict:
    eye, target = _camera_pose(stage, prim, closeup=closeup)
    path.parent.mkdir(parents=True, exist_ok=True)
    output_dir = Path(tempfile.mkdtemp(prefix="isaac-superarm-capture-"))
    try:
        with rep.new_layer():
            camera = rep.create.camera(position=eye, look_at=target, focal_length=35)
            product = rep.create.render_product(camera, (1280, 720))
            writer = rep.WriterRegistry.get("BasicWriter")
            writer.initialize(output_dir=str(output_dir), rgb=True)
            writer.attach([product])
            for _ in range(6):
                rep.orchestrator.step(rt_subframes=4)
            writer.detach()
        frames = sorted(output_dir.glob("rgb*.png"))
        if not frames:
            raise RuntimeError("Replicator did not create an RGB frame")
        shutil.copyfile(frames[-1], path)
    finally:
        shutil.rmtree(output_dir, ignore_errors=True)

    if not image_has_detail(path):
        raise RuntimeError("Replicator screenshot has no visible detail")
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "eye": eye,
        "target": target,
        "method": "replicator_render_product",
    }


def _prim_named(stage: Usd.Stage, name: str):
    matches = [prim for prim in stage.Traverse() if prim.GetName() == name]
    if len(matches) != 1:
        raise RuntimeError(f"expected one prim named {name!r}, found {len(matches)}")
    return matches[0]


def _schema_report(stage: Usd.Stage, root_prim, prim_path: str) -> dict:
    prefix = prim_path.rstrip("/") + "/"
    prims = [
        prim
        for prim in stage.Traverse()
        if str(prim.GetPath()) == prim_path or str(prim.GetPath()).startswith(prefix)
    ]
    minimum, maximum, _ = _bounds(stage, root_prim)
    dimensions = [maximum[index] - minimum[index] for index in range(3)]
    return {
        "prim_path": prim_path,
        "prim_count": len(prims),
        "mesh_prim_count": sum(prim.IsA(UsdGeom.Mesh) for prim in prims),
        "rigid_body_prim_count": sum(prim.HasAPI(UsdPhysics.RigidBodyAPI) for prim in prims),
        "collision_prim_count": sum(prim.HasAPI(UsdPhysics.CollisionAPI) for prim in prims),
        "articulation_root_count": sum(prim.HasAPI(UsdPhysics.ArticulationRootAPI) for prim in prims),
        "bounds_min": minimum,
        "bounds_max": maximum,
        "dimensions_m": dimensions,
        "finite_positive_bounds": all(math.isfinite(value) and value > 0 for value in dimensions),
    }


def _command_targets(art: Articulation, names: tuple[str, ...], targets: dict[str, float]) -> None:
    indices = np.asarray([art.dof_names.index(name) for name in names], dtype=np.int32)
    values = np.asarray([targets[name] for name in names], dtype=np.float32)
    art.set_dof_position_targets(values, dof_indices=indices)


def _set_positions(art: Articulation, names: tuple[str, ...], positions: dict[str, float]) -> None:
    indices = np.asarray([art.dof_names.index(name) for name in names], dtype=np.int32)
    values = np.asarray([positions[name] for name in names], dtype=np.float32)
    art.set_dof_positions(values, dof_indices=indices)


def _step(world: World, count: int, *, render: bool = False) -> None:
    for _ in range(count):
        world.step(render=render)


def main() -> int:
    run_dir = args.run_dir.resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    urdf = args.urdf.resolve()
    report_path = run_dir / "isaac-report.json"
    report: dict = {
        "status": "FAIL",
        "profile": args.profile,
        "isaac_sim_version": "6.0.0",
        "input_urdf": str(urdf),
        "phase": "initializing_importer",
    }
    _write_json(report_path, report)

    try:
        import_config = URDFImporterConfig(
            urdf_path=str(urdf),
            usd_path=str(run_dir),
            **urdf_import_settings(),
        )
        report["phase"] = "importing_urdf"
        _write_json(report_path, report)
        print(f"[superarm-isaac] importing {urdf}", flush=True)
        usd_path = Path(URDFImporter(import_config).import_urdf()).resolve()
        print(f"[superarm-isaac] imported {usd_path}", flush=True)
        report["phase"] = "opening_stage"
        _write_json(report_path, report)
        report["output_usd"] = str(usd_path)
        if not usd_path.is_file():
            raise RuntimeError(f"URDF importer did not write USD: {usd_path}")
        if not omni.usd.get_context().open_stage(str(usd_path)):
            raise RuntimeError(f"Isaac could not open imported USD: {usd_path}")
        print("[superarm-isaac] stage opened", flush=True)
        for _ in range(8):
            app.update()
        prim_path = f"/{urdf.stem}"

        world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 120.0, rendering_dt=1.0 / 30.0)
        stage = omni.usd.get_context().get_stage()
        if not stage.GetPrimAtPath("/World/SuperArmValidationLight").IsValid():
            UsdLux.DomeLight.Define(stage, "/World/SuperArmValidationLight").CreateIntensityAttr(700.0)
        timeline = omni.timeline.get_timeline_interface()
        timeline.play()
        art = Articulation(prim_path)
        world.reset()
        _step(world, 2)
        if not art.is_physics_tensor_entity_valid():
            raise RuntimeError("Isaac physics tensor did not initialize the imported articulation")
        dof_names = list(art.dof_names)
        expected = [*ARM_JOINTS, *HAND_JOINTS]
        missing = [name for name in expected if name not in dof_names]
        if missing:
            raise RuntimeError(f"imported articulation is missing expected joints: {missing}")

        root_prim = stage.GetPrimAtPath(prim_path)
        if not root_prim.IsValid():
            raise RuntimeError(f"imported root prim is invalid: {prim_path}")
        report["import"] = {
            "status": "PASS",
            "prim_path": prim_path,
            "dof_count": art.num_dofs,
            "dof_names": dof_names,
            "expected_movable_joint_count": 13,
        }
        report["schema"] = _schema_report(stage, root_prim, prim_path)
        if not report["schema"]["finite_positive_bounds"]:
            raise RuntimeError("imported asset has invalid or zero world bounds")

        report["phase"] = "testing_motion"
        _write_json(report_path, report)
        arm_results = []
        for index, joint_name in enumerate(ARM_JOINTS):
            before = _flat(art.get_dof_positions())[dof_names.index(joint_name)]
            target = 0.18 if index % 2 == 0 else -0.18
            _command_targets(art, (joint_name,), {joint_name: target})
            _step(world, 90)
            after = _flat(art.get_dof_positions())[dof_names.index(joint_name)]
            arm_results.append(
                {
                    "joint": joint_name,
                    "before": before,
                    "target": target,
                    "after": after,
                    "delta": after - before,
                    "passed": abs(after - before) >= 0.03,
                }
            )

        neutral_arm = dict.fromkeys(ARM_JOINTS, 0.0)
        _set_positions(art, ARM_JOINTS, neutral_arm)
        _command_targets(art, ARM_JOINTS, neutral_arm)
        _step(world, 2)
        neutral_positions = _flat(art.get_dof_positions())
        capture_arm_pose = {
            joint: neutral_positions[dof_names.index(joint)] for joint in ARM_JOINTS
        }
        if max(abs(value) for value in capture_arm_pose.values()) > 0.005:
            raise RuntimeError(f"could not restore neutral arm pose for direct hand captures: {capture_arm_pose}")

        hand_results = []
        hand_frames = []
        hand_root = _prim_named(stage, "r_wrist_interface")
        for name, grasp in (("open", 0.0), ("half_close", 0.5), ("close", 1.0)):
            targets = grasp_to_urdf_targets(grasp)
            _command_targets(art, HAND_JOINTS, targets)
            _step(world, 120)
            positions = _flat(art.get_dof_positions())
            measured = {joint: positions[dof_names.index(joint)] for joint in HAND_JOINTS}
            hand_results.append(
                {
                    "name": name,
                    "grasp": grasp,
                    "targets": targets,
                    "measured": measured,
                    "max_error": max(abs(measured[joint] - targets[joint]) for joint in HAND_JOINTS),
                }
            )
            frame = _capture(run_dir / f"hand_{name}.png", stage, hand_root, closeup=True)
            hand_frames.append({"name": name, **frame})

        hand_motion_passed = all(
            hand_results[0]["measured"][joint]
            < hand_results[1]["measured"][joint]
            < hand_results[2]["measured"][joint]
            for joint in HAND_JOINTS
        )
        report["motion"] = {
            "logical_action_width": 6,
            "physical_movable_joint_count": 13,
            "arm": arm_results,
            "capture_arm_pose": capture_arm_pose,
            "hand": hand_results,
            "arm_motion_passed": all(item["passed"] for item in arm_results),
            "hand_motion_passed": hand_motion_passed,
        }
        report["visual_motion"] = validate_direct_grasp_frames(hand_frames)
        report["visual_motion"]["frames"] = hand_frames
        report["phase"] = "capturing_visuals"
        _write_json(report_path, report)
        whole_robot = run_dir / "whole_robot.png"
        screenshots = [_capture(whole_robot, stage, root_prim, closeup=False)]
        report["screenshots"] = screenshots
        report["visual_boundary"] = (
            "Raw profile retains the generated full hand visual shell; detailed hand visual geometry "
            "is not guaranteed to be partitioned across moving finger links."
            if args.profile == "raw"
            else (
                "Aligned profile applies LeLab's wrist and joint-5 transforms while preserving the generated hand visuals."
                if args.profile == "aligned"
                else "Served profile matches LeLab's URDF tree after hand visuals are removed for the browser MJCF overlay."
            )
        )
        passed = (
            report["import"]["dof_count"] == 13
            and report["schema"]["rigid_body_prim_count"] > 0
            and report["schema"]["collision_prim_count"] > 0
            and report["motion"]["arm_motion_passed"]
            and report["motion"]["hand_motion_passed"]
            and report["visual_motion"]["passed"]
        )
        report["status"] = "PASS" if passed else "FAIL"
        report["phase"] = "complete"
        _write_json(report_path, report)
        timeline.stop()
        return 0 if passed else 2
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(report_path, report)
        raise
    finally:
        app.close()


raise SystemExit(main())
