import { describe, expect, it } from "vitest";

import { resolveApiBaseUrl } from "./apiBaseUrl";

const storage = (value: string | null = null) => ({
  getItem: () => value,
  setItem: () => undefined,
});

describe("resolveApiBaseUrl", () => {
  it("uses the serving origin for a remote browser", () => {
    expect(
      resolveApiBaseUrl(
        {
          origin: "http://192.0.2.10:8000",
          hostname: "192.0.2.10",
          port: "8000",
          search: "",
        },
        storage(),
      ),
    ).toBe("http://192.0.2.10:8000");
  });

  it("does not reuse a localhost API URL on a remote browser", () => {
    expect(
      resolveApiBaseUrl(
        {
          origin: "http://192.0.2.10:8000",
          hostname: "192.0.2.10",
          port: "8000",
          search: "",
        },
        storage("http://localhost:8000"),
      ),
    ).toBe("http://192.0.2.10:8000");
  });

  it("keeps the backend default for a localhost Vite dev server", () => {
    expect(
      resolveApiBaseUrl(
        {
          origin: "http://localhost:8080",
          hostname: "localhost",
          port: "8080",
          search: "",
        },
        storage(),
      ),
    ).toBe("http://localhost:8000");
  });
});
