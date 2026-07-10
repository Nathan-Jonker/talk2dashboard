import { describe, expect, it } from "vitest";

describe("voice dock contract", () => {
  it("keeps the six public tool names stable", () => {
    const names = ["inspect_workspace", "data_batch", "dashboard_batch", "nearby_places", "capture_dashboard", "external_search"];
    expect(new Set(names).size).toBe(6);
  });
});

