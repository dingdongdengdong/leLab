/*
 * Adapted from NVIDIA's create-ov-web-rtc-app local sample (MIT).
 * The only connection change is using the current LeLab host instead of
 * 127.0.0.1 so a remote browser connects to the Isaac host, not itself.
 */
import { useEffect, useMemo, useState } from "react";
import {
  AppStreamer,
  DirectConfig,
  eAction,
  eStatus,
  LogLevel,
  StreamEvent,
  StreamProps,
  StreamType,
} from "@nvidia/omniverse-webrtc-streaming-library";

import {
  ISAAC_WEBRTC_MEDIA_PORT,
  ISAAC_WEBRTC_SIGNAL_PORT,
  resolveIsaacWebRtcHost,
} from "@/lib/isaacWebRtc";

type StreamState = "connecting" | "connected" | "failed";

const IsaacWebRtcViewport = () => {
  const host = useMemo(() => resolveIsaacWebRtcHost(window.location), []);
  const [streamState, setStreamState] = useState<StreamState>("connecting");
  const [errorMessage, setErrorMessage] = useState("Failed to connect to Isaac Sim WebRTC");

  useEffect(() => {
    let disposed = false;
    const streamConfig: DirectConfig = {
      videoElementId: "isaac-remote-video",
      audioElementId: "isaac-remote-audio",
      signalingServer: host,
      signalingPort: ISAAC_WEBRTC_SIGNAL_PORT,
      mediaServer: host,
      mediaPort: ISAAC_WEBRTC_MEDIA_PORT,
      width: 1280,
      height: 720,
      fps: 30,
      nativeTouchEvents: true,
      onStart: (message: StreamEvent) => {
        if (disposed || message.action !== eAction.start) return;
        if (message.status === eStatus.success) {
          setStreamState("connected");
          return;
        }
        if (message.status === eStatus.error) {
          setErrorMessage(String(message.info || "Unknown WebRTC error"));
          setStreamState("failed");
        }
      },
      onStop: () => {
        if (!disposed) setStreamState("failed");
      },
    };
    const streamProps: StreamProps = {
      streamSource: StreamType.DIRECT,
      logLevel: LogLevel.INFO,
      streamConfig,
    };

    AppStreamer.connect(streamProps).catch((error: unknown) => {
      if (disposed) return;
      setErrorMessage(error instanceof Error ? error.message : String(error));
      setStreamState("failed");
    });

    return () => {
      disposed = true;
      AppStreamer.terminate();
    };
  }, [host]);

  return (
    <div className="relative aspect-video min-h-[360px] overflow-hidden bg-black">
      <video
        id="isaac-remote-video"
        className={`h-full w-full object-contain ${streamState === "connected" ? "block" : "invisible"}`}
        tabIndex={0}
        playsInline
        muted
        autoPlay
      />
      <audio id="isaac-remote-audio" muted />
      {streamState !== "connected" && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-6 text-center text-slate-400">
          <p className="font-semibold text-slate-200">
            {streamState === "connecting" ? "Waiting for Isaac Sim WebRTC…" : "WebRTC connection failed"}
          </p>
          <p className="max-w-xl text-xs">
            {streamState === "connecting"
              ? `${host}:${ISAAC_WEBRTC_SIGNAL_PORT} signaling · ${host}:${ISAAC_WEBRTC_MEDIA_PORT}/UDP media`
              : errorMessage}
          </p>
        </div>
      )}
    </div>
  );
};

export default IsaacWebRtcViewport;
