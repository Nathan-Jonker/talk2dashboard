export type WorkspacePolicy = {
  version: number;
  web_search_enabled: boolean;
  auto_update_enabled: boolean;
};

export const POLICY_CHANGED_EVENT = "talk2d:policy-changed";

export function webSearchStatusLabel(policy: WorkspacePolicy | null): string {
  if (!policy) return "Websearch-status laden";
  return policy.web_search_enabled ? "Websearch aan" : "Websearch uit";
}
