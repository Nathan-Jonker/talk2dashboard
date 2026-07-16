import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, PointerEvent as ReactPointerEvent } from "react";
import {
  BarChart3,
  CircleHelp,
  CircleStop,
  Info,
  GripVertical,
  MapPinned,
  MessageSquareText,
  Mic,
  Search,
  Send,
  SlidersHorizontal,
  Sparkles,
  X
} from "lucide-react";
import { useConversation } from "@elevenlabs/react";

import { abortAllToolRequests, invokeTool, jsonRequest, postConversationEvent, postMetric } from "./api";
import { VoiceRing } from "./VoiceRing";
import { InfoDrawer } from "./InfoDrawer";
import { clampDockPosition, parseStoredDockPosition } from "./dockPosition";
import type { DockPosition } from "./dockPosition";
import {
  normalizeOperatorSelection,
  operatorContextMessage,
  operatorSelectionMeta,
  parseStoredOperatorSelection
} from "./selectionContext";
import type { OperatorSelection } from "./selectionContext";

type TranscriptItem = { id: string; role: "user" | "agent" | "system"; text: string; final: boolean };
type DashboardOrigin = "agent" | "system" | "user" | "unknown";
type ConnectionAttempt = "voice-webrtc" | "voice-websocket" | "text-websocket" | null;
type DragState = { pointerId: number; startX: number; startY: number; originX: number; originY: number };
type CerebrasGenerationMode = "automatic" | "manual";

const DOCK_POSITION_KEY = "talk2d_voice_dock_position";
const OPERATOR_SELECTION_KEY = "talk2d_operator_selection";

function readableConnectionError(cause: unknown): string {
  const message = cause instanceof Error ? cause.message : String(cause);
  if (cause instanceof DOMException && cause.name === "NotAllowedError") {
    return "Geef deze pagina microfoontoegang om een gesprek te starten.";
  }
  if (cause instanceof DOMException && cause.name === "NotFoundError") {
    return "Er is geen beschikbare microfoon gevonden.";
  }
  if (/signal connection|failed to fetch|blocked_by_client/i.test(message)) {
    return "De realtimeverbinding is door de browser of een extensie geblokkeerd.";
  }
  return message;
}

function localConversationUrl(): string {
  const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${window.location.host}/api/session/elevenlabs-proxy`;
}

export function VoiceDock() {
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [text, setText] = useState("");
  const [activeToolCount, setActiveToolCount] = useState(0);
  const [error, setError] = useState("");
  const [infoOpen, setInfoOpen] = useState(false);
  const [guideOpen, setGuideOpen] = useState(false);
  const [dashboardOrigin, setDashboardOrigin] = useState<DashboardOrigin>("unknown");
  const [expanded, setExpanded] = useState(false);
  const [evidenceRef, setEvidenceRef] = useState<string | null>(null);
  const [connecting, setConnecting] = useState(false);
  const [awaitingAgent, setAwaitingAgent] = useState(false);
  const [operatorSelection, setOperatorSelection] = useState<OperatorSelection | null>(() => (
    typeof window === "undefined"
      ? null
      : parseStoredOperatorSelection(window.sessionStorage.getItem(OPERATOR_SELECTION_KEY))
  ));
  const [cerebrasGeneration, setCerebrasGeneration] = useState<{ active: boolean; mode: CerebrasGenerationMode }>(() => ({
    active: typeof document !== "undefined" && document.documentElement.dataset.cerebrasGenerating === "true",
    mode: "automatic"
  }));
  const [dockPosition, setDockPosition] = useState<DockPosition | null>(() => (
    typeof window === "undefined" ? null : parseStoredDockPosition(window.localStorage.getItem(DOCK_POSITION_KEY))
  ));
  const [dragging, setDragging] = useState(false);
  const dockRef = useRef<HTMLElement | null>(null);
  const transcriptRef = useRef<HTMLDivElement | null>(null);
  const dragRef = useRef<DragState | null>(null);
  const cleanupStarted = useRef(false);
  const sessionActive = useRef(false);
  const audioSeenForTurn = useRef(false);
  const agentTextSeenForTurn = useRef(false);
  const previousMode = useRef<string | undefined>(undefined);
  const connectionAttempt = useRef<ConnectionAttempt>(null);
  const fallbackInFlight = useRef(false);
  const websocketFallbackRef = useRef<() => Promise<void>>(async () => undefined);
  const lastSentSelection = useRef("");

  const clientTools = useMemo(() => ({
    inspect_workspace: async (parameters: Record<string, unknown>) => invokeTool("inspect_workspace", parameters),
    data_batch: async (parameters: Record<string, unknown>) => invokeTool("data_batch", parameters),
    dashboard_batch: async (parameters: Record<string, unknown>) => invokeTool("dashboard_batch", parameters),
    nearby_places: async (parameters: Record<string, unknown>) => invokeTool("nearby_places", parameters),
    capture_dashboard: async (parameters: Record<string, unknown>) => invokeTool("capture_dashboard", parameters),
    external_search: async (parameters: Record<string, unknown>) => invokeTool("external_search", parameters)
  }), []);

  const conversation = useConversation({
    clientTools,
    onConnect: ({ conversationId }: { conversationId?: string }) => {
      connectionAttempt.current = null;
      fallbackInFlight.current = false;
      sessionActive.current = true;
      cleanupStarted.current = false;
      setConnecting(false);
      setError("");
      setExpanded(true);
      setGuideOpen(false);
      if (conversationId) {
        window.localStorage.setItem("talk2d_conversation_id", conversationId);
        void fetch("/api/session/register", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ conversation_id: conversationId })
        });
      }
      postMetric("connection_established", { conversation_id: conversationId });
    },
    onDisconnect: () => {
      const unexpected = sessionActive.current && !cleanupStarted.current;
      sessionActive.current = false;
      setConnecting(false);
      if (unexpected) setError("De realtimeverbinding is onverwacht verbroken.");
      postMetric("conversation_disconnected");
    },
    onError: (message: unknown) => {
      sessionActive.current = false;
      if (connectionAttempt.current === "voice-webrtc" && !fallbackInFlight.current) {
        connectionAttempt.current = "voice-websocket";
        fallbackInFlight.current = true;
        postMetric("webrtc_connection_failed", { reason: readableConnectionError(message) });
        void websocketFallbackRef.current();
        return;
      }
      connectionAttempt.current = null;
      fallbackInFlight.current = false;
      setError(readableConnectionError(message));
      setConnecting(false);
      postMetric("conversation_error", { message: String(message) });
    },
    onMessage: (message) => {
      const payload = message as unknown as Record<string, unknown>;
      const role = payload.source === "user" ? "user" : "agent";
      const value = String(payload.message || payload.text || "").trim();
      if (!value) return;
      setExpanded(true);
      setGuideOpen(false);
      const id = String(payload.messageId || payload.id || crypto.randomUUID());
      setItems((current) => {
        const existing = current.findIndex((item) => item.id === id);
        const next: TranscriptItem = { id, role, text: value, final: payload.isFinal !== false };
        if (existing < 0) return [...current.slice(-11), next];
        return current.map((item, index) => index === existing ? next : item);
      });
      const isFinal = payload.isFinal !== false;
      if (role === "user" && isFinal) {
        const turnId = crypto.randomUUID();
        window.localStorage.setItem("talk2d_turn_id", turnId);
        audioSeenForTurn.current = false;
        agentTextSeenForTurn.current = false;
        postMetric("end_of_user_speech", { proxy: "final_transcript" });
        postMetric("final_transcript");
        setAwaitingAgent(true);
      } else if (role === "user") {
        postMetric("user_transcript", { final: false });
      } else {
        setAwaitingAgent(false);
        if (!agentTextSeenForTurn.current) {
          agentTextSeenForTurn.current = true;
          postMetric("agent_text", { final: isFinal });
        }
      }
      postConversationEvent(id, role, value, isFinal);
    },
    onAudio: () => {
      setAwaitingAgent(false);
      if (!audioSeenForTurn.current) {
        audioSeenForTurn.current = true;
        postMetric("first_playable_audio");
      }
    },
    onModeChange: ({ mode }: { mode?: string }) => {
      postMetric("conversation_mode", { mode });
      if (previousMode.current === "speaking" && mode === "listening") {
        postMetric("turn_complete");
        window.localStorage.removeItem("talk2d_turn_id");
      }
      previousMode.current = mode;
    }
  });

  useEffect(() => {
    const onTool = () => setActiveToolCount((current) => current + 1);
    const onResult = () => {
      setActiveToolCount((current) => Math.max(0, current - 1));
      window.setTimeout(() => window.dispatchEvent(new CustomEvent("talk2d:refresh")), 20);
    };
    window.addEventListener("talk2d:tool-start", onTool);
    window.addEventListener("talk2d:tool-result", onResult);
    return () => {
      window.removeEventListener("talk2d:tool-start", onTool);
      window.removeEventListener("talk2d:tool-result", onResult);
    };
  }, []);

  useEffect(() => {
    if (!expanded) return;
    const frame = window.requestAnimationFrame(() => {
      const transcript = transcriptRef.current;
      if (transcript) transcript.scrollTop = transcript.scrollHeight;
    });
    return () => window.cancelAnimationFrame(frame);
  }, [expanded, items]);

  useEffect(() => {
    const openEvidence = (event: Event) => {
      const detail = (event as CustomEvent<{ source_ref?: string }>).detail;
      if (!detail?.source_ref) return;
      setEvidenceRef(detail.source_ref);
      setInfoOpen(true);
    };
    window.addEventListener("talk2d:open-evidence", openEvidence);
    return () => window.removeEventListener("talk2d:open-evidence", openEvidence);
  }, []);

  useEffect(() => {
    const selectContext = (event: Event) => {
      const selection = normalizeOperatorSelection((event as CustomEvent<unknown>).detail);
      if (!selection) return;
      setOperatorSelection(selection);
      window.sessionStorage.setItem(OPERATOR_SELECTION_KEY, JSON.stringify(selection));
      setGuideOpen(false);
    };
    window.addEventListener("talk2d:select-context", selectContext);
    return () => window.removeEventListener("talk2d:select-context", selectContext);
  }, []);

  useEffect(() => {
    if (conversation.status !== "connected" || !operatorSelection) return;
    const contextKey = `${conversation.getId?.() || "conversation"}:${operatorSelection.sourceRef}`;
    if (lastSentSelection.current === contextKey) return;
    conversation.sendContextualUpdate(operatorContextMessage(operatorSelection));
    lastSentSelection.current = contextKey;
    postMetric("operator_context_selected", {
      source_ref: operatorSelection.sourceRef,
      stream_id: operatorSelection.streamId
    });
  }, [conversation, conversation.status, operatorSelection]);

  const end = useCallback(async () => {
    if (cleanupStarted.current || !sessionActive.current) return;
    cleanupStarted.current = true;
    abortAllToolRequests();
    setActiveToolCount(0);
    try {
      await conversation.endSession();
    } finally {
      sessionActive.current = false;
      const id = window.localStorage.getItem("talk2d_conversation_id");
      void fetch("/api/session/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: id }) });
      window.localStorage.removeItem("talk2d_conversation_id");
      setAwaitingAgent(false);
      postMetric("conversation_ended");
    }
  }, [conversation]);

  const clearOperatorSelection = () => {
    if (conversation.status === "connected" && operatorSelection) {
      conversation.sendContextualUpdate(
        `Stille dashboardcontext; antwoord hier nu niet op. De operatorselectie ${operatorSelection.sourceRef} is gewist. Verwijs niet langer met dit, hier of deze melding naar dat record.`
      );
    }
    setOperatorSelection(null);
    lastSentSelection.current = "";
    window.sessionStorage.removeItem(OPERATOR_SELECTION_KEY);
    window.dispatchEvent(new CustomEvent("talk2d:clear-context"));
    postMetric("operator_context_cleared");
  };

  const endRef = useRef(end);
  endRef.current = end;

  useEffect(() => {
    const close = () => void endRef.current();
    window.addEventListener("pagehide", close);
    window.addEventListener("beforeunload", close);
    return () => {
      window.removeEventListener("pagehide", close);
      window.removeEventListener("beforeunload", close);
      void endRef.current();
    };
  }, []);

  useEffect(() => {
    const openInfo = () => setInfoOpen(true);
    window.addEventListener("talk2d:open-info", openInfo);
    return () => window.removeEventListener("talk2d:open-info", openInfo);
  }, []);

  useEffect(() => {
    const updateGeneration = (event: Event) => {
      const detail = (event as CustomEvent<{ active?: boolean; mode?: CerebrasGenerationMode }>).detail;
      setCerebrasGeneration({
        active: detail?.active === true,
        mode: detail?.mode === "manual" ? "manual" : "automatic"
      });
    };
    window.addEventListener("talk2d:cerebras-generation", updateGeneration);
    return () => window.removeEventListener("talk2d:cerebras-generation", updateGeneration);
  }, []);

  useEffect(() => {
    if (!guideOpen) return;
    void jsonRequest<Array<{ created_by?: DashboardOrigin }>>("/api/dashboard/configs")
      .then((versions) => setDashboardOrigin(versions[0]?.created_by || "unknown"))
      .catch(() => setDashboardOrigin("unknown"));
  }, [guideOpen]);

  useEffect(() => {
    if (conversation.status !== "connected") return;
    const timeout = window.setTimeout(() => void end(), 15 * 60 * 1000);
    return () => window.clearTimeout(timeout);
  }, [conversation.status, end]);

  const startWebsocketFallback = async () => {
    try {
      const id = await conversation.startSession({
        signedUrl: localConversationUrl(),
        connectionType: "websocket"
      });
      window.localStorage.setItem("talk2d_conversation_id", String(id));
      postMetric("connection_fallback", { from: "webrtc", to: "websocket" });
    } catch (websocketError) {
      connectionAttempt.current = null;
      fallbackInFlight.current = false;
      const detail = readableConnectionError(websocketError);
      setError(
        /blocked|signal connection|failed to fetch/i.test(detail)
          ? "Chrome blokkeert de realtimeverbinding. Sta livekit.rtc.elevenlabs.io en api.elevenlabs.io toe in je contentblocker."
          : detail
      );
      setConnecting(false);
      postMetric("websocket_connection_failed", { reason: detail });
    }
  };
  websocketFallbackRef.current = startWebsocketFallback;

  const start = async () => {
    setError("");
    setConnecting(true);
    setExpanded(true);
    setGuideOpen(false);
    cleanupStarted.current = false;
    connectionAttempt.current = "voice-webrtc";
    fallbackInFlight.current = false;
    postMetric("connection_start");
    try {
      const permissionStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      permissionStream.getTracks().forEach((track) => track.stop());
      const token = await jsonRequest<{ conversation_token: string }>("/api/session/elevenlabs-token", { method: "POST", body: "{}" });
      const id = await conversation.startSession({
        conversationToken: token.conversation_token,
        connectionType: "webrtc"
      });
      window.localStorage.setItem("talk2d_conversation_id", String(id));
    } catch (webrtcError) {
      connectionAttempt.current = "voice-websocket";
      fallbackInFlight.current = true;
      postMetric("webrtc_connection_failed", { reason: readableConnectionError(webrtcError) });
      try {
        await conversation.endSession();
      } catch {
        // A rejected WebRTC handshake may have no active session to close.
      }
      await startWebsocketFallback();
    }
  };

  const submit = async () => {
    const value = text.trim();
    if (!value) return;
    setExpanded(true);
    setGuideOpen(false);
    if (conversation.status !== "connected") {
      try {
        connectionAttempt.current = "text-websocket";
        const id = await conversation.startSession({ signedUrl: localConversationUrl(), connectionType: "websocket", textOnly: true });
        sessionActive.current = true;
        window.localStorage.setItem("talk2d_conversation_id", String(id));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
        return;
      }
    }
    try {
      conversation.sendUserMessage(value);
      setItems((current) => [...current.slice(-11), { id: crypto.randomUUID(), role: "user", text: value, final: true }]);
      setText("");
    } catch (cause) {
      sessionActive.current = false;
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const signalUserActivity = () => {
    if (conversation.status !== "connected" || !sessionActive.current) return;
    try {
      conversation.sendUserActivity();
    } catch {
      sessionActive.current = false;
    }
  };

  const state = error ? "error" : connecting ? "connecting" : activeToolCount > 0 ? "tool" : conversation.isSpeaking ? "speaking" : awaitingAgent ? "thinking" : conversation.status === "connected" ? "listening" : "offline";
  const labels: Record<string, string> = {
    offline: "Offline", connecting: "Verbinden", listening: "Luisteren", thinking: "Denken",
    tool: "Tool uitvoeren", speaking: "Spreken", error: "Fout"
  };

  const persistDockPosition = useCallback((position: DockPosition) => {
    window.localStorage.setItem(DOCK_POSITION_KEY, JSON.stringify(position));
  }, []);

  const fitDockToViewport = useCallback((position: DockPosition): DockPosition => {
    const dock = dockRef.current;
    if (!dock) return position;
    return clampDockPosition(
      position,
      { width: dock.offsetWidth, height: dock.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight }
    );
  }, []);

  useEffect(() => {
    const dock = dockRef.current;
    if (!dock) return;
    const keepInView = () => {
      setDockPosition((current) => {
        if (!current) return current;
        const next = fitDockToViewport(current);
        if (next.x === current.x && next.y === current.y) return current;
        persistDockPosition(next);
        return next;
      });
    };
    const observer = new ResizeObserver(keepInView);
    observer.observe(dock);
    window.addEventListener("resize", keepInView);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", keepInView);
    };
  }, [fitDockToViewport, persistDockPosition]);

  const beginDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (event.button !== 0) return;
    const dock = dockRef.current;
    if (!dock) return;
    const rect = dock.getBoundingClientRect();
    dragRef.current = {
      pointerId: event.pointerId,
      startX: event.clientX,
      startY: event.clientY,
      originX: rect.left,
      originY: rect.top
    };
    event.currentTarget.setPointerCapture(event.pointerId);
    setDockPosition({ x: rect.left, y: rect.top });
    setDragging(true);
    event.preventDefault();
  };

  const moveDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    setDockPosition(fitDockToViewport({
      x: drag.originX + event.clientX - drag.startX,
      y: drag.originY + event.clientY - drag.startY
    }));
  };

  const finishDrag = (event: ReactPointerEvent<HTMLButtonElement>) => {
    const drag = dragRef.current;
    if (!drag || drag.pointerId !== event.pointerId) return;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
    dragRef.current = null;
    setDragging(false);
    setDockPosition((current) => {
      if (!current) return current;
      const next = fitDockToViewport(current);
      persistDockPosition(next);
      return next;
    });
  };

  const dockStyle: CSSProperties | undefined = dockPosition
    ? { left: `${dockPosition.x}px`, top: `${dockPosition.y}px`, right: "auto" }
    : undefined;

  return (
    <>
    {cerebrasGeneration.active && <aside className="cerebras-generation" role="status" aria-live="polite" aria-label="Cerebras stelt het dashboard samen">
      <div className="cerebras-generation__visual" aria-hidden="true">
        <Sparkles size={18} />
        <span /><span /><span />
      </div>
      <div className="cerebras-generation__copy">
        <strong>Dashboard wordt samengesteld</strong>
        <span>{cerebrasGeneration.mode === "manual" ? "Cerebras verwerkt je herontwerp…" : "Cerebras ververst de indeling op basis van actuele data…"}</span>
      </div>
      <div className="cerebras-generation__progress" aria-hidden="true"><span /></div>
    </aside>}
    <section
      ref={dockRef}
      className="voice-dock"
      style={dockStyle}
      data-state={state}
      data-expanded={expanded ? "true" : "false"}
      data-dragging={dragging ? "true" : "false"}
      aria-label="Spraak- en tekstbediening"
    >
      <div className="voice-dock__compact">
        <button
          className="voice-drag-handle"
          onPointerDown={beginDrag}
          onPointerMove={moveDrag}
          onPointerUp={finishDrag}
          onPointerCancel={finishDrag}
          aria-label="Spraakoverlay verslepen"
          title="Spraakoverlay verslepen"
        ><GripVertical size={17} /></button>
        <VoiceRing inputLevel={() => conversation.getInputVolume?.() || 0} outputLevel={() => conversation.getOutputVolume?.() || 0} state={state} />
        {conversation.status === "connected" ? (
          <button className="voice-btn voice-btn--stop" onClick={() => void end()} title="Gesprek stoppen"><CircleStop size={19} /><span>Stop</span></button>
        ) : (
          <button className="voice-btn voice-btn--start" onClick={() => void start()} disabled={connecting} title="Gesprek starten"><Mic size={19} /><span>{connecting ? "Verbinden" : "Start"}</span></button>
        )}
        <span className="voice-compact-status" aria-live="polite">{labels[state]}</span>
        <button
          className="voice-icon-btn voice-guide-btn"
          onClick={() => {
            setGuideOpen((current) => !current);
            setExpanded(false);
          }}
          aria-expanded={guideOpen}
          aria-label={guideOpen ? "Mogelijkheden sluiten" : "Wat kan ik vragen?"}
          title="Wat kan ik vragen?"
        >
          <CircleHelp size={18} />
        </button>
        <button
          className="voice-icon-btn voice-expand-btn"
          onClick={() => {
            setExpanded((current) => !current);
            setGuideOpen(false);
          }}
          aria-expanded={expanded}
          aria-label={expanded ? "Chat sluiten" : "Chat openen"}
          title={expanded ? "Chat sluiten" : "Chat openen"}
        >
          {expanded ? <X size={18} /> : <MessageSquareText size={18} />}
        </button>
      </div>
      {operatorSelection && <div className="voice-context-chip" role="status" aria-live="polite">
        <MapPinned size={17} aria-hidden="true" />
        <span>
          <small>Gespreksfocus · {operatorSelectionMeta(operatorSelection)}</small>
          <strong title={operatorSelection.title}>{operatorSelection.title}</strong>
          {operatorSelection.description && <em title={operatorSelection.description}>{operatorSelection.description}</em>}
          <span className="voice-context-chip__location">
            {operatorSelection.latitude !== undefined && operatorSelection.longitude !== undefined
              ? `Kaartfocus · ${operatorSelection.latitude.toFixed(4)}, ${operatorSelection.longitude.toFixed(4)}`
              : operatorSelection.streamId === "p2000"
                ? "Kaartfocus wordt uit het meldingsadres bepaald"
                : "Geen kaartlocatie in dit bronrecord"}
          </span>
        </span>
        <button type="button" onClick={clearOperatorSelection} aria-label="Gespreksfocus wissen" title="Focus wissen"><X size={16} /></button>
      </div>}
      {guideOpen && <aside className="voice-guide" aria-label="Mogelijke opdrachten">
        <header className="voice-guide__head">
          <div>
            <span className="voice-guide__eyebrow"><Sparkles size={14} /> Slim dashboard</span>
            <h2>Wat kan ik vragen?</h2>
          </div>
          <button className="voice-icon-btn" onClick={() => setGuideOpen(false)} aria-label="Mogelijkheden sluiten"><X size={18} /></button>
        </header>
        <p className="voice-guide__intro">Praat of typ in gewoon Nederlands. De assistent gebruikt gevalideerde brondata en kan geen meetwaarden veranderen.</p>
        <div className="voice-guide__grid">
          <div><MapPinned size={18} /><strong>Selecteer en onderzoek</strong><span>Klik op “Als focus gebruiken” en vraag: “Wat ligt hier binnen tien kilometer?”</span></div>
          <div><BarChart3 size={18} /><strong>Vergelijk data</strong><span>“Vergelijk windstoten met incidenten van het afgelopen uur.”</span></div>
          <div><SlidersHorizontal size={18} /><strong>Pas de weergave aan</strong><span>“Maak van de windgrafiek een ranglijst.”</span></div>
          <div><Search size={18} /><strong>Zoek context</strong><span>“Welke ziekenhuizen liggen rond deze melding?”</span></div>
        </div>
        <div className="voice-guide__ai-note" data-origin={dashboardOrigin}>
          <Sparkles size={17} />
          <div>
            <strong>{dashboardOrigin === "agent" ? "AI-samengesteld" : "Automatisch startbeeld"}</strong>
            <span>{dashboardOrigin === "agent" ? "De huidige indeling is door de agent voorgesteld op basis van de beschikbare bronnen." : "De assistent kan deze indeling tijdens het gesprek aanpassen."}</span>
          </div>
        </div>
        <button className="voice-guide__details" onClick={() => { setGuideOpen(false); setInfoOpen(true); }}><Info size={16} /> Bekijk alle bronnen en instellingen</button>
      </aside>}
      {expanded && <div className="voice-dock__conversation">
        <div className="voice-status"><span>{labels[state]}</span>{error && <strong>{error}</strong>}</div>
        <div ref={transcriptRef} className="voice-transcript" aria-live="polite">
          {items.length === 0 ? <p className="voice-placeholder">Vraag naar live bronnen, een locatie, een trend of een andere dashboardweergave.</p> : items.slice(-3).map((item) => (
            <article className="voice-message" key={item.id} data-role={item.role}>
              <span className="voice-message__role">{item.role === "user" ? "Jij" : item.role === "system" ? "Systeem" : "AI-agent"}</span>
              <p>{item.text}</p>
            </article>
          ))}
        </div>
        <div className="voice-chat-row">
          <label className="sr-only" htmlFor="voice-chat-input">Typ een opdracht</label>
          <input id="voice-chat-input" value={text} onChange={(event) => { setText(event.target.value); signalUserActivity(); }} onKeyDown={(event) => { if (event.key === "Enter") void submit(); }} placeholder="Typ een opdracht…" />
          <button className="voice-icon-btn" onClick={() => void submit()} disabled={!text.trim()} aria-label="Bericht versturen"><Send size={18} /></button>
        </div>
      </div>}
    </section>
    <InfoDrawer open={infoOpen} evidenceRef={evidenceRef} onClose={() => { setInfoOpen(false); setEvidenceRef(null); }} />
    </>
  );
}
