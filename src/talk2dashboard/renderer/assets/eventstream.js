(function () {
  function connect() {
    const stream = new EventSource("/api/dashboard/events");
    stream.onmessage = function (event) {
      try {
        const data = JSON.parse(event.data);
        if (window.dash_clientside && window.dash_clientside.set_props) {
          window.dash_clientside.set_props("dashboard-event-store", { data: data });
          if (data.type === "dashboard_updated" || data.type === "source_bundle") {
            window.requestAnimationFrame(function () {
              window.setTimeout(function () {
                fetch("/api/metrics/event", {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({
                    conversation_id: window.localStorage.getItem("talk2d_conversation_id"),
                    turn_id: window.localStorage.getItem("talk2d_turn_id"),
                    event_type: "render_complete",
                    monotonic_ms: performance.now(),
                    payload: { event_type: data.type }
                  })
                });
              }, 40);
            });
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
    document.addEventListener("DOMContentLoaded", connect, { once: true });
  } else {
    connect();
  }
})();
