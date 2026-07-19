import { describe, expect, it } from "vitest";
import {
  buildSuperArmLeaderFields,
  isSuperArmLeaderReady,
} from "./superarmRecording";

describe("SuperArm recording leader fields", () => {
  it("uses sentinel leader fields for manual web control", () => {
    expect(isSuperArmLeaderReady("manual", "", "")).toBe(true);
    expect(buildSuperArmLeaderFields("manual", "ignored", "ignored")).toEqual({
      input_mode: "manual",
      leader_port: "unused",
      leader_config: "manual",
    });
  });

  it("requires and preserves the SO101 serial and calibration arguments", () => {
    expect(isSuperArmLeaderReady("so101", "", "so101_leader")).toBe(false);
    expect(isSuperArmLeaderReady("so101", "/dev/ttyACM0", "")).toBe(false);
    expect(
      isSuperArmLeaderReady("so101", "/dev/ttyACM0", "so101_leader"),
    ).toBe(true);
    expect(
      buildSuperArmLeaderFields(
        "so101",
        " /dev/ttyACM0 ",
        " so101_leader ",
      ),
    ).toEqual({
      input_mode: "so101",
      leader_port: "/dev/ttyACM0",
      leader_config: "so101_leader",
    });
  });
});
