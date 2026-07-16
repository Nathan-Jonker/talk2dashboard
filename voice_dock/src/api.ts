export type ToolName =
  | "inspect_workspace"
  | "data_batch"
  | "dashboard_batch"
  | "nearby_places"
  | "capture_dashboard"
  | "external_search";

export class ApiError extends Error {
  constructor(public status: number, public payload: Record<string, unknown>, message: string) {
    super(message);
  }
}

export function apiErrorDetail(cause: unknown): Record<string, unknown> | undefined {
  if (!(cause instanceof ApiError)) return undefined;
  const detail = cause.payload.detail;
  if (detail && typeof detail === "object") {
    const nested = (detail as Record<string, unknown>).error;
    if (nested && typeof nested === "object") return nested as Record<string, unknown>;
  }
  const direct = cause.payload.error;
  return direct && typeof direct === "object" ? direct as Record<string, unknown> : undefined;
}

const pendingRequests = new Set<AbortController>();

export function prepareToolPayload(
  name: ToolName,
  payload: Record<string, unknown>,
  dashboardVersion: number
) {
  return name === "dashboard_batch"
    ? { ...payload, expected_version: dashboardVersion }
    : { ...payload };
}

export function abortAllToolRequests() {
  pendingRequests.forEach((controller) => controller.abort());
  pendingRequests.clear();
}

export async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data?.detail?.message || data?.detail || `HTTP ${response.status}`;
    throw new ApiError(response.status, data as Record<string, unknown>, String(message));
  }
  return data as T;
}

export async function invokeTool(name: ToolName, payload: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent("talk2d:tool-start", { detail: { name } }));
  postMetric("tool_execution_start", { name });
  let policy: { version: number };
  let dashboard: { version: number };
  try {
    [policy, dashboard] = await Promise.all([
      jsonRequest<{ version: number }>("/api/policy"),
      jsonRequest<{ version: number }>("/api/dashboard/state")
    ]);
  } catch (cause) {
    const failure = {
      ok: false,
      error: {
        code: "CLIENT_PREFLIGHT_FAILED",
        message: cause instanceof Error ? cause.message : String(cause),
        retryable: true
      }
    };
    postMetric("tool_execution_end", { name, ok: false, error_code: failure.error.code });
    window.dispatchEvent(new CustomEvent("talk2d:tool-result", { detail: { name, result: failure } }));
    return JSON.stringify(failure);
  }
  const requestPayload = prepareToolPayload(name, payload, dashboard.version);
  const body: Record<string, unknown> = {
    conversation_id: window.localStorage.getItem("talk2d_conversation_id"),
    turn_id: window.localStorage.getItem("talk2d_turn_id"),
    request_id: crypto.randomUUID(),
    session_policy_version: policy.version,
    dashboard_version: dashboard.version,
    payload: requestPayload
  };
  const execute = async () => {
    const controller = new AbortController();
    pendingRequests.add(controller);
    try {
      return await jsonRequest<Record<string, unknown>>(`/api/tools/${name.replaceAll("_", "-")}`, {
        method: "POST", body: JSON.stringify(body), signal: controller.signal
      });
    } finally {
      pendingRequests.delete(controller);
    }
  };
  let result: Record<string, unknown>;
  try {
    try {
      result = await execute();
    } catch (cause) {
      const error = apiErrorDetail(cause);
      if (name !== "dashboard_batch" || error?.code !== "VERSION_CONFLICT") throw cause;
      const current = await jsonRequest<{ version: number }>("/api/dashboard/state");
      const requestPayload = body.payload as Record<string, unknown>;
      requestPayload.expected_version = current.version;
      body.dashboard_version = current.version;
      body.request_id = crypto.randomUUID();
      result = await execute();
    }
  } catch (cause) {
    const backendError = apiErrorDetail(cause);
    const failure = {
      ok: false,
      error: backendError || {
        code: "CLIENT_TOOL_FAILED",
        message: cause instanceof Error ? cause.message : String(cause),
        retryable: false
      }
    };
    postMetric("tool_execution_end", { name, ok: false, error_code: failure.error.code });
    window.dispatchEvent(new CustomEvent("talk2d:tool-result", { detail: { name, result: failure } }));
    return JSON.stringify(failure);
  }
  postMetric("tool_execution_end", { name, ok: true });
  if (name === "dashboard_batch") postMetric("dashboard_commit_accepted");
  window.dispatchEvent(new CustomEvent("talk2d:tool-result", { detail: { name, result } }));
  return JSON.stringify(result);
}

export function postMetric(eventType: string, payload: Record<string, unknown> = {}) {
  void fetch("/api/metrics/event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      conversation_id: window.localStorage.getItem("talk2d_conversation_id"),
      turn_id: window.localStorage.getItem("talk2d_turn_id"),
      event_type: eventType,
      monotonic_ms: performance.now(),
      payload
    })
  });
}

export function postConversationEvent(eventId: string, role: "user" | "agent", text: string, final: boolean) {
  const conversationId = window.localStorage.getItem("talk2d_conversation_id");
  if (!conversationId || !text.trim()) return;
  void fetch("/api/conversations/event", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      event_id: eventId,
      conversation_id: conversationId,
      turn_id: window.localStorage.getItem("talk2d_turn_id"),
      role,
      text,
      final
    })
  });
}
