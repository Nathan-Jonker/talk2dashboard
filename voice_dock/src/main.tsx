import React from "react";
import { createRoot } from "react-dom/client";
import { ConversationProvider } from "@elevenlabs/react";
import { VoiceDock } from "./VoiceDock";
import "./styles.css";

function mount(): boolean {
  const target = document.getElementById("voice-dock-root");
  if (!target || target.dataset.mounted === "true") return Boolean(target);
  target.dataset.mounted = "true";
  createRoot(target).render(
    <React.StrictMode>
      <ConversationProvider serverLocation="eu-residency">
        <VoiceDock />
      </ConversationProvider>
    </React.StrictMode>
  );
  return true;
}

function mountWhenReady() {
  if (mount()) return;
  const observer = new MutationObserver(() => {
    if (mount()) observer.disconnect();
  });
  observer.observe(document.body, { childList: true, subtree: true });
  window.setTimeout(() => observer.disconnect(), 15_000);
}

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", mountWhenReady, { once: true });
} else mountWhenReady();
