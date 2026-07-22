from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from isaacsim_validation.visuals import (
    crop_hand_closeup,
    image_has_detail,
    validate_direct_grasp_frames,
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
    renderer = (
        Path(__file__).parents[1]
        / "isaacsim_validation"
        / "render_physics_snapshots.py"
    ).read_text()

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
    wrapper = (
        Path(__file__).parents[1] / "isaacsim_validation" / "run_isaacsim60_validation.sh"
    ).read_text()

    assert 'report.get("status") != "PASS"' in wrapper
    assert "Isaac validation report is not PASS" in wrapper
    assert "render_physics_snapshots.py" in wrapper
    assert 'numeric_container_name="${container_prefix}-numeric"' in wrapper
    assert 'render_container_name="${container_prefix}-render"' in wrapper


def test_numeric_runner_exports_each_measured_hand_state_before_visual_render():
    runner = (Path(__file__).parents[1] / "isaacsim_validation" / "run_validation.py").read_text()

    readback = runner.index("positions = _flat(art.get_dof_positions())")
    snapshot = runner.index('stage.Export(str(snapshot))')
    awaiting_render = runner.index('"awaiting_static_visual_render"')
    assert readback < snapshot < awaiting_render


def test_numeric_runner_restores_pristine_package_after_runtime_snapshots():
    runner = (Path(__file__).parents[1] / "isaacsim_validation" / "run_validation.py").read_text()

    pristine = runner.index("pristine_package = usd_path.read_bytes()")
    world = runner.index("world = World(")
    snapshot = runner.index('stage.Export(str(snapshot))')
    restore = runner.index("usd_path.write_bytes(pristine_package)")
    assert pristine < world < snapshot < restore
    assert '"restored_pristine_root_layer_after_runtime"' in runner
    assert '"physics_snapshots_only"' in runner


def test_static_snapshot_renderer_uses_one_fixed_camera_for_all_hand_states():
    renderer = (
        Path(__file__).parents[1]
        / "isaacsim_validation"
        / "render_physics_snapshots.py"
    ).read_text()

    assert "fixed_hand_pose = None" in renderer
    assert "fixed_pose=fixed_hand_pose" in renderer
    assert '"static_replicator_from_physics_snapshot"' in renderer


def test_static_snapshot_renderer_enables_capture_extension_before_rendering():
    renderer = (
        Path(__file__).parents[1]
        / "isaacsim_validation"
        / "render_physics_snapshots.py"
    ).read_text()

    extension = 'enable_extension("omni.kit.renderer.capture")'
    assert extension in renderer
    assert renderer.index(extension) < renderer.index("def _capture(")


def test_static_snapshot_renderer_uses_close_range_camera_clipping():
    renderer = (
        Path(__file__).parents[1]
        / "isaacsim_validation"
        / "render_physics_snapshots.py"
    ).read_text()

    assert "clipping_range=(0.001, 100.0)" in renderer
    assert "rep.orchestrator.step(rt_subframes=8)" in renderer
