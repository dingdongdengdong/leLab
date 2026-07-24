from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest


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
    assert "GetRootLayer().Save()" not in source


def test_author_boundary_deactivates_only_existing_frame_first_core_refs() -> None:
    from isaacsim_validation.passive_linkage_usd import FRAME_FIRST_CORE_REF_NAMES

    assert FRAME_FIRST_CORE_REF_NAMES == ("zip_proximal_1", "zip_distal_1")
    source = (Path(__file__).parents[1] / "isaacsim_validation" / "passive_linkage_usd.py").read_text(
        encoding="utf-8"
    )
    assert "prim.SetActive(False)" in source
    assert "deactivated_frame_first_core_ref_count" in source


def test_author_snapshot_keeps_original_bytes_when_flatten_export_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isaacsim_validation.passive_linkage import solve_passive_linkage
    from isaacsim_validation.passive_linkage_usd import author_passive_linkage_snapshot

    _install_fake_pxr(monkeypatch)
    snapshot = tmp_path / "snapshot.usda"
    original = b"#usda 1.0\noriginal snapshot\n"
    snapshot.write_bytes(original)
    instances = tmp_path / "source_zip" / "payloads" / "instances.usda"
    instances.parent.mkdir(parents=True)
    instances.write_text("#usda 1.0\n", encoding="utf-8")
    stage = _FakeStage(snapshot, fail_export=True)

    with pytest.raises(RuntimeError, match="fake flattened export failure"):
        author_passive_linkage_snapshot(
            stage,
            "/Robot",
            solve_passive_linkage(
                {f"finger{finger}_motor{motor}": 0.05 for finger in range(1, 5) for motor in range(1, 3)}
            ),
            instances,
        )

    assert stage.root_layer.save_calls == 0
    assert snapshot.read_bytes() == original


def test_live_runtime_authors_once_then_updates_without_saving_or_flattening(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from isaacsim_validation.passive_linkage import solve_passive_linkage
    from isaacsim_validation.passive_linkage_usd import (
        author_or_update_passive_linkage_runtime,
    )

    _install_fake_pxr(monkeypatch)
    stage = _FakeStage(tmp_path / "live.usda", fail_export=False)
    instances = tmp_path / "zip_hand_payloads" / "instances.usda"
    instances.parent.mkdir()
    instances.write_text("#usda 1.0\n", encoding="utf-8")
    open_poses = solve_passive_linkage(
        {f"finger{finger}_motor{motor}": 0.05 for finger in range(1, 5) for motor in range(1, 3)}
    )
    half_poses = solve_passive_linkage(
        {f"finger{finger}_motor{motor}": 0.55 for finger in range(1, 5) for motor in range(1, 3)}
    )

    created = author_or_update_passive_linkage_runtime(stage, "/Robot", open_poses, instances)
    updated = author_or_update_passive_linkage_runtime(stage, "/Robot", half_poses, instances)

    part_prims = [prim for prim in stage.prims.values() if "/part_" in str(prim.GetPath())]
    assert created["runtime_created"] is True
    assert created["runtime_updated"] is False
    assert updated["runtime_created"] is False
    assert updated["runtime_updated"] is True
    assert created["visual_part_count"] == updated["visual_part_count"] == 88
    assert sum(len(prim.references) for prim in part_prims) == 88
    assert all(prim.instanceable for prim in part_prims)
    runtime_root = stage.prims["/LeLabPassiveLinkageVisuals"]
    assert (
        runtime_root.xform_ops["xformOp:transform:passiveRuntimeParent"].set_calls
        == 2
    )
    assert all(
        prim.xform_ops["xformOp:translate:passiveRuntime"].set_calls == 2
        for prim in part_prims
    )
    assert all(
        prim.xform_ops["xformOp:orient:passiveRuntime"].set_calls == 2
        for prim in part_prims
    )
    assert stage.flatten_calls == 0
    assert stage.root_layer.save_calls == 0
    assert sum(not prim.active for prim in stage.prims.values()) == 8


def test_source_path_leak_validation_rejects_absolute_assets_but_allows_flattened_prototypes(
    tmp_path: Path,
) -> None:
    from isaacsim_validation.passive_linkage_usd import validate_no_source_path_leaks

    instances = tmp_path / "zip_hand_payloads" / "instances.usda"
    instances.parent.mkdir()
    instances.touch()
    clean_flattened_text = """
# Provenance from a temporary export layer is diagnostic text, not an asset dependency.
custom string passive_debug_provenance = "/tmp/pytest/export-session/layer.usda"
def Xform "part_026" (
    instanceable = true
    prepend references = </__Prototype_1>
)
{
}
"""
    validate_no_source_path_leaks(clean_flattened_text, instances)

    bad_snippets = [
        f"prepend references = @{instances}@</Instances/mjcf_026>",
        f"asset inputs:file = @{instances.parent / 'geometries.usd'}@",
        "prepend references = @./geometries.usd@</Geometry>",
        "prepend references = @../payload.usd@</Payload>",
        "prepend references = @/tmp/leaked.usd@</Root>",
        "prepend references = @/home/dong/source-root/leaked.usd@</Root>",
    ]
    for bad_text in bad_snippets:
        with pytest.raises(RuntimeError, match="external source asset path leak"):
            validate_no_source_path_leaks(bad_text, instances)


def test_passive_metadata_authoring_requires_create_attribute_and_set_success() -> None:
    from isaacsim_validation.passive_linkage_usd import _set_custom_attr

    with pytest.raises(RuntimeError, match="required custom attribute passive_source_index"):
        _set_custom_attr(object(), "passive_source_index", "Int", 3)

    prim = _FakePrim("/Robot/passive_linkage_visuals/finger1/part_003")
    prim.fail_attribute_set = True
    with pytest.raises(RuntimeError, match="could not set required custom attribute passive_source_index"):
        _set_custom_attr(prim, "passive_source_index", "Int", 3)


def test_author_snapshot_uses_mandatory_sdf_value_type_names_directly() -> None:
    source = (Path(__file__).parents[1] / "isaacsim_validation" / "passive_linkage_usd.py").read_text(
        encoding="utf-8"
    )

    assert "_sdf_value_type" not in source
    assert "Sdf.ValueTypeNames.Int" in source
    assert "Sdf.ValueTypeNames.String" in source
    assert 'hasattr(prim, "CreateAttribute")' not in source


class _FakePath(str):
    def AppendChild(self, child: str) -> _FakePath:  # noqa: N802 - mimics pxr API
        return _FakePath(f"{self.rstrip('/')}/{child}")


class _FakeAttribute:
    def __init__(self, prim: _FakePrim, name: str, value_type, custom: bool):
        self.prim = prim
        self.name = name
        self.value_type = value_type
        self.custom = custom
        self.value = None

    def Set(self, value) -> bool:  # noqa: N802 - mimics pxr API
        if self.prim.fail_attribute_set:
            return False
        self.value = value
        self.prim.attributes[self.name] = self
        return True


class _FakePrim:
    def __init__(self, path: str, type_name: str = "Xform"):
        self.path = _FakePath(path)
        self.type_name = type_name
        self.active = True
        self.instanceable = False
        self.fail_attribute_set = False
        self.references: list[tuple[str, str]] = []
        self.attributes: dict[str, _FakeAttribute] = {}
        self.xform_ops: dict[str, _FakeOp] = {}

    def __bool__(self) -> bool:
        return True

    def GetName(self) -> str:  # noqa: N802 - mimics pxr API
        return str(self.path).rsplit("/", 1)[-1]

    def GetPath(self) -> _FakePath:  # noqa: N802 - mimics pxr API
        return self.path

    def GetTypeName(self) -> str:  # noqa: N802 - mimics pxr API
        return self.type_name

    def GetAppliedSchemas(self) -> tuple[str, ...]:  # noqa: N802 - mimics pxr API
        return ()

    def SetActive(self, active: bool) -> None:  # noqa: N802 - mimics pxr API
        self.active = active

    def IsActive(self) -> bool:  # noqa: N802 - mimics pxr API
        return self.active

    def GetReferences(self):  # noqa: N802 - mimics pxr API
        return self

    def AddReference(self, asset_path: str, prim_path: str) -> None:  # noqa: N802 - mimics pxr API
        self.references.append((asset_path, prim_path))

    def SetInstanceable(self, instanceable: bool) -> None:  # noqa: N802 - mimics pxr API
        self.instanceable = instanceable

    def CreateAttribute(self, name: str, value_type, custom: bool = False):  # noqa: N802 - mimics pxr API
        return _FakeAttribute(self, name, value_type, custom)

    def GetAttribute(self, name: str):  # noqa: N802 - mimics pxr API
        return self.xform_ops.get(name)


class _FakeRootLayer:
    def __init__(self, path: Path):
        self.realPath = str(path)
        self.path = path
        self.save_calls = 0

    def Save(self) -> None:  # noqa: N802 - mimics pxr API
        self.save_calls += 1
        self.path.write_bytes(b"mutated by root save\n")


class _FakeFlattened:
    def __init__(self, fail_export: bool):
        self.fail_export = fail_export

    def Export(self, path: str) -> None:  # noqa: N802 - mimics pxr API
        if self.fail_export:
            raise RuntimeError("fake flattened export failure")
        Path(path).write_text("#usda 1.0\n", encoding="utf-8")


class _FakeStage:
    def __init__(self, path: Path, *, fail_export: bool):
        self.root_layer = _FakeRootLayer(path)
        self.fail_export = fail_export
        self.flatten_calls = 0
        self.prims: dict[str, _FakePrim] = {
            "/Robot": _FakePrim("/Robot"),
            "/Robot/r_wrist_interface": _FakePrim("/Robot/r_wrist_interface"),
        }
        for finger in range(1, 5):
            for link, core_name in (
                ("proximal", "zip_proximal_1"),
                ("distal", "zip_distal_1"),
            ):
                path_string = f"/Robot/finger{finger}_{link}/{core_name}"
                self.prims[path_string] = _FakePrim(path_string)

    def GetRootLayer(self) -> _FakeRootLayer:  # noqa: N802 - mimics pxr API
        return self.root_layer

    def Traverse(self):  # noqa: N802 - mimics pxr API
        return list(self.prims.values())

    def Flatten(self) -> _FakeFlattened:  # noqa: N802 - mimics pxr API
        self.flatten_calls += 1
        return _FakeFlattened(self.fail_export)

    def GetPrimAtPath(self, path: _FakePath):  # noqa: N802 - mimics pxr API
        return self.prims.get(str(path))

    def define_prim(self, path: _FakePath) -> _FakePrim:
        prim = self.prims.get(str(path))
        if prim is None:
            prim = _FakePrim(str(path))
            self.prims[str(path)] = prim
        return prim


class _FakeXform:
    def __init__(self, prim: _FakePrim):
        self.prim = prim

    @classmethod
    def Define(cls, stage: _FakeStage, path: _FakePath):  # noqa: N802 - mimics pxr API
        return cls(stage.define_prim(path))

    def GetPrim(self) -> _FakePrim:  # noqa: N802 - mimics pxr API
        return self.prim

    def GetPath(self) -> _FakePath:  # noqa: N802 - mimics pxr API
        return self.prim.GetPath()


class _FakeOp:
    def __init__(self, op_type: str):
        self.op_type = op_type
        self.value = None
        self.set_calls = 0

    def GetOpType(self) -> str:  # noqa: N802 - mimics pxr API
        return self.op_type

    def IsValid(self) -> bool:  # noqa: N802 - mimics pxr API
        return True

    def Set(self, value) -> bool:  # noqa: N802 - mimics pxr API
        self.value = value
        self.set_calls += 1
        return True


class _FakeXformable:
    def __init__(self, prim: _FakePrim):
        self.prim = prim

    def AddTranslateOp(self, *, opSuffix: str = "", **_kwargs) -> _FakeOp:  # noqa: N802, N803 - pxr API
        op = _FakeOp("translate")
        self.prim.xform_ops[f"xformOp:translate:{opSuffix}"] = op
        return op

    def AddOrientOp(self, *, opSuffix: str = "", **_kwargs) -> _FakeOp:  # noqa: N802, N803 - pxr API
        op = _FakeOp("orient")
        self.prim.xform_ops[f"xformOp:orient:{opSuffix}"] = op
        return op

    def AddTransformOp(self, *, opSuffix: str = "", **_kwargs) -> _FakeOp:  # noqa: N802, N803 - pxr API
        op = _FakeOp("transform")
        self.prim.xform_ops[f"xformOp:transform:{opSuffix}"] = op
        return op

    def ComputeLocalToWorldTransform(self, _time_code):  # noqa: N802 - mimics pxr API
        return ("world", str(self.prim.GetPath()))


class _FakeXformOp:
    PrecisionDouble = "double"
    TypeTranslate = "translate"
    TypeOrient = "orient"
    TypeTransform = "transform"

    def __new__(cls, attribute: _FakeOp):
        return attribute


def _install_fake_pxr(monkeypatch: pytest.MonkeyPatch) -> None:
    pxr = types.ModuleType("pxr")
    pxr.Gf = types.SimpleNamespace(
        Vec3d=lambda *values: values,
        Quatd=lambda real, imaginary: (real, imaginary),
    )
    pxr.Sdf = types.SimpleNamespace(
        Path=lambda value: value,
        ValueTypeNames=types.SimpleNamespace(Int="Int", String="String"),
    )
    pxr.Usd = types.SimpleNamespace(
        Stage=types.SimpleNamespace(Open=lambda _path: None),
        TimeCode=types.SimpleNamespace(Default=lambda: "default"),
    )
    pxr.UsdGeom = types.SimpleNamespace(
        Xform=_FakeXform,
        Xformable=_FakeXformable,
        XformOp=_FakeXformOp,
    )
    monkeypatch.setitem(sys.modules, "pxr", pxr)
