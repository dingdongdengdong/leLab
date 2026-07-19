export type FingerPair = [number, number];

export const clampSuperArm = (value: number, min: number, max: number) =>
  Math.max(min, Math.min(max, value));

export const formatSuperArmMetric = (value: unknown, digits = 2) =>
  typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "N/A";

export const keyboardFocusProtected = (tagName: string, isContentEditable = false) =>
  ["INPUT", "TEXTAREA", "SELECT"].includes(tagName.toUpperCase()) || isContentEditable;

export const baseSideToRaw = (base: number, side: number): FingerPair => [
  clampSuperArm(base + side, -40, 110),
  clampSuperArm(base - side, -40, 110),
];

export const reorderSequenceStep = <T,>(steps: T[], index: number, direction: -1 | 1): T[] => {
  const target = index + direction;
  if (index < 0 || index >= steps.length || target < 0 || target >= steps.length) return steps;
  const result = [...steps];
  [result[index], result[target]] = [result[target], result[index]];
  return result;
};
