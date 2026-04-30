import React, { useState } from "react";
import { Settings, X, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { RobotRecord } from "@/hooks/useRobots";

interface RobotTileProps {
  robot: RobotRecord;
  onConfigure: (name: string) => void;
  onTeleop: (robot: RobotRecord) => void;
  onRemoveFromSession: (name: string) => void;
  onDelete: (name: string) => void;
}

const RobotTile: React.FC<RobotTileProps> = ({
  robot,
  onConfigure,
  onTeleop,
  onRemoveFromSession,
  onDelete,
}) => {
  const [confirmDelete, setConfirmDelete] = useState(false);
  const status = robot.is_clean ? "Ready" : "Needs configuration";
  const teleopDisabled = !robot.is_clean;

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 flex flex-col gap-3 relative">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h4 className="font-semibold text-white truncate">{robot.name}</h4>
          <p
            className={`text-xs mt-0.5 ${
              robot.is_clean ? "text-green-400" : "text-amber-400"
            }`}
          >
            {status}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 text-gray-300 hover:text-white"
                onClick={() => onConfigure(robot.name)}
                aria-label="Configure"
              >
                <Settings className="w-4 h-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Configure (calibrate)</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 text-gray-400 hover:text-white"
                onClick={() => onRemoveFromSession(robot.name)}
                aria-label="Hide for this session"
              >
                <X className="w-4 h-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Hide for this session</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                size="icon"
                variant="ghost"
                className="h-7 w-7 text-red-400 hover:text-red-300 hover:bg-red-900/20"
                onClick={() => setConfirmDelete(true)}
                aria-label="Delete robot"
              >
                <Trash2 className="w-4 h-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent>Delete robot config</TooltipContent>
          </Tooltip>
        </div>
      </div>

      <Tooltip>
        <TooltipTrigger asChild>
          <div className="w-full">
            <Button
              onClick={() => onTeleop(robot)}
              disabled={teleopDisabled}
              className={`w-full ${
                teleopDisabled
                  ? "bg-red-500/30 hover:bg-red-500/30 text-red-200 cursor-not-allowed"
                  : "bg-yellow-500 hover:bg-yellow-600 text-white"
              }`}
            >
              Teleoperation
            </Button>
          </div>
        </TooltipTrigger>
        {teleopDisabled && (
          <TooltipContent>Configure the robot first.</TooltipContent>
        )}
      </Tooltip>

      <Dialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <DialogContent className="bg-gray-900 border-gray-800 text-white">
          <DialogHeader>
            <DialogTitle>Delete robot config?</DialogTitle>
            <DialogDescription className="text-gray-400">
              This deletes the robot config file from disk. Calibration files
              are not removed. This cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="flex gap-2 justify-end">
            <Button
              variant="outline"
              className="border-gray-600 text-gray-300"
              onClick={() => setConfirmDelete(false)}
            >
              Cancel
            </Button>
            <Button
              className="bg-red-500 hover:bg-red-600 text-white"
              onClick={async () => {
                setConfirmDelete(false);
                await onDelete(robot.name);
              }}
            >
              Delete
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default RobotTile;
