import { describe, expect, it } from "vitest";

import { ApiError, apiErrorDetail, prepareToolPayload } from "./api";
import { clampDockPosition, parseStoredDockPosition } from "./dockPosition";
import { webSearchStatusLabel } from "./policy";
import {
  normalizeOperatorSelection,
  operatorContextMessage,
  operatorSelectionMeta,
  parseStoredOperatorSelection
} from "./selectionContext";

describe("voice dock contract", () => {
  it("keeps the six public tool names stable", () => {
    const names = ["inspect_workspace", "data_batch", "dashboard_batch", "nearby_places", "capture_dashboard", "external_search"];
    expect(new Set(names).size).toBe(6);
  });

  it("labels the compact websearch state without ambiguity", () => {
    expect(webSearchStatusLabel(null)).toBe("Websearch-status laden");
    expect(webSearchStatusLabel({ version: 1, web_search_enabled: false, auto_update_enabled: true })).toBe("Websearch uit");
    expect(webSearchStatusLabel({ version: 2, web_search_enabled: true, auto_update_enabled: true })).toBe("Websearch aan");
  });

  it("injects the current dashboard version without model inspection", () => {
    expect(prepareToolPayload("dashboard_batch", {
      expected_version: 2,
      operations: [],
      reason: "test"
    }, 9)).toEqual({ expected_version: 9, operations: [], reason: "test" });
    expect(prepareToolPayload("data_batch", { operations: [] }, 9)).toEqual({ operations: [] });
  });

  it("keeps a dragged overlay inside the visible viewport", () => {
    expect(clampDockPosition(
      { x: 1200, y: -20 },
      { width: 320, height: 180 },
      { width: 1000, height: 700 }
    )).toEqual({ x: 672, y: 8 });
  });

  it("rejects malformed persisted overlay positions", () => {
    expect(parseStoredDockPosition('{"x":24,"y":36}')).toEqual({ x: 24, y: 36 });
    expect(parseStoredDockPosition('{"x":"left","y":36}')).toBeNull();
    expect(parseStoredDockPosition("not-json")).toBeNull();
  });

  it("preserves direct and FastAPI-nested tool errors", () => {
    const direct = new ApiError(400, { error: { code: "INVALID_ARGUMENT" } }, "invalid");
    const nested = new ApiError(
      400,
      { detail: { error: { code: "VERSION_CONFLICT" } } },
      "conflict"
    );
    expect(apiErrorDetail(direct)).toEqual({ code: "INVALID_ARGUMENT" });
    expect(apiErrorDetail(nested)).toEqual({ code: "VERSION_CONFLICT" });
  });

  it("normalizes a selected dashboard record into silent agent context", () => {
    const selection = normalizeOperatorSelection({
      source_ref: "p2000:evt-42",
      title: "Brandmelding Moerdijk",
      latitude: 51.7,
      longitude: 4.6
    });
    expect(selection).toMatchObject({
      sourceRef: "p2000:evt-42",
      streamId: "p2000",
      recordId: "evt-42",
      title: "Brandmelding Moerdijk"
    });
    expect(operatorContextMessage(selection!)).toContain("inspect_workspace is niet nodig");
    expect(operatorContextMessage(selection!)).toContain("query_nearby");
    expect(operatorSelectionMeta(selection!)).toBe("P2000 · Record evt-42");
  });

  it("routes records without source coordinates through geocoding", () => {
    const selection = normalizeOperatorSelection({
      source_ref: "p2000:evt-43",
      title: "Wateroverlast Lupinestraat Dedemsvaart",
      description: "Wateroverlast in Dedemsvaart"
    });
    const context = operatorContextMessage(selection!);
    expect(context).toContain("nearby_places");
    expect(context).toContain("origin_text");
    expect(context).toContain("query_nearby");
    expect(context).toContain("ongefilterde landelijke query");
  });

  it("uses the same missing-coordinate route for another source", () => {
    const selection = normalizeOperatorSelection({
      source_ref: "ns_disruptions:evt-7",
      title: "Storing rond Utrecht Centraal",
      description: "Treinverkeer rond station Utrecht Centraal is beperkt"
    });
    const context = operatorContextMessage(selection!);
    expect(context).toContain("origin_text");
    expect(context).toContain("query_nearby");
    expect(context).not.toContain("P2000-bronrecord");
  });

  it("uses a resolved P2000 focus directly for nearby data and facilities", () => {
    const selection = normalizeOperatorSelection({
      source_ref: "p2000:evt-44",
      title: "Buitenbrand Kuifeend Bedum",
      latitude: 53.30,
      longitude: 6.60,
      location_label: "Kuifeend, Bedum",
      location_source: "geocoded",
      resolution_id: "locres_bedum"
    });
    const context = operatorContextMessage(selection!);
    expect(context).toContain("origin_resolution_id=locres_bedum");
    expect(context).toContain("query_source_ref");
    expect(context).toContain("source_ref=p2000:evt-44");
    expect(context).toContain("Zoek het record niet opnieuw in de actuele feed");
    expect(context).toContain("nearby_places");
  });

  it("rejects malformed persisted operator selections", () => {
    expect(parseStoredOperatorSelection('{"sourceRef":"ndw_incidents:road-1","title":"A12"}'))
      .toMatchObject({ streamId: "ndw_incidents", recordId: "road-1" });
    expect(parseStoredOperatorSelection('{"sourceRef":"nos_rss:2026-07-15T10:30:00Z","title":"Nieuws"}'))
      .toMatchObject({ streamId: "nos_rss", recordId: "2026-07-15T10:30:00Z" });
    expect(parseStoredOperatorSelection('{"title":"zonder bron"}')).toBeNull();
    expect(parseStoredOperatorSelection("not-json")).toBeNull();
  });

});
