import { describe, expect, it } from "vitest";

import {
  ISAAC_WEBRTC_MEDIA_PORT,
  ISAAC_WEBRTC_SIGNAL_PORT,
  resolveIsaacWebRtcHost,
} from "./isaacWebRtc";

describe("Isaac Sim WebRTC connection settings", () => {
  it("uses the LeLab page host so remote and Tailscale clients do not dial localhost", () => {
    expect(resolveIsaacWebRtcHost({ hostname: "100.96.41.100" })).toBe(
      "100.96.41.100",
    );
  });

  it("keeps Isaac Sim 6.0's default signaling and media ports", () => {
    expect(ISAAC_WEBRTC_SIGNAL_PORT).toBe(49100);
    expect(ISAAC_WEBRTC_MEDIA_PORT).toBe(47998);
  });
});
