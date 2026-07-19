export type SuperArmInputMode = "manual" | "so101";

export interface SuperArmLeaderFields {
  input_mode: SuperArmInputMode;
  leader_port: string;
  leader_config: string;
}

export function isSuperArmLeaderReady(
  mode: SuperArmInputMode,
  leaderPort: string,
  leaderConfig: string,
): boolean {
  return (
    mode === "manual" ||
    (leaderPort.trim().length > 0 && leaderConfig.trim().length > 0)
  );
}

export function buildSuperArmLeaderFields(
  mode: SuperArmInputMode,
  leaderPort: string,
  leaderConfig: string,
): SuperArmLeaderFields {
  if (mode === "manual") {
    return {
      input_mode: "manual",
      leader_port: "unused",
      leader_config: "manual",
    };
  }

  return {
    input_mode: "so101",
    leader_port: leaderPort.trim(),
    leader_config: leaderConfig.trim(),
  };
}
