import { describe, expect, it } from "vitest";
import {
  buildIsaacSessionPayload,
  captureImageUrl,
  clearCaptureUiState,
  countMeasuredPhysicalJoints,
  isSuperArmBackend,
  mergePhysicalJointPositions,
  runtimeSupportsCapture,
  runtimeSupportsContinuousVideo,
  superArmUrdfUrl,
} from "./superarmRuntime";

describe("SuperArm runtime helpers", () => {
  it("recognizes both LeLab simulation robot backends", () => {
    expect(isSuperArmBackend("superarm_mujoco")).toBe(true);
    expect(isSuperArmBackend("superarm_isaac")).toBe(true);
    expect(isSuperArmBackend("so101")).toBe(false);
  });

  it("keeps continuous video MuJoCo-only and capture Isaac-only", () => {
    expect(runtimeSupportsContinuousVideo("mujoco")).toBe(true);
    expect(runtimeSupportsContinuousVideo("isaac_sim")).toBe(false);
    expect(runtimeSupportsCapture("isaac_sim")).toBe(true);
    expect(runtimeSupportsCapture("mujoco")).toBe(false);
  });

  it("builds the managed Isaac session payload without unrelated runtime fields", () => {
    expect(
      buildIsaacSessionPayload({
        distributionZip: "/server/superarm.zip",
        expectedSha256: "a".repeat(64),
        bridgeMode: "managed",
        host: "127.0.0.1",
        port: 8765,
        externalRunDir: "",
      }),
    ).toEqual({
      runtime: "isaac_sim",
      isaac_distribution_zip: "/server/superarm.zip",
      isaac_expected_sha256: "a".repeat(64),
      isaac_bridge_mode: "managed",
      isaac_host: "127.0.0.1",
      isaac_port: 8765,
    });
  });

  it("cache-busts the server-authorized latest capture image", () => {
    expect(captureImageUrl("http://localhost:8000", 42)).toBe(
      "http://localhost:8000/api/superarm/capture/latest/image?v=42",
    );
  });

  it("keeps measured Isaac hand joints in the URDF showroom pose", () => {
    expect(
      mergePhysicalJointPositions(
        { joint_rev_1: 0.1, joint_rev_2: 0.2 },
        { joint_rev_1: { position: 0.3 } },
        { finger1_motor1: { position: 0.95 }, finger1_motor2: { position: 1.1 } },
      ),
    ).toEqual({
      joint_rev_1: 0.3,
      joint_rev_2: 0.2,
      finger1_motor1: 0.95,
      finger1_motor2: 1.1,
    });
  });

  it("reports only physical joints backed by measured Isaac telemetry", () => {
    expect(
      countMeasuredPhysicalJoints(
        {
          joint_rev_1: { position: 0.1 },
          joint_rev_2: { position: null },
          unrelated: { position: 0.2 },
        },
        {
          finger1_motor1: { position: 0.95 },
          finger1_motor2: {},
        },
      ),
    ).toBe(2);
  });

  it("uses URDF hand visuals only when the MuJoCo overlay is disabled", () => {
    expect(superArmUrdfUrl("http://localhost:8000", true)).toBe(
      "http://localhost:8000/api/superarm/urdf",
    );
    expect(superArmUrdfUrl("http://localhost:8000", false)).toBe(
      "http://localhost:8000/api/superarm/urdf?include_hand_visuals=true",
    );
  });

  it("clears capture metadata and invalidates the image URL at session boundaries", () => {
    expect(clearCaptureUiState({ capture: { bytes: 128 }, version: 4 })).toEqual({
      capture: null,
      version: 5,
    });
  });
});
