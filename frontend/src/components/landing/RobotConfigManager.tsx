import React from "react";
import { useNavigate } from "react-router-dom";
import { useApi } from "@/contexts/ApiContext";
import { useToast } from "@/hooks/use-toast";
import { RobotRecord } from "@/hooks/useRobots";
import { Button } from "@/components/ui/button";
import RobotTile from "./RobotTile";

interface RobotConfigManagerProps {
  selectedName: string | null;
  selectedRecord: RobotRecord | null;
  availableNames: string[];
  isLoading: boolean;
  selectRobot: (name: string) => void;
  createRobot: (name: string) => Promise<boolean>;
  deleteRobot: (name: string) => Promise<boolean>;
}

const RobotConfigManager: React.FC<RobotConfigManagerProps> = ({
  selectedName,
  selectedRecord,
  availableNames,
  isLoading,
  selectRobot,
  createRobot,
  deleteRobot,
}) => {
  const navigate = useNavigate();
  const { baseUrl, fetchWithHeaders } = useApi();
  const { toast } = useToast();

  const handleConfigure = (name: string) => {
    navigate("/calibration", { state: { robot_name: name } });
  };

  const handleManualLeader = (robot: RobotRecord) => {
    navigate(`/manual-leader?robot=${encodeURIComponent(robot.name)}`, {
      state: { robot_name: robot.name },
    });
  };

  const handleTeleop = async (robot: RobotRecord) => {
    try {
      const res = await fetchWithHeaders(`${baseUrl}/move-arm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          leader_port: robot.leader_port,
          follower_port: robot.follower_port,
          leader_config: robot.leader_config,
          follower_config: robot.follower_config,
          robot_backend: robot.robot_backend || "so101",
          superarm_config: robot.superarm_config || undefined,
          superarm_asset_root: robot.superarm_asset_root || undefined,
          mujoco_model_path: robot.mujoco_model_path || undefined,
        }),
      });
      const data = await res.json();
      // The backend returns HTTP 200 with `{ success: false }` for logical
      // failures (arm not connected, already active), so gate on `data.success`
      // — not just `res.ok` — or we'd navigate to an empty teleop screen.
      if (res.ok && data.success) {
        toast({
          title: "Teleoperation Started",
          description: data.message || `Started teleoperation for ${robot.name}.`,
        });
        navigate(`/teleoperation?robot=${encodeURIComponent(robot.name)}`, {
          state: {
            robot_name: robot.name,
            showroom_urdf: Boolean(robot.urdf_path),
            physical_joint_names: robot.physical_joint_names || [],
          },
        });
      } else {
        toast({
          title: "Error Starting Teleoperation",
          description: data.message || "Failed to start.",
          variant: "destructive",
        });
      }
    } catch (e) {
      toast({
        title: "Connection Error",
        description: "Could not connect to the backend server.",
        variant: "destructive",
      });
    }
  };

  return (
    <div className="grid gap-3">
      <div className="rounded-lg border border-cyan-700/60 bg-gradient-to-r from-slate-900 to-cyan-950 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-400">MuJoCo diagnostic</p>
            <h3 className="text-lg font-semibold text-white">Secondary physics dashboard</h3>
            <p className="text-sm text-slate-300">Inspect the MuJoCo joint contract separately. Use the normal robot selector above for LeRobot, recording, and the URDF showroom.</p>
          </div>
          <Button
            onClick={() => navigate("/superarm")}
            className="bg-cyan-500 text-slate-950 hover:bg-cyan-400"
          >
            Open diagnostic
          </Button>
          <Button
            variant="outline"
            onClick={() => navigate("/hardware-setup")}
            className="border-cyan-700 text-cyan-200 hover:bg-cyan-950"
          >
            Hardware setup
          </Button>
        </div>
      </div>
      <RobotTile
      robot={selectedRecord}
      selectedName={selectedName}
      availableNames={availableNames}
      isLoading={isLoading}
      onSelect={selectRobot}
      onCreateNew={createRobot}
      onConfigure={handleConfigure}
      onTeleop={handleTeleop}
      onManualLeader={handleManualLeader}
      onDelete={deleteRobot}
      />
    </div>
  );
};

export default RobotConfigManager;
