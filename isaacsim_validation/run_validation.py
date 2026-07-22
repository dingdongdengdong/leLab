"""Import and exercise a staged SuperArm URDF inside Isaac Sim 6.0."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--urdf", required=True, type=Path)
parser.add_argument("--run-dir", required=True, type=Path)
parser.add_argument(
    "--profile",
    choices=("raw", "aligned", "learning", "served", "zip_learning"),
    required=True,
)
parser.add_argument("--hand-usd-package", type=Path)
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
import omni.timeline  # noqa: E402
import omni.usd  # noqa: E402
from contracts import ARM_JOINTS, HAND_JOINTS, grasp_to_urdf_targets  # noqa: E402
from import_config import urdf_import_settings  # noqa: E402
from isaacsim.core.api import World  # noqa: E402
from isaacsim.core.experimental.prims import Articulation  # noqa: E402
from isaacsim.core.utils.extensions import enable_extension  # noqa: E402
from pxr import Usd, UsdGeom, UsdPhysics  # noqa: E402
from zip_hand_binding import bind_zip_hand_visuals  # noqa: E402

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

        stage = omni.usd.get_context().get_stage()
        if args.profile == "zip_learning":
            if args.hand_usd_package is None:
                raise RuntimeError("zip_learning requires --hand-usd-package")
            report["zip_hand_binding"] = bind_zip_hand_visuals(
                stage,
                prim_path,
                args.hand_usd_package,
            )
            report["phase"] = "bound_supplied_hand_usd_visuals"
            _write_json(report_path, report)
        # World creates the runtime /physicsScene in the current edit target.
        # Preserve the robot package before that scene-owned state exists so
        # the published asset remains a reusable robot, not a captured world.
        pristine_package = usd_path.read_bytes()
        world = World(stage_units_in_meters=1.0, physics_dt=1.0 / 120.0, rendering_dt=1.0 / 30.0)
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
        hand_snapshots = []
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
            snapshot = run_dir / f"hand_{name}_snapshot.usda"
            stage.Export(str(snapshot))
            hand_snapshots.append(
                {
                    "name": name,
                    "grasp": grasp,
                    "path": str(snapshot),
                    "bytes": snapshot.stat().st_size,
                    "method": "usd_stage_export_after_physics_readback",
                }
            )

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
        report["physics_snapshots"] = {
            "status": "PASS",
            "method": "flattened USD export after measured PhysX state",
            "hand_states": hand_snapshots,
        }
        numeric_passed = (
            report["import"]["dof_count"] == 13
            and report["schema"]["rigid_body_prim_count"] > 0
            and report["schema"]["collision_prim_count"] > 0
            and report["motion"]["arm_motion_passed"]
            and report["motion"]["hand_motion_passed"]
        )
        report["status"] = "NUMERIC_PASS" if numeric_passed else "FAIL"
        report["phase"] = "awaiting_static_visual_render" if numeric_passed else "complete"
        timeline.stop()
        usd_path.write_bytes(pristine_package)
        report["package_cleanup"] = {
            "restored_pristine_root_layer_after_runtime": True,
            "runtime_state_location": "physics_snapshots_only",
        }
        _write_json(report_path, report)
        return 0 if numeric_passed else 2
    except Exception as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        _write_json(report_path, report)
        raise
    finally:
        app.close()


raise SystemExit(main())
