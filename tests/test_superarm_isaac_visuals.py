from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from isaacsim_validation.visuals import (
    crop_hand_closeup,
    image_has_detail,
    validate_direct_grasp_frames,
    validate_independent_finger_linkage_sequence,
    validate_passive_linkage_motion_sequence,
    validate_passive_linkage_stage_contract,
    validate_passive_linkage_visual_summary,
    zip_learning_visual_boundary,
)


def test_blank_frame_is_rejected(tmp_path: Path):
    frame = tmp_path / "blank.png"
    Image.new("RGB", (1280, 720), (210, 210, 210)).save(frame)

    assert not image_has_detail(frame)


def test_hand_crop_is_derived_from_visible_isaac_frame(tmp_path: Path):
    source = tmp_path / "whole.png"
    target = tmp_path / "hand.png"
    frame = Image.new("RGB", (1280, 720), (210, 210, 210))
    draw = ImageDraw.Draw(frame)
    draw.rectangle((520, 40, 760, 300), fill=(40, 60, 80))
    frame.save(source)

    evidence = crop_hand_closeup(source, target)

    assert image_has_detail(target)
    assert evidence["method"] == "crop_from_whole_robot_isaac_frame"
    assert evidence["source"] == str(source)


def test_hand_crop_rejects_blank_source(tmp_path: Path):
    source = tmp_path / "blank.png"
    Image.new("RGB", (1280, 720), (210, 210, 210)).save(source)

    with pytest.raises(RuntimeError, match="source frame has no visible detail"):
        crop_hand_closeup(source, tmp_path / "hand.png")


def test_direct_grasp_sequence_requires_three_changed_camera_frames(tmp_path: Path):
    frames = []
    for index, name in enumerate(("open", "half_close", "close")):
        path = tmp_path / f"hand_{name}.png"
        frame = Image.new("RGB", (320, 240), (210, 210, 210))
        draw = ImageDraw.Draw(frame)
        draw.rectangle((80 + index * 20, 40, 160 + index * 20, 190), fill=(30, 60, 90))
        frame.save(path)
        frames.append(
            {
                "name": name,
                "path": str(path),
                "method": "static_replicator_from_physics_snapshot",
            }
        )

    result = validate_direct_grasp_frames(frames)

    assert result["passed"] is True
    assert result["frame_names"] == ["open", "half_close", "close"]
    assert min(result["adjacent_rms_difference"]) > 1.0


def test_direct_grasp_sequence_rejects_crop_evidence(tmp_path: Path):
    path = tmp_path / "hand.png"
    frame = Image.new("RGB", (320, 240), (210, 210, 210))
    ImageDraw.Draw(frame).rectangle((80, 40, 160, 190), fill=(30, 60, 90))
    frame.save(path)
    frames = [
        {"name": name, "path": str(path), "method": "crop_from_whole_robot_isaac_frame"}
        for name in ("open", "half_close", "close")
    ]

    with pytest.raises(RuntimeError, match="direct camera"):
        validate_direct_grasp_frames(frames)


def test_direct_grasp_sequence_rejects_static_visuals(tmp_path: Path):
    frames = []
    for name in ("open", "half_close", "close"):
        path = tmp_path / f"hand_{name}.png"
        frame = Image.new("RGB", (320, 240), (210, 210, 210))
        ImageDraw.Draw(frame).rectangle((80, 40, 160, 190), fill=(30, 60, 90))
        frame.save(path)
        frames.append({"name": name, "path": str(path), "method": "replicator_render_product"})

    with pytest.raises(RuntimeError, match="did not visibly change"):
        validate_direct_grasp_frames(frames)


def test_isaac_camera_bounds_include_render_purpose_payloads():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    assert "[UsdGeom.Tokens.default_, UsdGeom.Tokens.render]" in renderer
    assert "useExtentsHint=closeup" in renderer
    assert "factor = 2.4 if closeup else 2.2" in renderer


def test_direct_hand_capture_restores_a_deterministic_neutral_arm_pose():
    runner = (Path(__file__).parents[1] / "isaacsim_validation" / "run_validation.py").read_text()

    reset = runner.index("_set_positions(art, ARM_JOINTS, neutral_arm)")
    first_hand_snapshot = runner.index('run_dir / f"hand_{name}_snapshot.usda"')
    assert reset < first_hand_snapshot
    assert '"capture_arm_pose": capture_arm_pose' in runner


def test_runtime_wrapper_rejects_a_non_pass_report():
    wrapper = (Path(__file__).parents[1] / "isaacsim_validation" / "run_isaacsim60_validation.sh").read_text()

    assert 'report.get("status") != "PASS"' in wrapper
    assert "Isaac validation report is not PASS" in wrapper
    assert "render_physics_snapshots.py" in wrapper
    assert 'numeric_container_name="${container_prefix}-numeric"' in wrapper
    assert 'render_container_name="${container_prefix}-render"' in wrapper


def test_numeric_runner_exports_each_measured_hand_state_before_visual_render():
    runner = (Path(__file__).parents[1] / "isaacsim_validation" / "run_validation.py").read_text()

    readback = runner.index("positions = _flat(art.get_dof_positions())")
    snapshot = runner.index("stage.Export(str(snapshot))")
    reopen_author = runner.index("author_passive_linkage_snapshot(")
    awaiting_render = runner.index('"awaiting_static_visual_render"')
    assert readback < snapshot < reopen_author < awaiting_render
    assert "passive_linkage_contract" in runner
    assert "solve_passive_linkage(measured)" in runner


def test_numeric_runner_restores_pristine_package_after_runtime_snapshots():
    runner = (Path(__file__).parents[1] / "isaacsim_validation" / "run_validation.py").read_text()

    pristine = runner.index("pristine_package = usd_path.read_bytes()")
    world = runner.index("world = World(")
    snapshot = runner.index("stage.Export(str(snapshot))")
    restore = runner.index("usd_path.write_bytes(pristine_package)")
    finally_restore = runner.index("finally:")
    assert pristine < world < snapshot < finally_restore < restore
    assert '"restored_pristine_root_layer_after_runtime"' in runner
    assert '"physics_snapshots_only"' in runner
    assert "package_cleanup_written" in runner


def test_static_snapshot_renderer_uses_one_fixed_camera_for_all_hand_states():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    assert "fixed_hand_pose = None" in renderer
    assert "fixed_pose=fixed_hand_pose" in renderer
    assert '"static_replicator_from_physics_snapshot"' in renderer


def test_static_snapshot_renderer_enables_capture_extension_before_rendering():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    extension = 'enable_extension("omni.kit.renderer.capture")'
    assert extension in renderer
    assert renderer.index(extension) < renderer.index("def _capture(")


def test_static_snapshot_renderer_uses_close_range_camera_clipping():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    assert "clipping_range=(0.001, 100.0)" in renderer
    assert "rep.orchestrator.step(rt_subframes=8)" in renderer


def _fake_passive_part(finger: int, index: int, *, offset: float = 0.0, **overrides):
    part = {
        "finger": finger,
        "source_index": finger * 100 + index,
        "source_prim": f"mjcf_{finger:03d}_{index:03d}_linkage",
        "reference_prim": f"/Instances/mjcf_{finger:03d}_{index:03d}_linkage",
        "xform_path": f"/r_wrist_interface/passive_linkage_visuals/finger{finger}/part_{finger:03d}{index:03d}",
        "translate": (offset + finger * 0.01, index * 0.001, 0.0),
        "orient": (1.0, 0.0, 0.0, 0.0),
        "type_name": "Xform",
        "applied_schemas": (),
    }
    part.update(overrides)
    return part


def _fake_passive_contract(*, offset_by_finger: dict[int, float] | None = None) -> dict:
    offsets = offset_by_finger or {}
    parts = [
        _fake_passive_part(finger, index, offset=offsets.get(finger, 0.0))
        for finger in range(1, 5)
        for index in range(22)
    ]
    return {
        "mode": "frame_plus_passive_linkage_no_shells",
        "visual_part_count": 88,
        "parts_per_finger": {1: 22, 2: 22, 3: 22, 4: 22},
        "excluded_shell_visual_count": 0,
        "added_rigid_body_count": 0,
        "added_collider_count": 0,
        "added_joint_count": 0,
        "parts": parts,
    }


def _open_measured() -> dict[str, float]:
    return {
        f"finger{finger}_motor{motor}": 0.05 if motor == 1 else 0.02
        for finger in range(1, 5)
        for motor in range(1, 3)
    }


def _fake_state(name: str, contract: dict, measured: dict[str, float] | None = None, **extra) -> dict:
    state = {
        "name": name,
        "passive_linkage_contract": contract,
        "measured": measured or _open_measured(),
    }
    state.update(extra)
    return state


def test_passive_linkage_visual_summary_rejects_wrong_part_count():
    contract = _fake_passive_contract()
    contract["parts"] = contract["parts"][:-1]

    with pytest.raises(RuntimeError, match="88"):
        validate_passive_linkage_visual_summary(contract)


def test_passive_linkage_visual_summary_rejects_any_shell_source():
    contract = _fake_passive_contract()
    contract["parts"][0]["source_prim"] = "mjcf_045_proximal_shell_1"

    with pytest.raises(RuntimeError, match="shell"):
        validate_passive_linkage_visual_summary(contract)


def test_passive_linkage_visual_summary_rejects_follower_physics_collision_or_joint_schema():
    for schema in ("PhysicsRigidBodyAPI", "PhysicsCollisionAPI", "PhysicsRevoluteJoint"):
        contract = _fake_passive_contract()
        contract["parts"][0]["applied_schemas"] = [schema]
        with pytest.raises(RuntimeError, match="physics|collision|joint"):
            validate_passive_linkage_visual_summary(contract)


def test_passive_linkage_motion_sequence_rejects_unchanged_finger_followers():
    states = [
        _fake_state("open", _fake_passive_contract()),
        _fake_state("half_close", _fake_passive_contract(offset_by_finger={1: 0.1, 2: 0.1, 4: 0.1})),
        _fake_state("close", _fake_passive_contract(offset_by_finger={1: 0.2, 2: 0.2, 4: 0.2})),
    ]

    with pytest.raises(RuntimeError, match="finger3"):
        validate_passive_linkage_motion_sequence(states)


def test_independent_finger_snapshots_require_only_target_finger_moves():
    open_state = _fake_state("open", _fake_passive_contract())
    finger1_measured = _open_measured()
    finger1_measured["finger1_motor1"] = 0.95
    finger1_measured["finger1_motor2"] = 1.10
    bad_state = _fake_state(
        "finger1_close",
        _fake_passive_contract(offset_by_finger={1: 0.2, 2: 0.2}),
        measured=finger1_measured,
        target_finger=1,
    )

    with pytest.raises(RuntimeError, match="finger2"):
        validate_independent_finger_linkage_sequence(open_state, [bad_state])


def test_static_snapshot_renderer_records_passive_linkage_visuals_before_capture():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    validate = renderer.index("_validate_passive_linkage_stage(")
    capture = renderer.index("_capture(")
    assert validate < capture
    assert 'report["passive_linkage_visuals"]' in renderer
    assert "validate_independent_finger_linkage_sequence" in renderer


def test_numeric_runner_exports_measured_independent_finger_snapshots():
    runner = (Path(__file__).parents[1] / "isaacsim_validation" / "run_validation.py").read_text()

    assert "independent_finger_states" in runner
    assert "target_finger" in runner
    assert "close_targets = grasp_to_urdf_targets(1.0)" in runner
    assert "open_targets = grasp_to_urdf_targets(0.0)" in runner
    measured = runner.index("measured = {joint: positions[dof_names.index(joint)] for joint in HAND_JOINTS}")
    snapshot = runner.index('run_dir / f"hand_finger{target_finger}_close_snapshot.usda"')
    author = runner.index("author_passive_linkage_snapshot(", snapshot)
    assert measured < snapshot < author


def test_zip_learning_visual_boundary_is_shell_free_and_source_closed_loop_informed():
    boundary = zip_learning_visual_boundary()

    assert "source closed-loop-informed" in boundary
    assert "measured Isaac joints" in boundary
    assert "outer shells disabled" in boundary
    assert "no closed-loop PhysX" in boundary


def test_passive_linkage_visual_summary_rejects_wrong_declared_parts_per_finger_contract():
    contract = _fake_passive_contract()
    contract["parts_per_finger"] = {1: 21, 2: 22, 3: 22, 4: 23}

    with pytest.raises(RuntimeError, match="22 parts per finger"):
        validate_passive_linkage_visual_summary(contract)


def test_non_zip_profiles_keep_generic_snapshot_rendering_without_passive_report_gate():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    profile_gate = renderer.index('if report.get("profile") == "zip_learning":')
    passive_report = renderer.index('report["passive_linkage_visuals"]')
    generic_capture = renderer.index("frame = _capture(")
    assert profile_gate < passive_report < generic_capture
    assert "else:\n            independent_snapshots = []" in renderer
    independent_report = renderer.index('report["independent_finger_visuals"]')
    assert renderer.rfind('if report.get("profile") == "zip_learning":', 0, independent_report) > profile_gate


def test_passive_linkage_stage_contract_rejects_transform_and_source_mismatch_but_accepts_negated_quat():
    contract = _fake_passive_contract()
    matching_stage = {
        "parts": [
            {
                **part,
                "orient": tuple(-value for value in part["orient"]),
                "metadata_source_index": part["source_index"],
                "metadata_source_prim": part["source_prim"],
                "metadata_reference_prim": part["reference_prim"],
            }
            for part in contract["parts"]
        ],
        "visual_part_count": 88,
        "parts_per_finger": {1: 22, 2: 22, 3: 22, 4: 22},
        "shell_visual_count": 0,
        "physics_schema_count": 0,
    }

    assert validate_passive_linkage_stage_contract(matching_stage, contract)["passed"] is True

    bad_source = {**matching_stage, "parts": [dict(part) for part in matching_stage["parts"]]}
    bad_source["parts"][0]["metadata_source_prim"] = "wrong_source"
    with pytest.raises(RuntimeError, match="source_prim"):
        validate_passive_linkage_stage_contract(bad_source, contract)

    bad_transform = {**matching_stage, "parts": [dict(part) for part in matching_stage["parts"]]}
    bad_transform["parts"][0]["translate"] = (99.0, 0.0, 0.0)
    with pytest.raises(RuntimeError, match="translate"):
        validate_passive_linkage_stage_contract(bad_transform, contract)


def test_usd_authoring_records_passive_linkage_contract_metadata_for_reopened_stage_validation():
    source = (Path(__file__).parents[1] / "isaacsim_validation" / "passive_linkage_usd.py").read_text()

    assert "passive_source_index" in source
    assert "passive_source_prim" in source
    assert "passive_reference_prim" in source


def test_independent_finger_runner_resets_to_open_before_each_target_close():
    runner = (Path(__file__).parents[1] / "isaacsim_validation" / "run_validation.py").read_text()

    loop = runner.index("for target_finger in range(1, 5):")
    reset_positions = runner.index("_set_positions(art, HAND_JOINTS, open_targets)", loop)
    reset_targets = runner.index("_command_targets(art, HAND_JOINTS, open_targets)", reset_positions)
    close_targets = runner.index("targets = dict(open_targets)", reset_targets)
    assert loop < reset_positions < reset_targets < close_targets


def test_runtime_wrapper_stops_before_renderer_when_numeric_report_is_not_passed():
    wrapper = (Path(__file__).parents[1] / "isaacsim_validation" / "run_isaacsim60_validation.sh").read_text()

    numeric_status = wrapper.index("numeric_status=${PIPESTATUS[0]}")
    numeric_report_check = wrapper.index("numeric_report_status=")
    render_launch = wrapper.index('docker run --name "$render_container_name"')
    assert numeric_status < numeric_report_check < render_launch
    assert "NUMERIC_PASS|PASS" in wrapper
    assert "Numeric Isaac validation report is not NUMERIC_PASS" in wrapper
    assert 'cp "$run_dir/numeric.log" "$run_dir/isaac.log"' in wrapper[numeric_report_check:render_launch]


def test_static_snapshot_renderer_uses_sdf_path_for_final_root_lookup():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    assert "from pxr import Sdf" in renderer
    assert 'last_stage.GetPrimAtPath(Sdf.Path(report["import"]["prim_path"]))' in renderer


def test_static_snapshot_renderer_retains_independent_stage_for_whole_robot_render():
    renderer = (Path(__file__).parents[1] / "isaacsim_validation" / "render_physics_snapshots.py").read_text()

    independent_loop = renderer.index("for snapshot in independent_snapshots:")
    open_success = renderer.index(
        'raise RuntimeError(f"Isaac could not open independent finger snapshot: {snapshot_path}")',
        independent_loop,
    )
    get_stage = renderer.index("stage = omni.usd.get_context().get_stage()", open_success)
    final_root_lookup = renderer.index(
        'root_prim = last_stage.GetPrimAtPath(Sdf.Path(report["import"]["prim_path"]))', get_stage
    )
    light_define = renderer.index("UsdLux.DomeLight.Define(stage, ", get_stage)
    last_stage_update = renderer.find("last_stage = stage", get_stage, light_define)

    assert last_stage_update != -1
    assert get_stage < last_stage_update < light_define < final_root_lookup
