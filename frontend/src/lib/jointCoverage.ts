export type JointCoverageState = "disconnected" | "awaiting" | "complete" | "mismatch";

interface JointCoverageInput {
  isConnected: boolean;
  hasReceivedJointData: boolean;
  physicalJointNames: string[];
  liveJointNames: string[];
}

interface JointCoverageStatus {
  liveCount: number;
  totalCount: number;
  state: JointCoverageState;
  showMismatch: boolean;
}

export const deriveJointCoverageStatus = ({
  isConnected,
  hasReceivedJointData,
  physicalJointNames,
  liveJointNames,
}: JointCoverageInput): JointCoverageStatus => {
  const liveNames = new Set(liveJointNames);
  const liveCount = isConnected && hasReceivedJointData
    ? physicalJointNames.filter((name) => liveNames.has(name)).length
    : 0;
  const totalCount = physicalJointNames.length;

  let state: JointCoverageState;
  if (!isConnected) state = "disconnected";
  else if (!hasReceivedJointData) state = "awaiting";
  else if (totalCount > 0 && liveCount !== totalCount) state = "mismatch";
  else state = "complete";

  return {
    liveCount,
    totalCount,
    state,
    showMismatch: state === "mismatch",
  };
};
