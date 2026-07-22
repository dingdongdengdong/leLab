from __future__ import annotations

import json
from pathlib import Path

from isaacsim_validation.zip_hand_binding import (
    VISUAL_PAYLOAD_FILES,
    static_visual_names,
    visual_reference_contract,
)


def _write_prepared_package(root: Path) -> Path:
    payloads = root / "usd" / "amazinghand_graspable" / "payloads"
    payloads.mkdir(parents=True)
    static_parts = "\n".join(
        f'def Xform "mjcf_{index:03d}_static_part_{index}" {{ }}' for index in range(26)
    )
    (payloads / "base.usda").write_text(f"#usda 1.0\n{static_parts}\n", encoding="utf-8")
    for name in set(VISUAL_PAYLOAD_FILES) - {"base.usda"}:
        (payloads / name).write_text("#usda 1.0\n", encoding="utf-8")
    (root / "prepared-manifest.json").write_text(
        json.dumps(
            {
                "source_zip": "/authoritative/amazinghand.zip",
                "source_zip_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_combined_binding_contract_uses_only_checked_zip_visual_payloads(tmp_path: Path):
    package = _write_prepared_package(tmp_path / "prepared")

    contract = visual_reference_contract(package)

    assert len(static_visual_names(package)) == 26
    assert contract["static_visual_part_count"] == 26
    assert contract["moving_visual_part_count"] == 16
    assert contract["finger_count"] == 4
    assert set(VISUAL_PAYLOAD_FILES) == {
        "base.usda",
        "robot.usda",
        "instances.usda",
        "geometries.usd",
    }


def test_distal_offsets_are_authored_in_a_referenced_visual_payload():
    source = (
        Path(__file__).parents[1] / "isaacsim_validation" / "zip_hand_binding.py"
    ).read_text(encoding="utf-8")

    assert 'target_payloads / "distal_visuals.usda"' in source
    assert '"./zip_hand_payloads/distal_visuals.usda"' in source
    assert "pose.AddTranslateOp" not in source
