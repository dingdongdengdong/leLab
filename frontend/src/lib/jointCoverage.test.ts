import { describe, expect, it } from "vitest";

import { deriveJointCoverageStatus } from "./jointCoverage";

const PHYSICAL_JOINTS = [
  "joint_rev_1",
  "joint_rev_2",
  "joint_rev_3",
  "joint_rev_4",
  "joint_rev_5",
  "finger1_motor1",
  "finger1_motor2",
  "finger2_motor1",
  "finger2_motor2",
  "finger3_motor1",
  "finger3_motor2",
  "finger4_motor1",
  "finger4_motor2",
];

describe("deriveJointCoverageStatus", () => {
  it("waits for the first runtime sample instead of claiming a mismatch", () => {
    expect(
      deriveJointCoverageStatus({
        isConnected: true,
        hasReceivedJointData: false,
        physicalJointNames: PHYSICAL_JOINTS,
        liveJointNames: [],
      }),
    ).toEqual({
      liveCount: 0,
      totalCount: 13,
      state: "awaiting",
      showMismatch: false,
    });
  });

  it("ignores stale names until the reconnected socket receives a fresh sample", () => {
    expect(
      deriveJointCoverageStatus({
        isConnected: true,
        hasReceivedJointData: false,
        physicalJointNames: PHYSICAL_JOINTS,
        liveJointNames: PHYSICAL_JOINTS,
      }),
    ).toEqual({
      liveCount: 0,
      totalCount: 13,
      state: "awaiting",
      showMismatch: false,
    });
  });

  it("reports a mismatch only after a partial runtime sample arrives", () => {
    expect(
      deriveJointCoverageStatus({
        isConnected: true,
        hasReceivedJointData: true,
        physicalJointNames: PHYSICAL_JOINTS,
        liveJointNames: PHYSICAL_JOINTS.slice(0, 12),
      }),
    ).toMatchObject({
      liveCount: 12,
      totalCount: 13,
      state: "mismatch",
      showMismatch: true,
    });
  });
});
