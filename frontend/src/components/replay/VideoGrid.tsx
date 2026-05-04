import React, { useEffect, useRef } from "react";
import { VideoOff } from "lucide-react";
import { CameraItem } from "@/lib/replayApi";

interface Props {
  cameras: CameraItem[];
  registerRefs: (els: (HTMLVideoElement | null)[]) => void;
}

const VideoGrid: React.FC<Props> = ({ cameras, registerRefs }) => {
  const refs = useRef<(HTMLVideoElement | null)[]>([]);

  useEffect(() => {
    registerRefs(refs.current);
  }, [cameras, registerRefs]);

  if (cameras.length === 0) {
    return (
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        {[1, 2, 3, 4].map((i) => (
          <div key={i} className="aspect-video bg-gray-900 rounded-lg border border-gray-800 flex flex-col items-center justify-center p-2">
            <VideoOff className="h-8 w-8 text-gray-600 mb-2" />
            <span className="text-gray-500 text-xs">No video</span>
          </div>
        ))}
      </div>
    );
  }

  const cols = cameras.length === 1 ? "grid-cols-1" : cameras.length === 2 ? "grid-cols-2" : "grid-cols-2 md:grid-cols-4";

  return (
    <div className={`grid ${cols} gap-4`}>
      {cameras.map((cam, i) => (
        <div key={cam.key} className="aspect-video bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
          <video
            ref={(el) => { refs.current[i] = el; }}
            src={cam.url}
            preload="metadata"
            muted
            playsInline
            className="w-full h-full object-cover"
          />
          <div className="px-2 py-1 text-xs text-gray-400 bg-black/40 truncate">{cam.key}</div>
        </div>
      ))}
    </div>
  );
};

export default VideoGrid;
