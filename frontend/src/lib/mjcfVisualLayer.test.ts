import { describe, expect, it, vi } from "vitest";
import { Group, Object3D, Quaternion } from "three";

import {
  filterScalarJoints,
  findUrdfAttachment,
  loadUniqueMeshes,
  normalizeVisualPose,
  VisualPoseBuffer,
  wxyzToThreeQuaternion,
} from "@/lib/mjcfVisualLayer";

const pose = (timestamp: number, x: number) => normalizeVisualPose({
  timestamp,
  bodies: {
    horn: {
      position_m: [x, 0, 0],
      quaternion_wxyz: [1, 0, 0, 0],
    },
  },
})!;

describe("exact MJCF visual layer", () => {
  it("converts MuJoCo wxyz quaternion order to Three.js xyzw", () => {
    const result = wxyzToThreeQuaternion([0.5, 0.1, 0.2, 0.3]);
    expect(result).toEqual(new Quaternion(0.1, 0.2, 0.3, 0.5));
  });

  it("finds the wrist attachment through URDF robot links", () => {
    const wrist = new Group();
    const robot = new Group() as Group & { links: Record<string, Object3D> };
    robot.links = { r_wrist_interface: wrist };
    expect(findUrdfAttachment({ robot } as never, "r_wrist_interface")).toBe(wrist);
  });

  it("loads each unique mesh URL only once", async () => {
    const loader = vi.fn(async () => new Group());
    const result = await loadUniqueMeshes(
      ["finger.stl", "horn.stl", "finger.stl", "horn.stl"],
      loader,
    );
    expect(loader).toHaveBeenCalledTimes(2);
    expect(result.size).toBe(2);
  });

  it("retains the last valid pose when an invalid frame arrives", () => {
    const buffer = new VisualPoseBuffer();
    const valid = pose(1, 0.25);
    buffer.push(valid, 100);
    const invalid = normalizeVisualPose({
      timestamp: 2,
      bodies: { horn: { position_m: [NaN, 0, 0], quaternion_wxyz: [0, 0, 0, 0] } },
    });
    expect(invalid).toBeNull();
    expect(buffer.sample(200)).toBe(valid);
  });

  it("interpolates between frames and never extrapolates", () => {
    const buffer = new VisualPoseBuffer();
    buffer.push(pose(1, 0), 100);
    buffer.push(pose(1.05, 1), 150);
    expect(buffer.sample(150, 25)?.bodies.horn.position_m[0]).toBeCloseTo(0.5);
    expect(buffer.sample(1_000, 25)?.bodies.horn.position_m[0]).toBe(1);
  });

  it("keeps 13-joint coverage data while filtering hand scalar animation", () => {
    const joints = {
      joint_rev_1: 0.1,
      joint_rev_2: 0.2,
      joint_rev_3: 0.3,
      joint_rev_4: 0.4,
      joint_rev_5: 0.5,
      finger1_motor1: 0.6,
      finger1_motor2: 0.7,
      finger2_motor1: 0.8,
      finger2_motor2: 0.9,
      finger3_motor1: 1.0,
      finger3_motor2: 1.1,
      finger4_motor1: 1.2,
      finger4_motor2: 1.3,
    };
    const filtered = filterScalarJoints(joints, true);
    expect(Object.keys(joints)).toHaveLength(13);
    expect(Object.keys(filtered)).toEqual([
      "joint_rev_1",
      "joint_rev_2",
      "joint_rev_3",
      "joint_rev_4",
      "joint_rev_5",
    ]);
    expect(filterScalarJoints(joints, false)).toBe(joints);
  });
});
