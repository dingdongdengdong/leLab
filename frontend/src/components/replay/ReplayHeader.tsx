import React from "react";
import { useNavigate } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import Logo from "@/components/Logo";
import { ReplayStatus } from "@/hooks/useReplayPlayback";

interface Props {
  status: ReplayStatus;
  repoId: string | null;
  episode: number | null;
}

const STATUS_DOT: Record<ReplayStatus, string> = {
  idle: "bg-slate-500",
  loading: "bg-blue-500 animate-pulse",
  playing: "bg-green-500",
  paused: "bg-amber-500",
  ended: "bg-slate-500",
  error: "bg-red-500",
};

const STATUS_LABEL: Record<ReplayStatus, string> = {
  idle: "Idle",
  loading: "Loading…",
  playing: "Playing",
  paused: "Paused",
  ended: "Ended",
  error: "Error",
};

const ReplayHeader: React.FC<Props> = ({ status, repoId, episode }) => {
  const navigate = useNavigate();
  const detail = status === "playing" || status === "paused"
    ? ` • ${repoId} ep ${episode}`
    : "";

  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-4 text-3xl">
        <Button variant="ghost" size="icon" onClick={() => navigate("/")} className="text-slate-400 hover:bg-slate-800 hover:text-white rounded-lg">
          <ArrowLeft className="w-5 h-5" />
        </Button>
        <Logo />
        <h1 className="font-bold text-white text-2xl">Replay Dataset</h1>
      </div>
      <div className="flex items-center gap-3">
        <div className={`w-3 h-3 rounded-full ${STATUS_DOT[status]}`}></div>
        <span className="font-semibold text-gray-400">
          {STATUS_LABEL[status]}{detail}
        </span>
      </div>
    </div>
  );
};

export default ReplayHeader;
