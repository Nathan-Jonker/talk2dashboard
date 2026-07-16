(function () {
  let initializationRequested = false;
  const generationRequests = new Map();
  const operatorSelectionKey = "talk2d_operator_selection";
  let selectedContextRef = "";
  let focusRefreshScheduled = false;

  function storedContextRef() {
    try {
      const stored = JSON.parse(window.sessionStorage.getItem(operatorSelectionKey) || "null");
      return String(stored?.sourceRef || stored?.source_ref || "");
    } catch (_error) {
      return "";
    }
  }

  function applyContextFocus(sourceRef) {
    selectedContextRef = String(sourceRef || "");
    if (selectedContextRef) document.documentElement.dataset.focusSourceRef = selectedContextRef;
    else delete document.documentElement.dataset.focusSourceRef;
    document.querySelectorAll(".talk2d-feed-item[data-focus='true']").forEach(function (item) {
      delete item.dataset.focus;
      item.removeAttribute("aria-current");
    });
    document.querySelectorAll("[data-context-source-ref]").forEach(function (target) {
      const active = Boolean(selectedContextRef) && target.dataset.contextSourceRef === selectedContextRef;
      target.dataset.focusActive = active ? "true" : "false";
      target.setAttribute("aria-pressed", active ? "true" : "false");
      if (target.classList.contains("talk2d-context-ref")) {
        const label = active ? "Gespreksfocus actief" : "Als gespreksfocus gebruiken";
        target.setAttribute("aria-label", label);
        target.setAttribute("title", label);
        const item = target.closest(".talk2d-feed-item");
        if (item && active) {
          item.dataset.focus = "true";
          item.setAttribute("aria-current", "true");
        }
      }
    });
    window.dispatchEvent(new CustomEvent("talk2d:context-changed", {
      detail: { source_ref: selectedContextRef || null }
    }));
  }

  function scheduleContextFocus() {
    if (focusRefreshScheduled) return;
    focusRefreshScheduled = true;
    window.requestAnimationFrame(function () {
      focusRefreshScheduled = false;
      applyContextFocus(selectedContextRef || storedContextRef());
    });
  }

  function setCerebrasGeneration(requestId, active, mode) {
    if (active) generationRequests.set(requestId, mode);
    else generationRequests.delete(requestId);
    const currentModes = [...generationRequests.values()];
    const detail = {
      active: currentModes.length > 0,
      mode: currentModes.at(-1) || mode,
      request_id: requestId
    };
    document.documentElement.dataset.cerebrasGenerating = detail.active ? "true" : "false";
    window.dispatchEvent(new CustomEvent("talk2d:cerebras-generation", { detail: detail }));
  }

  window.talk2dSetCerebrasGeneration = setCerebrasGeneration;

  function afterPaint(callback) {
    window.requestAnimationFrame(function () {
      window.requestAnimationFrame(callback);
    });
  }

  function readyContext() {
    const node = document.getElementById("render-context");
    if (!node || !node.textContent) return null;
    try { return JSON.parse(node.textContent); } catch (_error) { return null; }
  }

  function waitUntilReady(expectedVersion, expectedBundle, timeoutMs) {
    const started = performance.now();
    return new Promise(function (resolve, reject) {
      function check() {
        const context = readyContext();
        const mapsReady = ![...document.querySelectorAll(".talk2d-google-map")]
          .some((node) => !["ready", "fallback"].includes(node.dataset.status));
        const panels = new Set([...document.querySelectorAll("[data-panel-id]")]
          .map((node) => node.dataset.panelId));
        const handles = new Set();
        document.querySelectorAll("[data-handle-id], [data-handle-ids]").forEach((node) => {
          if (node.dataset.handleId) handles.add(node.dataset.handleId);
          if (node.dataset.handleIds) {
            try {
              JSON.parse(node.dataset.handleIds).filter(Boolean).forEach((id) => handles.add(id));
            } catch (_error) {
              // Invalid presentation metadata cannot acknowledge a render.
            }
          }
        });
        const panelsReady = context && context.panel_ids.every((id) => panels.has(id));
        const handlesReady = context && context.handle_ids.every((id) => handles.has(id));
        if (context && (!expectedVersion || context.dashboard_version === expectedVersion)
            && (!expectedBundle || context.source_bundle_version === expectedBundle)
            && !document.querySelector(".dash-spinner") && mapsReady && panelsReady && handlesReady) {
          resolve(context);
          return;
        }
        if (performance.now() - started > timeoutMs) {
          reject(new Error("RENDER_ACK_TIMEOUT"));
          return;
        }
        window.setTimeout(check, 50);
      }
      afterPaint(check);
    });
  }

  function acknowledge(expectedVersion, expectedBundle, eventType, timeoutMs) {
    return waitUntilReady(expectedVersion, expectedBundle, timeoutMs || 10000).then(function (context) {
      const captureMode = new URLSearchParams(window.location.search).get("capture") === "1";
      if (captureMode) {
        window.__talk2dRenderReady = context;
        return context;
      }
      return fetch("/api/dashboard/render-ack", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          dashboard_version: context.dashboard_version,
          source_bundle_version: context.source_bundle_version,
          handle_ids: context.handle_ids,
          status: "rendered"
        })
      }).then(function (response) {
        if (!response.ok) throw new Error("RENDER_ACK_FAILED");
        afterPaint(function () {
          window.dispatchEvent(new Event("resize"));
        });
        fetch("/api/metrics/event", {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            conversation_id: window.localStorage.getItem("talk2d_conversation_id"),
            turn_id: window.localStorage.getItem("talk2d_turn_id"),
            event_type: "render_complete", monotonic_ms: performance.now(),
            payload: { event_type: eventType, dashboard_version: context.dashboard_version }
          })
        });
        return context;
      });
    });
  }

  function awaitDashboardRender(dashboardVersion, timeoutMs) {
    if (window.dash_clientside && window.dash_clientside.set_props) {
      window.dash_clientside.set_props("dashboard-event-store", {
        data: {
          type: "dashboard_updated",
          dashboard_version: dashboardVersion,
          requested_at: Date.now()
        }
      });
    }
    return acknowledge(dashboardVersion, null, "dashboard_redesign", timeoutMs || 20000);
  }

  window.talk2dAwaitDashboardRender = awaitDashboardRender;

  function requestAutomaticComposition() {
    const requestId = "automatic-" + Date.now();
    setCerebrasGeneration(requestId, true, "automatic");
    return fetch("/api/dashboard/initialize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}"
    }).then(function (response) {
      if (!response.ok) throw new Error("DASHBOARD_INITIALIZATION_FAILED");
      return response.json();
    }).then(function (result) {
      if (!result.changed) return null;
      return awaitDashboardRender(result.dashboard_version, 20000);
    }).catch(function () {
      return null;
    }).finally(function () {
      setCerebrasGeneration(requestId, false, "automatic");
    });
  }

  function initializeAfterFirstRender() {
    if (initializationRequested) return;
    initializationRequested = true;
    void requestAutomaticComposition();
  }

  function acknowledgeEvent(expectedVersion, expectedBundle, eventType) {
    return acknowledge(expectedVersion, expectedBundle, eventType).then(function (context) {
        if (eventType === "initial" && !initializationRequested) {
          initializeAfterFirstRender();
        }
        return context;
      });
  }

  function connect() {
    const stream = new EventSource("/api/dashboard/events");
    stream.onmessage = function (event) {
      try {
        const data = JSON.parse(event.data);
        if (window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props("dashboard-event-store", { data: data });
          if (data.type === "dashboard_updated" || data.type === "source_bundle" || data.type === "dashboard_ready") {
            acknowledgeEvent(
              data.dashboard_version || null,
              data.source_bundle_version || null,
              data.type
            ).catch(function () {});
          }
        }
      } catch (_error) {
        // Invalid events are ignored; the server remains authoritative.
      }
    };
    stream.onerror = function () {
      stream.close();
      window.setTimeout(connect, 2000);
    };
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () {
      connect();
      applyContextFocus(storedContextRef());
    }, { once: true });
  } else {
    connect();
    applyContextFocus(storedContextRef());
  }
  window.addEventListener("talk2d:select-context", function (event) {
    applyContextFocus(event.detail?.source_ref || "");
  });
  window.addEventListener("talk2d:clear-context", function () {
    applyContextFocus("");
  });
  new MutationObserver(scheduleContextFocus).observe(document.documentElement, {
    childList: true,
    subtree: true
  });
  document.addEventListener("click", function (event) {
    const contextTarget = event.target.closest && event.target.closest("[data-context-source-ref]");
    if (contextTarget && contextTarget.dataset.contextSourceRef) {
      window.dispatchEvent(new CustomEvent("talk2d:select-context", {
        detail: {
          source_ref: contextTarget.dataset.contextSourceRef,
          stream_id: contextTarget.dataset.contextStreamId || "",
          record_id: contextTarget.dataset.contextRecordId || "",
          title: contextTarget.dataset.contextTitle || "Bronrecord",
          description: contextTarget.dataset.contextDescription || "",
          latitude: contextTarget.dataset.contextLatitude
            ? Number(contextTarget.dataset.contextLatitude) : undefined,
          longitude: contextTarget.dataset.contextLongitude
            ? Number(contextTarget.dataset.contextLongitude) : undefined
        }
      }));
      return;
    }
    const target = event.target.closest && event.target.closest("[data-source-ref]");
    if (target && target.dataset.sourceRef) {
      window.dispatchEvent(new CustomEvent("talk2d:open-evidence", {
        detail: { source_ref: target.dataset.sourceRef }
      }));
    }
  });
  acknowledgeEvent(
    null,
    null,
    new URLSearchParams(window.location.search).get("capture") === "1" ? "capture" : "initial"
  ).catch(function () {});
})();
