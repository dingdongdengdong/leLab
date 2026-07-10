# SuperArm Isaac Sim Control from LeLab

This LeLab branch adds a custom Isaac Sim follower backend for the local SuperArm/source-arm workspace. It lets you control the simulated arm from the normal LeLab web UI, either with full teleoperation plumbing or with a browser slider page when no physical SO101 leader arm is connected.

## What is supported

- Robot entry in LeLab: **SuperArm Source Arm**
- Backend: `isaacsim_rpo_arm`
- Isaac/LeRobot config:
  `/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml`
- Controlled arm joints:
  - `joint_rev_1`
  - `joint_rev_2`
  - `joint_rev_3`
  - `joint_rev_4`
  - `joint_rev_5`
- ROS 2 topics:
  - `/follower/joint_states` publishes the simulated follower state.
  - `/follower/joint_commands` receives follower commands.
  - `/leader/joint_commands` remains available for LeRobot-style leader commands.

The source asset currently used here is arm-only. Hand assets exist separately in the SuperArm workspace and are not part of this 5-DOF manual leader flow.

## 1. Start the LeLab server

From this LeLab checkout:

```bash
cd /workspaces/superarm_ws/worktrees/leLab
source /opt/ros/humble/setup.bash
export SUPERARM_WS_PATH=/workspaces/superarm_ws
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTDDS_BUILTIN_TRANSPORTS=UDPv4
export PYTHONPATH=/workspaces/superarm_ws/worktrees/leLab:/workspaces/superarm_ws/isaacsim_test/lerobot:${PYTHONPATH:-}
python3 -m uvicorn lelab.server:app --host 0.0.0.0 --port 8000
```

Open:

```text
http://127.0.0.1:8000/
```

If you are using a remote browser, port-forward port `8000`.

## 2. Start the Isaac Sim follower bridge

LeLab can only connect after Isaac Sim is publishing `/follower/joint_states`. Start the source-arm scene from the SuperArm workspace:

```bash
cd /workspaces/superarm_ws
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=42
export ROS_DISTRO=humble
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTDDS_BUILTIN_TRANSPORTS=UDPv4
export PYTHONUNBUFFERED=1
export HEADLESS=1
export SCREENSHOT_AFTER_COMMAND=0
export SCREENSHOT_ON_STARTUP=0
export EXIT_AFTER_SCREENSHOT=0
export SIMREADY_USD_PATH=/does/not/exist.usd
export RPO_ARM_URDF_PATH=/workspaces/superarm_ws/isaacsim_test/outputs/robot_arm_hand_from_zip_local_drive/robot_arm_hand_sanitized.urdf
export JOINT_NAMES=joint_rev_1,joint_rev_2,joint_rev_3,joint_rev_4,joint_rev_5
export NUM_JOINTS=5
export LD_LIBRARY_PATH=/workspace/isaacsim/exts/isaacsim.ros2.bridge/humble/lib:${LD_LIBRARY_PATH:-}
export PYTHONPATH=/workspace/isaacsim/exts/isaacsim.ros2.bridge/humble/rclpy:${PYTHONPATH:-}
/workspace/isaacsim/python.sh /workspaces/superarm_ws/isaacsim_test/isaacsim/setup_rpo_arm_scene.py
```

Wait for this line:

```text
[setup_rpo_arm_scene] Simulation running. Ctrl+C to stop.
```

## 3. Verify ROS bridge health

In another shell:

```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=42
export RMW_IMPLEMENTATION=rmw_fastrtps_cpp
export FASTDDS_BUILTIN_TRANSPORTS=UDPv4
ros2 topic list
ros2 topic echo /follower/joint_states --once
ros2 topic hz /follower/joint_states
```

Expected topics include:

```text
/follower/joint_commands
/follower/joint_states
```

If `/follower/joint_states` is missing, the LeLab **Connect Isaac Follower** button will fail with a message such as “no joint states received.”

## 4. Control without a physical leader arm

Use the Manual Web Leader page:

```text
http://127.0.0.1:8000/manual-leader?robot=SuperArm%20Source%20Arm
```

Or from the landing page:

1. Select **SuperArm Source Arm**.
2. Click **Manual Web Leader**.
3. Click **Connect Isaac Follower**.
4. Move the sliders.
5. Click **Send Slider Action**.

The page sends the same backend calls the app exposes:

- `POST /move-arm` starts the `isaacsim_rpo_arm` follower backend.
- `POST /send-joint-action` sends the 5 slider values.
- `POST /stop-teleoperation` closes the follower session.

Leaving the page also sends a best-effort stop request so the follower session does not block the next connect.

## 5. Capture arm movement screenshots

For visual debugging, start Isaac Sim with command screenshots enabled:

```bash
export SCREENSHOT_AFTER_COMMAND=1
export SCREENSHOT_EACH_COMMAND=1
export EXIT_AFTER_SCREENSHOT=0
export SCREENSHOT_SEQUENCE_DIR=/workspaces/superarm_ws/isaacsim_test/artifacts/manual_web_leader_live/screenshots
export COMMAND_EVIDENCE_DIR=/workspaces/superarm_ws/isaacsim_test/artifacts/manual_web_leader_live/command_evidence
```

Then start the same Isaac scene command from section 2. Each `/send-joint-action` command saves another image:

```text
$SCREENSHOT_SEQUENCE_DIR/command_001.png
$SCREENSHOT_SEQUENCE_DIR/command_002.png
...
```

It also writes command evidence JSON with the applied joint values and articulation readback.

Example evidence from a 5-minute run:

```text
/workspaces/superarm_ws/isaacsim_test/artifacts/manual_web_leader_20260707T074836Z
```

That run sent 21 commands and saved command screenshots under its `screenshots/` directory.

## Troubleshooting

### Connect Isaac Follower fails: no joint states received

Cause: LeLab is running, but Isaac Sim is not publishing `/follower/joint_states`.

Check:

```bash
source /opt/ros/humble/setup.bash
export ROS_DOMAIN_ID=42
ros2 topic echo /follower/joint_states --once
```

Fix: start or restart the Isaac Sim follower bridge from section 2 and wait for `Simulation running`.

### Connect fails because teleoperation is already active

A previous LeLab session is still open. Stop it:

```bash
curl -X POST http://127.0.0.1:8000/stop-teleoperation
```

Then reconnect from the Manual Web Leader page.

### Sliders send but the arm does not visibly move

Check command evidence from the Isaac Sim scene:

```bash
ls -l /workspaces/superarm_ws/isaacsim_test/artifacts/*/command_evidence
```

If evidence files contain the new joint command but the screenshot looks unchanged, inspect the URDF/articulation joint binding and drive setup. A screenshot is visual evidence only; it does not prove physics, colliders, or joint-drive correctness.

### Wrong robot appears in LeLab

The built-in record is named **SuperArm Source Arm**. Its config comes from:

```text
/workspaces/superarm_ws/isaacsim_test/lerobot/source_arm_isaacsim_arm_only.yaml
```

Make sure `SUPERARM_WS_PATH=/workspaces/superarm_ws` is set before starting LeLab.
