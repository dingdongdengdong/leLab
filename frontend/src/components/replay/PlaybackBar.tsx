import React from "react";
import { Pause, Play, Square } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Slider } from "@/components/ui/slider";
import { cn } from "@/lib/utils";

interface Props {
  paused: boolean;
  frame: number;
  totalFrames: number;
  fps: number;
  speed: number;
  disabled: boolean;
  onPlay: () => void;
  onPause: () => void;
  onStop: () => void;
  onSeek: (frame: number) => void;
  onSpeedChange: (speed: number) => void;
}

const SPEEDS = [1, 2, 4, 10];

const formatTime = (seconds: number): string => {
  if (!Number.isFinite(seconds)) return "—";
  const s = Math.max(0, Math.floor(seconds));
  if (s < 3600) return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  const h = Math.floor(s / 3600);
  return `${String(h).padStart(2, "0")}:${String(Math.floor((s % 3600) / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
};

const PlaybackBar: React.FC<Props> = ({
  paused, frame, totalFrames, fps, speed, disabled,
  onPlay, onPause, onStop, onSeek, onSpeedChange,
}) => {
  const current = fps ? frame / fps : 0;
  const total = fps ? totalFrames / fps : 0;

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 flex flex-col gap-3">
      <div className="flex items-center gap-3">
        {paused ? (
          <Button size="icon" onClick={onPlay} disabled={disabled}><Play className="h-4 w-4" /></Button>
        ) : (
          <Button size="icon" onClick={onPause} disabled={disabled}><Pause className="h-4 w-4" /></Button>
        )}
        <Button size="icon" variant="outline" onClick={onStop} disabled={disabled}>
          <Square className="h-4 w-4" />
        </Button>
        <div className="flex-1">
          <Slider
            min={0}
            max={Math.max(0, totalFrames - 1)}
            step={1}
            value={[frame]}
            disabled={disabled || totalFrames === 0}
            onValueChange={(v) => onSeek(v[0])}
          />
        </div>
        <div className="font-mono text-xs text-gray-300 w-32 text-right">
          {formatTime(current)} / {formatTime(total)}
        </div>
      </div>
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400">Speed</span>
        {SPEEDS.map((s) => (
          <button
            key={s}
            onClick={() => onSpeedChange(s)}
            disabled={disabled}
            className={cn(
              "px-2 py-1 rounded text-xs",
              speed === s ? "bg-purple-500/30 text-purple-200" : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            )}
          >
            {s}×
          </button>
        ))}
        <span className="ml-auto font-mono text-xs text-gray-500">
          Frame {frame} / {Math.max(totalFrames - 1, 0)}
        </span>
      </div>
    </div>
  );
};

export default PlaybackBar;
