from __future__ import annotations

import importlib
import os
import tempfile
import unittest
from pathlib import Path


HAND_JOINTS = [
    "finger1_motor1",
    "finger1_motor2",
    "finger2_motor1",
    "finger2_motor2",
    "finger3_motor1",
    "finger3_motor2",
    "finger4_motor1",
    "finger4_motor2",
]


class SuperArmAmazingHandManualConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.superarm_ws = self.root / "superarm_ws"
        self.lerobot_dir = self.superarm_ws / "isaacsim_test" / "lerobot"
        self.lerobot_dir.mkdir(parents=True)
        (self.lerobot_dir / "source_arm_isaacsim_arm_only.yaml").write_text(
            "_type: isaacsim_rpo_arm\njoint_names: [joint_rev_1, joint_rev_2, joint_rev_3, joint_rev_4, joint_rev_5]\n",
            encoding="utf-8",
        )
        (self.lerobot_dir / "amazinghand_isaacsim_hand_only.yaml").write_text(
            "\n".join(
                [
                    "_type: isaacsim_rpo_arm",
                    "joint_names:",
                    *(f"  - {name}" for name in HAND_JOINTS),
                    "joint_state_topic: /hand/joint_states",
                    "joint_command_topic: /hand/joint_commands",
                    "phone_command_topic: /hand/leader_joint_commands",
                    "screenshot_debug_topic: /hand/screenshot_debug",
                    "allow_custom_joint_names: true",
                    "manual_leader:",
                    "  kind: amazinghand",
                    "  slider_min: 0.0",
                    "  slider_max: 1.2",
                    "  slider_step: 0.01",
                ]
            ),
            encoding="utf-8",
        )
        self.old_superarm_ws = os.environ.get("SUPERARM_WS_PATH")
        os.environ["SUPERARM_WS_PATH"] = str(self.superarm_ws)

        from lelab.utils import config as cfg

        self.cfg = importlib.reload(cfg)
        self.cfg.ROBOTS_PATH = str(self.root / "robots")

    def tearDown(self) -> None:
        if self.old_superarm_ws is None:
            os.environ.pop("SUPERARM_WS_PATH", None)
        else:
            os.environ["SUPERARM_WS_PATH"] = self.old_superarm_ws

    def test_list_robot_records_includes_builtin_superarm_amazinghand(self) -> None:
        records = self.cfg.list_robot_records()
        hand = next((record for record in records if record["name"] == "SuperArm AmazingHand"), None)

        self.assertIsNotNone(hand)
        assert hand is not None
        self.assertEqual(hand["robot_backend"], "isaacsim_rpo_arm")
        self.assertEqual(hand["leader_port"], "unused")
        self.assertEqual(hand["follower_port"], "unused")
        self.assertTrue(hand["isaacsim_config"].endswith("amazinghand_isaacsim_hand_only.yaml"))
        self.assertEqual(hand["follower_config"], hand["isaacsim_config"])
        self.assertTrue(self.cfg.is_robot_record_clean(hand))

    def test_amazinghand_manual_leader_config_uses_safe_finger_sliders_and_presets(self) -> None:
        from lelab.manual_leader import build_manual_leader_config

        hand = self.cfg.get_robot_record("SuperArm AmazingHand")
        self.assertIsNotNone(hand)
        assert hand is not None
        body = build_manual_leader_config(hand)

        self.assertEqual(body["robot_name"], "SuperArm AmazingHand")
        self.assertEqual(body["joint_names"], HAND_JOINTS)
        self.assertEqual(body["start_request"]["isaacsim_config"], hand["isaacsim_config"])
        self.assertEqual(body["start_request"]["follower_config"], hand["isaacsim_config"])
        self.assertEqual(body["start_request"]["robot_backend"], "isaacsim_rpo_arm")
        self.assertTrue(all(slider["min"] == 0.0 for slider in body["sliders"]))
        self.assertTrue(all(slider["max"] == 1.2 for slider in body["sliders"]))
        self.assertEqual(body["presets"][0], {"name": "Open hand", "action": [0.05, 0.02] * 4})
        self.assertEqual(body["presets"][-1], {"name": "Close hand", "action": [0.95, 1.1] * 4})


if __name__ == "__main__":
    unittest.main()
