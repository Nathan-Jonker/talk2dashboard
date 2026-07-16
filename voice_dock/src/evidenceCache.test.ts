import { beforeEach, describe, expect, it, vi } from "vitest";

import { loadEvidence } from "./evidenceCache";

describe("evidence cache", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("deduplicates repeated record loads", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        source_ref: "p2000:cache-test",
        quality_flags: [],
        record: {},
        snapshot: {},
        bundle_versions: [],
        fallback: { used: false }
      })
    });
    vi.stubGlobal("fetch", fetchMock);

    const first = await loadEvidence("p2000:cache-test");
    const second = await loadEvidence("p2000:cache-test");

    expect(second).toBe(first);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
