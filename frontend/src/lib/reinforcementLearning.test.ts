import { describe, expect, it } from "vitest";
import { reinforcementLearningFrameUrl } from "./jobsApi";

describe("reinforcement learning API", () => {
  it("encodes job ids and cache-busts policy frames", () => {
    expect(reinforcementLearningFrameUrl("http://localhost:8000", "job / one", 42))
      .toBe("http://localhost:8000/jobs/job%20%2F%20one/frame?t=42");
  });
});
