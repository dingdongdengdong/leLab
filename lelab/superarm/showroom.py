"""Browser-showroom alignment for the custom SuperArm and AmazingHand asset."""

from __future__ import annotations

import xml.etree.ElementTree as ET

ATTACHMENT_JOINT = "wrist_adapter_to_amazinghand"
ATTACHMENT_XYZ = "0 0 0.011753"


def align_amazinghand_attachment(root: ET.Element) -> bool:
    """Match the URDF hand mount to the attached transform used by MuJoCo."""
    for joint in root.findall(".//joint"):
        if joint.get("name") != ATTACHMENT_JOINT:
            continue
        origin = joint.find("origin")
        if origin is None:
            origin = ET.SubElement(joint, "origin")
        origin.set("xyz", ATTACHMENT_XYZ)
        return True
    return False
