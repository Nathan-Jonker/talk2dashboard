import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CircleStop, Info, Mic, MicOff, Send, Volume2, VolumeX } from "lucide-react";
import { useConversation } from "@elevenlabs/react";

import { invokeTool, jsonRequest, postConversationEvent, postMetric } from "./api";
import { VoiceRing } from "./VoiceRing";
import { InfoDrawer } from "./InfoDrawer";

type TranscriptItem = { id: string; role: "user" | "agent" | "system"; text: string; final: boolean };

export function VoiceDock() {
  const [items, setItems] = useState<TranscriptItem[]>([]);
  const [text, setText] = useState("");
  const [muted, setMuted] = useState(false);
  const [outputMuted, setOutputMuted] = useState(false);
  const [toolActive, setToolActive] = useState(false);
  const [error, setError] = useState("");
  const [infoOpen, setInfoOpen] = useState(false);
  const cleanupStarted = useRef(false);
  const audioSeenForTurn = useRef(false);
  const agentTextSeenForTurn = useRef(false);
  const previousMode = useRef<string | undefined>(undefined);

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
    micMuted: muted,
    onConnect: ({ conversationId }: { conversationId?: string }) => {
      cleanupStarted.current = false;
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
    onDisconnect: () => postMetric("conversation_disconnected"),
    onError: (message: unknown) => {
      setError(String(message));
      postMetric("conversation_error", { message: String(message) });
    },
    onMessage: (message) => {
      const payload = message as unknown as Record<string, unknown>;
      const role = payload.source === "user" ? "user" : "agent";
      const value = String(payload.message || payload.text || "").trim();
      if (!value) return;
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
      } else if (role === "user") {
        postMetric("user_transcript", { final: false });
      } else {
        if (!agentTextSeenForTurn.current) {
          agentTextSeenForTurn.current = true;
          postMetric("agent_text", { final: isFinal });
        }
      }
      postConversationEvent(id, role, value, isFinal);
    },
    onAudio: () => {
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
    const onTool = () => setToolActive(true);
    const onResult = () => {
      setToolActive(false);
      window.setTimeout(() => window.dispatchEvent(new CustomEvent("talk2d:refresh")), 20);
    };
    window.addEventListener("talk2d:tool-start", onTool);
    window.addEventListener("talk2d:tool-result", onResult);
    return () => {
      window.removeEventListener("talk2d:tool-start", onTool);
      window.removeEventListener("talk2d:tool-result", onResult);
    };
  }, []);

  const end = useCallback(async () => {
    if (cleanupStarted.current) return;
    cleanupStarted.current = true;
    try {
      await conversation.endSession();
    } finally {
      const id = window.localStorage.getItem("talk2d_conversation_id");
      void fetch("/api/session/end", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ conversation_id: id }) });
      window.localStorage.removeItem("talk2d_conversation_id");
      postMetric("conversation_ended");
    }
  }, [conversation]);

  useEffect(() => {
    const close = () => void end();
    window.addEventListener("pagehide", close);
    window.addEventListener("beforeunload", close);
    return () => {
      window.removeEventListener("pagehide", close);
      window.removeEventListener("beforeunload", close);
      void end();
    };
  }, [end]);

  useEffect(() => {
    const openInfo = () => setInfoOpen(true);
    window.addEventListener("talk2d:open-info", openInfo);
    return () => window.removeEventListener("talk2d:open-info", openInfo);
  }, []);

  useEffect(() => {
    if (conversation.status !== "connected") return;
    const timeout = window.setTimeout(() => void end(), 15 * 60 * 1000);
    return () => window.clearTimeout(timeout);
  }, [conversation.status, end]);

  const start = async () => {
    setError("");
    cleanupStarted.current = false;
    postMetric("connection_start");
    try {
      const token = await jsonRequest<{ conversation_token: string }>("/api/session/elevenlabs-token", { method: "POST", body: "{}" });
      const id = await conversation.startSession({ conversationToken: token.conversation_token, connectionType: "webrtc" });
      window.localStorage.setItem("talk2d_conversation_id", String(id));
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : String(cause));
    }
  };

  const submit = async () => {
    const value = text.trim();
    if (!value) return;
    if (conversation.status !== "connected") {
      try {
        const signed = await jsonRequest<{ signed_url: string }>("/api/session/elevenlabs-signed-url", { method: "POST", body: "{}" });
        const id = await conversation.startSession({ signedUrl: signed.signed_url, connectionType: "websocket", textOnly: true });
        window.localStorage.setItem("talk2d_conversation_id", String(id));
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : String(cause));
        return;
      }
    }
    conversation.sendUserActivity();
    conversation.sendUserMessage(value);
    setItems((current) => [...current.slice(-11), { id: crypto.randomUUID(), role: "user", text: value, final: true }]);
    setText("");
  };

  const state = error ? "error" : toolActive ? "tool" : conversation.isSpeaking ? "speaking" : conversation.status === "connected" ? "listening" : "idle";

  return (
    <>
    <section className="voice-dock" data-state={state} aria-label="Spraak- en tekstbediening">
      <div className="voice-dock__controls">
        <VoiceRing inputLevel={() => conversation.getInputVolume?.() || 0} outputLevel={() => conversation.getOutputVolume?.() || 0} state={state} />
        {conversation.status === "connected" ? (
          <button className="voice-btn voice-btn--stop" onClick={() => void end()} title="Gesprek stoppen"><CircleStop size={19} /><span>Stop</span></button>
        ) : (
          <button className="voice-btn voice-btn--start" onClick={() => void start()} title="Gesprek starten"><Mic size={19} /><span>Start</span></button>
        )}
        <button className="voice-icon-btn" onClick={() => setMuted(!muted)} aria-label={muted ? "Microfoon aanzetten" : "Microfoon dempen"}>{muted ? <MicOff size={18} /> : <Mic size={18} />}</button>
        <button className="voice-icon-btn" onClick={() => { setOutputMuted(!outputMuted); conversation.setVolume({ volume: outputMuted ? 1 : 0 }); }} aria-label={outputMuted ? "Geluid aanzetten" : "Geluid dempen"}>{outputMuted ? <VolumeX size={18} /> : <Volume2 size={18} />}</button>
      </div>
      <div className="voice-dock__conversation">
        <div className="voice-status"><span>{state === "idle" ? "Gereed" : state === "tool" ? "Dashboard aanpassen" : state === "speaking" ? "Agent spreekt" : state === "listening" ? "Luistert" : "Fout"}</span>{error && <strong>{error}</strong>}</div>
        <div className="voice-transcript" aria-live="polite">
          {items.length === 0 ? <p className="voice-placeholder">Vraag naar live bronnen, een locatie, een trend of een andere dashboardweergave.</p> : items.slice(-3).map((item) => <p key={item.id} data-role={item.role}><strong>{item.role === "user" ? "Jij" : "Agent"}</strong>{item.text}</p>)}
        </div>
        <div className="voice-chat-row">
          <label className="sr-only" htmlFor="voice-chat-input">Typ een opdracht</label>
          <input id="voice-chat-input" value={text} onChange={(event) => { setText(event.target.value); if (conversation.status === "connected") conversation.sendUserActivity(); }} onKeyDown={(event) => { if (event.key === "Enter") void submit(); }} placeholder="Typ een opdracht…" />
          <button className="voice-icon-btn" onClick={() => void submit()} disabled={!text.trim()} aria-label="Bericht versturen"><Send size={18} /></button>
          <button className="voice-icon-btn" onClick={() => window.dispatchEvent(new CustomEvent("talk2d:open-info"))} aria-label="Beschikbare data en mogelijkheden"><Info size={18} /></button>
        </div>
      </div>
    </section>
    <InfoDrawer open={infoOpen} onClose={() => setInfoOpen(false)} />
    </>
  );
}
