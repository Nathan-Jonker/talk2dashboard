export type OperatorSelection = {
  sourceRef: string;
  streamId: string;
  recordId: string;
  title: string;
  description?: string;
  latitude?: number;
  longitude?: number;
  layer?: string;
};

const SOURCE_LABELS: Record<string, string> = {
  knmi_observations: "KNMI",
  luchtmeetnet: "Luchtmeetnet",
  ndw_incidents: "NDW",
  nos_rss: "NOS",
  ns_disruptions: "NS",
  p2000: "P2000",
  rws_water: "Rijkswaterstaat"
};

function optionalNumber(value: unknown): number | undefined {
  const number = Number(value);
  return Number.isFinite(number) ? number : undefined;
}

export function normalizeOperatorSelection(value: unknown): OperatorSelection | null {
  if (!value || typeof value !== "object") return null;
  const item = value as Record<string, unknown>;
  const sourceRef = String(item.source_ref || item.sourceRef || "").trim();
  if (!sourceRef) return null;
  const separator = sourceRef.indexOf(":");
  const sourceStream = separator >= 0 ? sourceRef.slice(0, separator) : "";
  const sourceRecord = separator >= 0 ? sourceRef.slice(separator + 1) : "";
  const streamId = String(item.stream_id || item.streamId || sourceStream).trim();
  const recordId = String(item.record_id || item.recordId || sourceRecord).trim();
  const title = String(item.title || "Bronrecord").trim() || "Bronrecord";
  return {
    sourceRef,
    streamId,
    recordId,
    title,
    description: String(item.description || "").trim() || undefined,
    latitude: optionalNumber(item.latitude),
    longitude: optionalNumber(item.longitude),
    layer: String(item.layer || "").trim() || undefined
  };
}

export function parseStoredOperatorSelection(value: string | null): OperatorSelection | null {
  if (!value) return null;
  try {
    return normalizeOperatorSelection(JSON.parse(value));
  } catch {
    return null;
  }
}

export function operatorSelectionMeta(selection: OperatorSelection): string {
  const source = SOURCE_LABELS[selection.streamId] || selection.streamId || "Onbekende bron";
  return selection.recordId ? `${source} · Record ${selection.recordId}` : source;
}

export function operatorContextMessage(selection: OperatorSelection): string {
  const hasCoordinates = selection.latitude !== undefined && selection.longitude !== undefined;
  const location = hasCoordinates
    ? `; coordinaten=${selection.latitude!.toFixed(5)},${selection.longitude!.toFixed(5)}`
    : "";
  const spatialInstruction = hasCoordinates
    ? "Gebruik bij een ruimtelijke vervolgvraag direct data_batch met een exacte origin-query en query_nearby; inspect_workspace is niet nodig."
    : selection.streamId === "p2000"
      ? `Dit P2000-bronrecord bevat geen ruwe coordinaten. Gebruik voor vaste databronnen rond deze melding direct data_batch query_nearby met origin_text=${JSON.stringify(`${selection.title} ${selection.description || ""}`.trim())} en de gevraagde radius; gebruik nearby_places met dezelfde origin_text alleen voor voorzieningen. Val nooit terug op een ongefilterde landelijke query. Elk radiusresultaat moet distance_m bevatten. Label de positie als gegeocodeerd.`
      : "Dit bronrecord bevat geen kaartcoordinaten; vraag alleen om verduidelijking wanneer titel en omschrijving geen bruikbare plaats bevatten.";
  return [
    "Stille dashboardcontext voor vervolgvragen; antwoord hier nu niet op.",
    `De operator selecteerde source_ref=${selection.sourceRef}`,
    `stream=${selection.streamId || "onbekend"}`,
    `record_id=${selection.recordId || "onbekend"}`,
    `label=${selection.title}${location}.`,
    "Koppel woorden als dit, hier, deze melding, dit meetpunt of deze plek aan dit record.",
    spatialInstruction
  ].join(" ");
}
