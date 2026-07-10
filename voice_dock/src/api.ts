export type ToolName =
  | "inspect_workspace"
  | "data_batch"
  | "dashboard_batch"
  | "nearby_places"
  | "capture_dashboard"
  | "external_search";

export async function jsonRequest<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) }
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = data?.detail?.message || data?.detail || `HTTP ${response.status}`;
    throw new Error(String(message));
  }
  return data as T;
}

export async function invokeTool(name: ToolName, payload: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent("talk2d:tool-start", { detail: { name } }));
  postMetric("tool_execution_start", { name });
  const policy = await jsonRequest<{ version: number }>("/api/policy");
  const dashboard = await jsonRequest<{ version: number }>("/api/dashboard/state");
  const body = {
    conversation_id: window.localStorage.getItem("talk2d_conversation_id"),
    request_id: crypto.randomUUID(),
    session_policy_version: policy.version,
    dashboard_version: dashboard.version,
    payload
  };
  const result = await jsonRequest<Record<string, unknown>>(`/api/tools/${name.replaceAll("_", "-")}`, {
    method: "POST",
    body: JSON.stringify(body)
  });
  postMetric("tool_execution_end", { name });
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
