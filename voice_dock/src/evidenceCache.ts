import { jsonRequest } from "./api";

export type Evidence = {
  source_ref: string;
  owner?: string;
  trust_tier?: string;
  quality_flags: string[];
  record: Record<string, unknown>;
  snapshot: Record<string, unknown>;
  bundle_versions: string[];
  fallback: { used: boolean; from?: string; reason?: string };
};

const TTL_MS = 60_000;
const MAX_ENTRIES = 128;
const cache = new Map<string, { expiresAt: number; value: Evidence }>();
const pending = new Map<string, Promise<Evidence>>();

export function cachedEvidence(sourceRef: string): Evidence | null {
  const entry = cache.get(sourceRef);
  if (!entry || entry.expiresAt <= Date.now()) {
    cache.delete(sourceRef);
    return null;
  }
  cache.delete(sourceRef);
  cache.set(sourceRef, entry);
  return entry.value;
}

export function loadEvidence(sourceRef: string): Promise<Evidence> {
  const cached = cachedEvidence(sourceRef);
  if (cached) return Promise.resolve(cached);
  const inFlight = pending.get(sourceRef);
  if (inFlight) return inFlight;
  const request = jsonRequest<Evidence>(`/api/evidence/${encodeURIComponent(sourceRef)}`)
    .then((value) => {
      cache.set(sourceRef, { expiresAt: Date.now() + TTL_MS, value });
      while (cache.size > MAX_ENTRIES) {
        const oldest = cache.keys().next().value;
        if (oldest === undefined) break;
        cache.delete(oldest);
      }
      return value;
    })
    .finally(() => pending.delete(sourceRef));
  pending.set(sourceRef, request);
  return request;
}

export function prefetchEvidence(sourceRef: string): void {
  void loadEvidence(sourceRef).catch(() => undefined);
}
