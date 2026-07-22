export type SuperArmRuntime = "mujoco" | "hybrid_serial" | "isaac_sim";
export type SuperArmBackend = "superarm_mujoco" | "superarm_isaac";
export type IsaacBridgeMode = "managed" | "external";

export interface IsaacSessionSettings {
  distributionZip: string;
  expectedSha256?: string;
  bridgeMode: IsaacBridgeMode;
  host: string;
  port: number;
  externalRunDir?: string;
}

export interface CaptureUiState<T> {
  capture: T | null;
  version: number;
}

export const SUPERARM_BACKENDS = [
  "superarm_mujoco",
  "superarm_isaac",
] as const;

export const isSuperArmBackend = (
  value?: string | null,
): value is SuperArmBackend =>
  value === "superarm_mujoco" || value === "superarm_isaac";

export const runtimeSupportsContinuousVideo = (
  runtime: SuperArmRuntime,
): boolean => runtime === "mujoco" || runtime === "hybrid_serial";

export const runtimeSupportsCapture = (runtime: SuperArmRuntime): boolean =>
  runtime === "isaac_sim";

export const buildIsaacSessionPayload = (settings: IsaacSessionSettings) => ({
  runtime: "isaac_sim" as const,
  isaac_distribution_zip: settings.distributionZip.trim(),
  ...(settings.expectedSha256?.trim()
    ? { isaac_expected_sha256: settings.expectedSha256.trim() }
    : {}),
  isaac_bridge_mode: settings.bridgeMode,
  isaac_host: settings.host.trim(),
  isaac_port: settings.port,
  ...(settings.externalRunDir?.trim()
    ? { isaac_external_run_dir: settings.externalRunDir.trim() }
    : {}),
});

export const captureImageUrl = (baseUrl: string, version: number): string =>
  `${baseUrl}/api/superarm/capture/latest/image?v=${version}`;

export const clearCaptureUiState = <T>(
  state: CaptureUiState<T>,
): CaptureUiState<T> => ({ capture: null, version: state.version + 1 });

export const superArmUrdfUrl = (
  baseUrl: string,
  enableMjcfVisuals: boolean,
): string =>
  `${baseUrl}/api/superarm/urdf${enableMjcfVisuals ? "" : "?include_hand_visuals=true"}`;

type JointMetric = { position?: number | null };

const PHYSICAL_JOINTS = new Set([
  ...Array.from({ length: 5 }, (_, index) => `joint_rev_${index + 1}`),
  ...Array.from({ length: 4 }, (_, finger) =>
    Array.from({ length: 2 }, (_, motor) => `finger${finger + 1}_motor${motor + 1}`),
  ).flat(),
]);

export const mergePhysicalJointPositions = (
  fallbackArm: Record<string, number>,
  measuredArm?: Record<string, JointMetric>,
  measuredHand?: Record<string, JointMetric>,
): Record<string, number> => {
  const result = { ...fallbackArm };
  for (const [name, metric] of Object.entries({ ...measuredArm, ...measuredHand })) {
    if (typeof metric.position === "number") result[name] = metric.position;
  }
  return result;
};

export const countMeasuredPhysicalJoints = (
  measuredArm?: Record<string, JointMetric>,
  measuredHand?: Record<string, JointMetric>,
): number =>
  Object.entries({ ...measuredArm, ...measuredHand }).filter(
    ([name, metric]) => PHYSICAL_JOINTS.has(name) && typeof metric.position === "number",
  ).length;
