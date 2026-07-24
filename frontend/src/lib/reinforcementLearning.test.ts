import { describe, expect, it } from "vitest";
import {
  getRlReadiness,
  reinforcementLearningFrameUrl,
  ReinforcementLearningRequest,
} from "./jobsApi";

const config = (distribution_zip: string): ReinforcementLearningRequest => ({
  task: "SuperArmIsaacPickLift-v0",
  runner: "local",
  seed: 1000,
  episode_length_steps: 150,
  training_steps: 20_000,
  online_buffer_capacity: 100_000,
  learning_starts: 100,
  batch_size: 256,
  actor_lr: 0.0003,
  critic_lr: 0.0003,
  temperature_lr: 0.0003,
  checkpoint_frequency: 5000,
  distribution_zip,
  distribution_sha256: "c356d1157318b72532b82d73270ef06b5b11ed5b8a90641ea4e431941e4554f7",
  learner_port: 50051,
  bridge_port: 8765,
  camera_preview: true,
});

describe("reinforcement learning API", () => {
  it("encodes job ids and cache-busts policy frames", () => {
    expect(reinforcementLearningFrameUrl("http://localhost:8000", "job / one", 42))
      .toBe("http://localhost:8000/jobs/job%20%2F%20one/frame?t=42");
  });

  it("lets the server supply the confirmed distribution path", async () => {
    const urls: string[] = [];
    const fetcher = async (url: string) => {
      urls.push(url);
      return new Response(JSON.stringify({
        ready: true,
        checks: {},
        distribution_zip: "/server/confirmed-v3.zip",
        distribution_sha256: config("").distribution_sha256,
      }), { status: 200, headers: { "content-type": "application/json" } });
    };

    const readiness = await getRlReadiness("http://localhost:8000", fetcher, config(""));
    expect(readiness.distribution_zip).toBe("/server/confirmed-v3.zip");
    expect(urls[0]).not.toContain("distribution_zip=");
  });
});
