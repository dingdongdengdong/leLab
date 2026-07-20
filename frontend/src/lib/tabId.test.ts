import { describe, expect, it } from "vitest";

import { createTabId } from "./tabId";

describe("createTabId", () => {
  it("uses randomUUID when the secure-context API is available", () => {
    expect(
      createTabId({ randomUUID: () => "secure-id" }),
    ).toBe("secure-id");
  });

  it("still creates an id when randomUUID is unavailable over remote HTTP", () => {
    const id = createTabId({
      getRandomValues: (values) => {
        values.fill(7);
        return values;
      },
    });

    expect(id).toBe("07070707070707070707070707070707");
  });
});
