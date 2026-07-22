"""Run Isaac Sim 6.0 robot and SimReady rules against one USD asset."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--usd", required=True, type=Path)
parser.add_argument("--output", required=True, type=Path)
args, _ = parser.parse_known_args()

from isaacsim import SimulationApp  # noqa: E402

app = SimulationApp({"headless": True, "renderer": "RaytracedLighting"})

from isaacsim.core.utils.extensions import enable_extension  # noqa: E402

enable_extension("isaacsim.asset.validation")
app.update()

import isaacsim.asset.validation  # noqa: E402, F401
from omni.asset_validator.core import ValidationEngine, ValidationRulesRegistry  # noqa: E402
from pxr import Usd, UsdGeom, UsdPhysics  # noqa: E402
from validation_policy import asset_validator_verdict  # noqa: E402

CATEGORIES = (
    "IsaacSim.PhysicsRules",
    "IsaacSim.RobotRules",
    "IsaacSim.SimReadyAssetRules",
)


def _issue_dict(issue) -> dict:
    rule = issue.rule
    return {
        "severity": getattr(issue.severity, "name", str(issue.severity)),
        "rule": getattr(rule, "__name__", str(rule)),
        "message": issue.message,
        "at": str(issue.at) if issue.at is not None else None,
        "code": issue.code,
        "has_fix": bool(issue.suggestions),
    }


def main() -> int:
    usd_path = args.usd.resolve()
    output_path = args.output.resolve()
    stage = Usd.Stage.Open(str(usd_path))
    if stage is None:
        raise RuntimeError(f"cannot open {usd_path}")
    stage.Load()

    engine = ValidationEngine(init_rules=False, variants=False)
    registered: dict[str, list[str]] = {}
    for category in CATEGORIES:
        rules = list(ValidationRulesRegistry.rules(category))
        registered[category] = [rule.__name__ for rule in rules]
        for rule in rules:
            engine.enable_rule(rule)

    issues = [_issue_dict(issue) for issue in engine.validate(stage)]
    prims = list(stage.Traverse())
    default = stage.GetDefaultPrim()
    report = {
        "target_usd": str(usd_path),
        "runtime": "nvcr.io/nvidia/isaac-sim:6.0.0 (internal 6.0.0-rc.59)",
        "default_prim": str(default.GetPath()) if default else None,
        "meters_per_unit": UsdGeom.GetStageMetersPerUnit(stage),
        "up_axis": UsdGeom.GetStageUpAxis(stage),
        "prim_count": len(prims),
        "mesh_count": sum(prim.IsA(UsdGeom.Mesh) for prim in prims),
        "rigid_body_count": sum(prim.HasAPI(UsdPhysics.RigidBodyAPI) for prim in prims),
        "collision_count": sum(prim.HasAPI(UsdPhysics.CollisionAPI) for prim in prims),
        "mass_api_count": sum(prim.HasAPI(UsdPhysics.MassAPI) for prim in prims),
        "articulation_root_count": sum(prim.HasAPI(UsdPhysics.ArticulationRootAPI) for prim in prims),
        "revolute_joint_count": sum(prim.IsA(UsdPhysics.RevoluteJoint) for prim in prims),
        "fixed_joint_count": sum(prim.IsA(UsdPhysics.FixedJoint) for prim in prims),
        "root_applied_schemas": list(default.GetAppliedSchemas()) if default else [],
        "registered_rules": registered,
        "issue_counts": dict(Counter(issue["severity"] for issue in issues)),
        "issues": issues,
        "verdict": asset_validator_verdict(issues),
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("target_usd", "issue_counts", "verdict")}, indent=2))
    return 0 if report["verdict"]["passed"] else 2


try:
    raise SystemExit(main())
finally:
    app.close()
