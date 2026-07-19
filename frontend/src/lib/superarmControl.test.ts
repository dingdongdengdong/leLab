import { describe, expect, it } from "vitest";
import {
  baseSideToRaw,
  clampSuperArm,
  formatSuperArmMetric,
  keyboardFocusProtected,
  reorderSequenceStep,
} from "./superarmControl";

describe("SuperArm dashboard controls", () => {
  it("converts auto base/side into clamped two-servo values", () => {
    expect(baseSideToRaw(50, 10)).toEqual([60, 40]);
    expect(baseSideToRaw(110, 40)).toEqual([110, 70]);
    expect(clampSuperArm(-100, -40, 110)).toBe(-40);
  });

  it("protects form focus from global keyboard controls", () => {
    expect(keyboardFocusProtected("input")).toBe(true);
    expect(keyboardFocusProtected("textarea")).toBe(true);
    expect(keyboardFocusProtected("div", true)).toBe(true);
    expect(keyboardFocusProtected("body")).toBe(false);
  });

  it("never fabricates unavailable telemetry", () => {
    expect(formatSuperArmMetric(undefined)).toBe("N/A");
    expect(formatSuperArmMetric(null)).toBe("N/A");
    expect(formatSuperArmMetric(Number.NaN)).toBe("N/A");
    expect(formatSuperArmMetric(1.234)).toBe("1.23");
  });

  it("reorders sequence steps without mutating the input", () => {
    const steps = ["home", "close", "open"];
    expect(reorderSequenceStep(steps, 1, -1)).toEqual(["close", "home", "open"]);
    expect(steps).toEqual(["home", "close", "open"]);
    expect(reorderSequenceStep(steps, 0, -1)).toBe(steps);
  });
});
