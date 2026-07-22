export interface BrowserLocationLike {
  hostname: string;
}

export const ISAAC_WEBRTC_SIGNAL_PORT = 49100;
export const ISAAC_WEBRTC_MEDIA_PORT = 47998;

export const resolveIsaacWebRtcHost = (location: BrowserLocationLike): string =>
  location.hostname || "127.0.0.1";
