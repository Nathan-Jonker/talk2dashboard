import { useEffect, useRef, useState } from "react";
import { Activity, Clock3, Database, Download, Search, X } from "lucide-react";

import { jsonRequest } from "./api";

type Stream = {
  stream_id: string;
  owner: string;
  status: string;
  newest_record_at?: string;
  record_count: number;
  provider?: string;
  message?: string;
};

type DashboardVersion = {
  version: number;
  created_at: string;
  created_by: string;
  reason: string;
  renderer_status: string;
};

type Policy = { version: number; web_search_enabled: boolean; auto_update_enabled: boolean };
type EvaluationTurn = {
  turn_id?: string;
  conversation_id?: string;
  events: unknown[];
  latencies_ms: Record<string, number>;
};

export function InfoDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const drawerRef = useRef<HTMLElement | null>(null);
  const previousFocus = useRef<HTMLElement | null>(null);
  const [streams, setStreams] = useState<Stream[]>([]);
  const [history, setHistory] = useState<DashboardVersion[]>([]);
  const [policy, setPolicy] = useState<Policy | null>(null);
  const [turns, setTurns] = useState<EvaluationTurn[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    void Promise.all([
      jsonRequest<Stream[]>("/api/streams"),
      jsonRequest<DashboardVersion[]>("/api/dashboard/configs"),
      jsonRequest<Policy>("/api/policy"),
      jsonRequest<EvaluationTurn[]>("/api/evaluation/turns")
    ]).then(([sourceRows, versions, currentPolicy, recentTurns]) => {
      setStreams(sourceRows);
      setHistory(versions);
      setPolicy(currentPolicy);
      setTurns(recentTurns);
    });
  }, [open]);

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

  if (!open) return null;
  return (
    <div className="info-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <aside ref={drawerRef} className="info-drawer" role="dialog" aria-modal="true" aria-labelledby="info-title">
        <header className="info-drawer__head">
          <div><span className="info-eyebrow">Werkruimte</span><h2 id="info-title">Data en mogelijkheden</h2></div>
          <button className="voice-icon-btn" onClick={onClose} aria-label="Sluiten"><X size={19} /></button>
        </header>
        <section className="info-section">
          <h3><Search size={17} /> Externe websearch</h3>
          <div className="policy-row">
            <p>Uitgangspunt is alleen de vaste databronnen. Webresultaten worden als onbevestigde context gelabeld.</p>
            <label className="switch"><input aria-label="Externe websearch inschakelen" type="checkbox" checked={policy?.web_search_enabled || false} onChange={() => void toggleSearch()} disabled={busy} /><span /></label>
          </div>
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
          <h3><Database size={17} /> Bevraagbare streams</h3>
          <div className="source-list">
            {streams.map((stream) => (
              <div className="source-row" key={stream.stream_id} data-status={stream.status}>
                <div><strong>{stream.owner}</strong><span>{stream.provider || stream.stream_id}</span></div>
                <div><b>{stream.status}</b><span>{stream.record_count} records</span></div>
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
        <section className="info-section info-section--plain">
          <h3>Wat de agent mag aanpassen</h3>
          <p>Paneltype, filters, kaartmodus, focus, volgorde, titel en tijdvenster. De agent bindt alleen aan gevalideerde handles en kan nooit bronwaarden schrijven.</p>
        </section>
      </aside>
    </div>
  );
}
