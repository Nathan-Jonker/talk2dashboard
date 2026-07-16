import { useEffect, useRef, useState } from "react";
import { Activity, Clock3, Database, Download, FileSearch, Search, Sparkles, Wrench, X } from "lucide-react";

import { jsonRequest } from "./api";

declare global {
  interface Window {
    talk2dSetCerebrasGeneration?: (requestId: string, active: boolean, mode: "automatic" | "manual") => void;
    talk2dAwaitDashboardRender?: (dashboardVersion: number, timeoutMs?: number) => Promise<unknown>;
  }
}

function signalCerebrasGeneration(requestId: string, active: boolean, mode: "automatic" | "manual") {
  if (window.talk2dSetCerebrasGeneration) {
    window.talk2dSetCerebrasGeneration(requestId, active, mode);
    return;
  }
  document.documentElement.dataset.cerebrasGenerating = active ? "true" : "false";
  window.dispatchEvent(new CustomEvent("talk2d:cerebras-generation", {
    detail: { active, mode, request_id: requestId }
  }));
}

type Stream = {
  stream_id: string;
  owner: string;
  status: string;
  newest_record_at?: string;
  record_count: number;
  provider?: string;
  message?: string;
  last_success_at?: string;
  age_seconds?: number;
  fixture?: boolean;
  fallback?: boolean;
};

type DashboardVersion = {
  version: number;
  created_at: string;
  created_by: string;
  reason: string;
  renderer_status: string;
};

type Policy = { version: number; web_search_enabled: boolean; auto_update_enabled: boolean };
type CapabilityInput = { name: string; type: string; required: boolean; description: string };
type AgentTool = {
  name: string;
  display_name: string;
  category: string;
  description: string;
  inputs: CapabilityInput[];
  outputs: string[];
  constraints: string[];
  examples: string[];
};
type SourceMetric = { id: string; label: string; unit: string };
type SourceInput = { name: string; type: string; required: boolean; description: string };
type SourceCapability = {
  stream_id: string;
  display_name: string;
  kind: string;
  description: string;
  inputs: SourceInput[];
  metrics: SourceMetric[];
  fields: string[];
  possibilities: string[];
  examples: string[];
  limitations: string[];
};
type EvaluationTurn = {
  turn_id?: string;
  conversation_id?: string;
  events: unknown[];
  latencies_ms: Record<string, number>;
};

type DashboardInitialization = {
  changed: boolean;
  dashboard_version: number;
  decision: string;
  next_automatic_at?: string | null;
  cooldown_minutes: number;
};

type Evidence = {
  source_ref: string;
  owner?: string;
  trust_tier?: string;
  quality_flags: string[];
  record: Record<string, unknown>;
  snapshot: Record<string, unknown>;
  bundle_versions: string[];
  fallback: { used: boolean; from?: string; reason?: string };
};

export function InfoDrawer({ open, evidenceRef, onClose }: { open: boolean; evidenceRef?: string | null; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [streams, setStreams] = useState<Stream[]>([]);
  const [history, setHistory] = useState<DashboardVersion[]>([]);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [tools, setTools] = useState<AgentTool[]>([]);
  const [sourceCatalog, setSourceCatalog] = useState<SourceCapability[]>([]);
  const [turns, setTurns] = useState<EvaluationTurn[]>([]);
  const [busy, setBusy] = useState(false);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);
  const [evidenceError, setEvidenceError] = useState<string | null>(null);
  const [redesignMessage, setRedesignMessage] = useState<string | null>(null);

  useEffect(() => {
    const root = document.documentElement;
    root.classList.toggle("talk2d-info-open", open);
    let secondFrame = 0;
    const firstFrame = window.requestAnimationFrame(() => {
      secondFrame = window.requestAnimationFrame(() => {
        window.dispatchEvent(new Event("resize"));
      });
    });
    return () => {
      window.cancelAnimationFrame(firstFrame);
      if (secondFrame) window.cancelAnimationFrame(secondFrame);
      root.classList.remove("talk2d-info-open");
    };
  }, [open]);

  useEffect(() => {
    if (!open || evidenceRef) return;
    void Promise.all([
      jsonRequest<Stream[]>("/api/streams"),
      jsonRequest<DashboardVersion[]>("/api/dashboard/configs"),
      jsonRequest<Policy>("/api/policy"),
      jsonRequest<EvaluationTurn[]>("/api/evaluation/turns"),
      jsonRequest<AgentTool[]>("/api/agent-tools"),
      jsonRequest<SourceCapability[]>("/api/source-catalog")
    ]).then(([sourceRows, versions, currentPolicy, recentTurns, agentTools, capabilities]) => {
      setStreams(sourceRows);
      setHistory(versions);
      setPolicy(currentPolicy);
      setTurns(recentTurns);
      setTools(agentTools);
      setSourceCatalog(capabilities);
    });
  }, [open, evidenceRef]);

  useEffect(() => {
    if (!open || !evidenceRef) {
      setEvidence(null);
      setEvidenceError(null);
      setEvidenceLoading(false);
      return;
    }
    let active = true;
    setEvidence(null);
    setEvidenceError(null);
    setEvidenceLoading(true);
    void jsonRequest<Evidence>(`/api/evidence/${encodeURIComponent(evidenceRef)}`)
      .then((result) => { if (active) setEvidence(result); })
      .catch((cause) => {
        if (active) setEvidenceError(cause instanceof Error ? cause.message : "Bronrecord kon niet worden geladen.");
      })
      .finally(() => { if (active) setEvidenceLoading(false); });
    return () => { active = false; };
  }, [open, evidenceRef]);

  useEffect(() => {
    if (!open) return;
    previousFocus.current = document.activeElement as HTMLElement | null;
    const drawer = drawerRef.current;
    drawer?.querySelector<HTMLElement>("button, a, input")?.focus();
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        onClose();
        return;
      }
      if (event.key !== "Tab" || !drawer) return;
      const focusable = [...drawer.querySelectorAll<HTMLElement>("button:not(:disabled), a[href], input:not(:disabled)")];
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", handleKey);
    return () => {
      window.removeEventListener("keydown", handleKey);
      previousFocus.current?.focus();
    };
  }, [open, onClose]);

  const toggleSearch = async () => {
    if (!policy || busy) return;
    setBusy(true);
    try {
      const next = await jsonRequest<Policy>("/api/dashboard/user-settings", {
        method: "POST",
        body: JSON.stringify({ web_search_enabled: !policy.web_search_enabled })
      });
      setPolicy(next);
    } finally {
      setBusy(false);
    }
  };

  const toggleRefresh = async () => {
    if (!policy || busy) return;
    setBusy(true);
    try {
      setPolicy(await jsonRequest<Policy>("/api/dashboard/user-settings", {
        method: "POST", body: JSON.stringify({ auto_update_enabled: !policy.auto_update_enabled })
      }));
    } finally { setBusy(false); }
  };

  const restore = async (version: number) => {
    if (busy) return;
    setBusy(true);
    try {
      await jsonRequest(`/api/dashboard/configs/${version}/restore`, { method: "POST", body: "{}" });
      window.dispatchEvent(new CustomEvent("talk2d:refresh"));
      setHistory(await jsonRequest<DashboardVersion[]>("/api/dashboard/configs"));
    } finally {
      setBusy(false);
    }
  };

  const redesignDashboard = async () => {
    if (busy) return;
    const requestId = `manual-${crypto.randomUUID()}`;
    setBusy(true);
    setRedesignMessage(null);
    signalCerebrasGeneration(requestId, true, "manual");
    try {
      const result = await jsonRequest<DashboardInitialization>("/api/dashboard/initialize", {
        method: "POST",
        body: JSON.stringify({ force: true })
      });
      if (!result.changed) {
        setRedesignMessage("Er kon geen nieuwe compositie worden gemaakt.");
        return;
      }
      if (!window.talk2dAwaitDashboardRender) {
        throw new Error("De browser-renderbevestiging is niet beschikbaar.");
      }
      await window.talk2dAwaitDashboardRender(result.dashboard_version, 20000);
      const nextHistory = await jsonRequest<DashboardVersion[]>("/api/dashboard/configs");
      const renderedVersion = nextHistory.find((item) => item.version === result.dashboard_version);
      if (renderedVersion?.renderer_status !== "rendered") {
        throw new Error(`Dashboardversie ${result.dashboard_version} is nog niet bevestigd.`);
      }
      setHistory(nextHistory);
      setRedesignMessage(`Dashboard opnieuw samengesteld en weergegeven als versie ${result.dashboard_version}.`);
    } catch (cause) {
      setRedesignMessage(cause instanceof Error ? cause.message : "AI-herontwerp mislukt.");
    } finally {
      signalCerebrasGeneration(requestId, false, "manual");
      setBusy(false);
    }
  };

  if (!open) return null;
  return (
    <div className="info-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside ref={drawerRef} className="info-drawer" role="dialog" aria-modal="true" aria-labelledby="info-title">
        <header className="info-drawer__head">
          <div>
            <span className="info-eyebrow">{evidenceRef ? "Herkomst" : "Werkruimte"}</span>
            <h2 id="info-title">{evidenceRef ? "Bronrecord" : "Data en mogelijkheden"}</h2>
          </div>
          <button className="voice-icon-btn" onClick={onClose} aria-label="Sluiten"><X size={19} /></button>
        </header>
        {evidenceLoading && <section className="info-section evidence-detail" role="status"><p>Bronrecord laden…</p></section>}
        {evidenceError && <section className="info-section evidence-detail" role="alert"><p>{evidenceError}</p></section>}
        {evidence && <section className="info-section evidence-detail">
          <h3><FileSearch size={17} /> Evidence</h3>
          <dl>
            <dt>Bron</dt><dd>{evidence.owner || evidence.source_ref}</dd>
            <dt>Trust tier</dt><dd>{evidence.trust_tier || "onbekend"}</dd>
            <dt>Provider</dt><dd>{String(evidence.snapshot.provider || "onbekend")}</dd>
            <dt>Waargenomen</dt><dd>{String(evidence.snapshot.observed_at || "onbekend")}</dd>
            <dt>Opgehaald</dt><dd>{String(evidence.snapshot.ingested_at || "onbekend")}</dd>
            <dt>Kwaliteit</dt><dd>{evidence.quality_flags.join(", ") || "geen flags"}</dd>
            <dt>Fallback</dt><dd>{evidence.fallback.used ? `${evidence.fallback.from || "ja"} (${evidence.fallback.reason || "reden onbekend"})` : "nee"}</dd>
          </dl>
          <details><summary>Genormaliseerd record</summary><pre>{JSON.stringify(evidence.record, null, 2)}</pre></details>
        </section>}
        {!evidenceRef && <>
        <section className="info-section">
          <h3><Activity size={17} /> Automatisch verversen</h3>
          <div className="policy-row">
            <p>Bronupdates worden alleen hervat wanneer deze instelling aan staat.</p>
            <label className="switch"><input aria-label="Automatisch verversen" type="checkbox" checked={policy?.auto_update_enabled || false} onChange={() => void toggleRefresh()} disabled={busy} /><span /></label>
          </div>
        </section>
        <section className="info-section">
          <h3><Sparkles size={17} /> AI-herontwerp</h3>
          <p>Cerebras herontwerpt niet bij iedere refresh. Automatisch gebeurt dit alleen wanneer er vijftien minuten geen dashboardwijziging is opgeslagen. Met deze knop start je het direct.</p>
          <button className="settings-action" type="button" onClick={() => void redesignDashboard()} disabled={busy}>
            <Sparkles size={16} /> Dashboard nu opnieuw samenstellen
          </button>
          {redesignMessage && <p className="settings-feedback" role="status">{redesignMessage}</p>}
        </section>
        <section className="info-section">
          <h3><Search size={17} /> Externe websearch</h3>
          <div className="policy-row">
            <p>Uitgangspunt is alleen de vaste databronnen. Webresultaten worden als onbevestigde context gelabeld.</p>
            <label className="switch"><input aria-label="Externe websearch inschakelen" type="checkbox" checked={policy?.web_search_enabled || false} onChange={() => void toggleSearch()} disabled={busy} /><span /></label>
          </div>
        </section>
        <section className="info-section">
          <h3><Database size={17} /> Bevraagbare streams</h3>
          <p>Iedere stream heeft een vast read-only contract. Open een bron om toegestane invoer, meetwaarden, uitvoervelden, analyses en beperkingen te bekijken.</p>
          <div className="capability-list source-capabilities">
            {sourceCatalog.map((source) => {
              const stream = streams.find((item) => item.stream_id === source.stream_id);
              return (
                <details className="capability-item source-capability" key={source.stream_id} data-status={stream?.status || "unknown"}>
                  <summary>
                    <span><strong>{source.display_name}</strong><small>{source.description}</small></span>
                    <b>{stream?.status || "onbekend"}</b>
                  </summary>
                  <div className="capability-body">
                    <div className="source-facts">
                      <span><b>{stream?.record_count ?? 0}</b> records</span>
                      <span><b>{source.kind}</b> datatype</span>
                      <span><b>{stream?.age_seconds != null ? `${Math.round(stream.age_seconds / 60)} min` : "onbekend"}</b> ouderdom</span>
                      <span><b>{stream?.provider || source.stream_id}</b> provider</span>
                    </div>
                    {(stream?.fixture || stream?.fallback) && <p className="source-warning">{stream.fixture ? "Fixturedata" : "Fallbackdata"}: niet verwarren met een volledige live bron.</p>}
                    <h4>Invoer voor deze bron</h4>
                    <dl className="contract-list source-input-list">
                      {source.inputs.map((input) => (
                        <div key={input.name}>
                          <dt><code>{input.name}</code><span>{input.type}{input.required ? " · verplicht" : " · optioneel"}</span></dt>
                          <dd>{input.description}</dd>
                        </div>
                      ))}
                    </dl>
                    {source.metrics.length > 0 && <>
                      <h4>Meetwaarden</h4>
                      <div className="metric-list">{source.metrics.map((metric) => <span key={metric.id}><strong>{metric.label}</strong><code>{metric.id}</code><small>{metric.unit}</small></span>)}</div>
                    </>}
                    <h4>Velden in de output</h4>
                    <div className="field-list">{source.fields.map((field) => <code key={field}>{field}</code>)}</div>
                    <h4>Wat kan ermee?</h4>
                    <div className="tag-list">{source.possibilities.map((possibility) => <span key={possibility}>{possibility}</span>)}</div>
                    <h4>Voorbeeldvragen</h4>
                    <ul className="example-list">{source.examples.map((example) => <li key={example}>“{example}”</li>)}</ul>
                    <h4>Let op</h4>
                    <ul>{source.limitations.map((limitation) => <li key={limitation}>{limitation}</li>)}</ul>
                  </div>
                </details>
              );
            })}
          </div>
        </section>
        <section className="info-section">
          <h3><Wrench size={17} /> Agenttools</h3>
          <p>Klap een tool open voor alle invoer, uitvoer, grenzen en voorbeeldopdrachten. Leestools wijzigen geen data; presentatietools wijzigen alleen de dashboardconfiguratie.</p>
          <div className="capability-list">
            {tools.map((tool) => (
              <details className="capability-item" key={tool.name}>
                <summary>
                  <span><strong>{tool.display_name}</strong><small>{tool.description}</small></span>
                  <b>{tool.category}</b>
                </summary>
                <div className="capability-body">
                  <code>{tool.name}</code>
                  <h4>Invoer</h4>
                  <dl className="contract-list">
                    {tool.inputs.map((input) => (
                      <div key={input.name}>
                        <dt><code>{input.name}</code><span>{input.type}{input.required ? " · verplicht" : " · optioneel"}</span></dt>
                        <dd>{input.description}</dd>
                      </div>
                    ))}
                  </dl>
                  <h4>Uitvoer</h4>
                  <ul>{tool.outputs.map((output) => <li key={output}>{output}</li>)}</ul>
                  <h4>Voorbeeldvragen</h4>
                  <ul className="example-list">{tool.examples.map((example) => <li key={example}>“{example}”</li>)}</ul>
                  <h4>Grenzen</h4>
                  <ul>{tool.constraints.map((constraint) => <li key={constraint}>{constraint}</li>)}</ul>
                </div>
              </details>
            ))}
          </div>
        </section>
        <section className="info-section info-section--plain">
          <h3>Wat de agent mag aanpassen</h3>
          <p>Paneltype, filters, kaartmodus, focus, volgorde, titel en tijdvenster. De agent bindt alleen aan gevalideerde handles en kan nooit bronwaarden schrijven.</p>
        </section>
        <section className="info-section">
          <h3><Activity size={17} /> Evaluatie en latency</h3>
          <div className="export-row">
            <a href="/api/export/metrics.csv"><Download size={15} /> Metrics CSV</a>
            <a href="/api/export/tools.csv"><Download size={15} /> Tools CSV</a>
            <a href="/api/export/state.json"><Download size={15} /> State JSON</a>
          </div>
          <div className="latency-list">
            {turns.length === 0 ? <p>Nog geen gemeten turns.</p> : turns.slice(-8).reverse().map((turn, index) => (
              <div key={turn.turn_id || index}>
                <span>{turn.turn_id ? turn.turn_id.slice(0, 8) : "sessie"}</span>
                <strong>{turn.latencies_ms.first_playable_audio != null ? `${Math.round(turn.latencies_ms.first_playable_audio)} ms audio` : "audio niet gemeten"}</strong>
                <b>{turn.latencies_ms.render_complete != null ? `${Math.round(turn.latencies_ms.render_complete)} ms render` : "geen render"}</b>
              </div>
            ))}
          </div>
        </section>
        <section className="info-section">
          <h3><Clock3 size={17} /> Dashboardgeschiedenis</h3>
          <p>Elke layoutwijziging wordt append-only opgeslagen. Live dataverversing maakt geen nieuwe configuratieversie.</p>
          <div className="version-list">
            {history.slice(0, 12).map((item, index) => (
              <button key={item.version} disabled={busy || index === 0} onClick={() => void restore(item.version)}>
                <strong>v{item.version}</strong><span>{item.reason}</span><b>{item.renderer_status}</b>
              </button>
            ))}
          </div>
        </section>
        </>}
      </aside>
    </div>
  );
}
