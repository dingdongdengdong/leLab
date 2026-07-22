from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def test_passive_linkage_usd_imports_without_pxr_or_isaac() -> None:
    code = """
import builtins
real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name == 'pxr' or name.startswith('pxr.') or name == 'isaacsim' or name.startswith('isaacsim.'):
        raise ModuleNotFoundError(f'blocked runtime import: {name}')
    return real_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
from isaacsim_validation.passive_linkage_usd import build_passive_linkage_author_plan
assert callable(build_passive_linkage_author_plan)
"""
    subprocess.run([sys.executable, "-c", code], check=True)


def test_author_plan_uses_wrist_local_unique_xforms_and_exact_instance_refs() -> None:
    from isaacsim_validation.passive_linkage import solve_passive_linkage
    from isaacsim_validation.passive_linkage_usd import build_passive_linkage_author_plan

    measured = {
        f"finger{finger}_motor{motor}": 0.25 + motor * 0.1 for finger in range(1, 5) for motor in range(1, 3)
    }
    plan = build_passive_linkage_author_plan(solve_passive_linkage(measured))

    assert plan["mode"] == "frame_plus_passive_linkage_no_shells"
    assert plan["visual_part_count"] == 88
    assert plan["parts_per_finger"] == {1: 22, 2: 22, 3: 22, 4: 22}
    assert plan["excluded_shell_visual_count"] == 0
    assert plan["deactivated_frame_first_core_ref_count"] == 8

    paths = [part["xform_path"] for part in plan["parts"]]
    assert len(paths) == len(set(paths)) == 88
    assert all(
        "/r_wrist_interface/passive_linkage_visuals/finger" in path
        and path.rsplit("/", 1)[-1].startswith("part_")
        for path in paths
    )
    assert all(part["reference_prim"].startswith("/Instances/") for part in plan["parts"])
    assert all("proximal_shell" not in part["reference_prim"] for part in plan["parts"])
    assert all("distal_shell" not in part["reference_prim"] for part in plan["parts"])


def test_author_boundary_keeps_pxr_imports_lazy_and_has_no_physics_authoring() -> None:
    source = (Path(__file__).parents[1] / "isaacsim_validation" / "passive_linkage_usd.py").read_text(
        encoding="utf-8"
    )

    assert "from pxr import" not in source.split("def author_passive_linkage_snapshot", 1)[0]
    assert "UsdGeom.Xform.Define" in source
    assert "UsdPhysics" not in source
    assert ".AddReference(" in source
    assert ".SetInstanceable(True)" in source
    assert ".Flatten()" in source
    assert "os.replace(" in source


def test_author_boundary_deactivates_only_existing_frame_first_core_refs() -> None:
    from isaacsim_validation.passive_linkage_usd import FRAME_FIRST_CORE_REF_NAMES

    assert FRAME_FIRST_CORE_REF_NAMES == ("zip_proximal_1", "zip_distal_1")
    source = (Path(__file__).parents[1] / "isaacsim_validation" / "passive_linkage_usd.py").read_text(
        encoding="utf-8"
    )
    assert "prim.SetActive(False)" in source
    assert "deactivated_frame_first_core_ref_count" in source
