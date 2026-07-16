/* Hallmark · pre-emit critique: P5 H5 E5 S5 R5 V4 */
(function () {
  let configPromise;
  let loadPromise;
  let selectedSourceRef = "";
  let selectedContext = null;
  const markerFocusTargets = new Set();
  const mapFocusTargets = new Set();

  try {
    const stored = JSON.parse(window.sessionStorage.getItem("talk2d_operator_selection") || "null");
    selectedContext = stored;
    selectedSourceRef = String(stored?.sourceRef || stored?.source_ref || "");
  } catch (_error) {
    selectedSourceRef = "";
  }

  function applyMarkerTargetFocus(target) {
    const active = Boolean(selectedSourceRef) && target.sourceRef === selectedSourceRef;
    target.element.classList.toggle("talk2d-map-marker--focused", active);
    target.element.setAttribute("data-focus-active", active ? "true" : "false");
  }

  function updateMarkerFocus(sourceRef) {
    selectedSourceRef = String(sourceRef || "");
    markerFocusTargets.forEach((target) => {
      if (!target.element.isConnected) {
        markerFocusTargets.delete(target);
        return;
      }
      applyMarkerTargetFocus(target);
    });
  }

  function registerMarkerFocus(point, element) {
    element.classList.add("talk2d-map-marker");
    const target = { sourceRef: String(point.sourceRef || ""), element };
    markerFocusTargets.add(target);
    applyMarkerTargetFocus(target);
  }

  function contextPoint(context) {
    if (!context) return null;
    const latitude = Number(context.latitude);
    const longitude = Number(context.longitude);
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null;
    return {
      lat: latitude,
      lng: longitude,
      title: String(context.title || "Gespreksfocus"),
      description: String(context.description || "Geselecteerd bronrecord"),
      meta: "Gespreksfocus",
      sourceRef: String(context.sourceRef || context.source_ref || ""),
      streamId: String(context.streamId || context.stream_id || ""),
      record_id: String(context.recordId || context.record_id || ""),
      color: "#f7d417",
      layer: "Gespreksfocus"
    };
  }

  function updateFocusBeacons(context) {
    selectedContext = context || null;
    mapFocusTargets.forEach((target) => {
      if (!target.host.isConnected) {
        mapFocusTargets.delete(target);
        return;
      }
      target.render(selectedContext);
    });
  }

  async function getConfig() {
    configPromise ||= fetch("/api/maps/client-config").then((response) => response.json());
    return configPromise;
  }

  async function loadGoogle() {
    if (window.google?.maps?.importLibrary) return window.google;
    if (loadPromise) return loadPromise;
    loadPromise = (async () => {
      const config = await getConfig();
      if (!config.api_key) throw new Error("GOOGLE_MAPS_NOT_CONFIGURED");
      await new Promise((resolve, reject) => {
        window.__talk2dGoogleMapsReady = resolve;
        const script = document.createElement("script");
        const params = new URLSearchParams({
          key: config.api_key,
          loading: "async",
          callback: "__talk2dGoogleMapsReady",
          v: "weekly",
          language: config.language || "nl",
          region: config.region || "NL",
          auth_referrer_policy: "origin"
        });
        if (config.map_id) params.set("map_ids", config.map_id);
        script.src = `https://maps.googleapis.com/maps/api/js?${params}`;
        script.async = true;
        script.onerror = () => reject(new Error("GOOGLE_MAPS_LOAD_FAILED"));
        document.head.append(script);
      });
      return window.google;
    })();
    return loadPromise;
  }

  function pointsFor(host) {
    try { return JSON.parse(host.dataset.points || "[]"); } catch { return []; }
  }

  function markerInk(color) {
    const value = String(color || "#e75b43").replace("#", "");
    if (!/^[0-9a-f]{6}$/i.test(value)) return "#ffffff";
    const [red, green, blue] = value.match(/.{2}/g).map((part) => parseInt(part, 16));
    const luminance = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255;
    return luminance > 0.56 ? "#102b3a" : "#ffffff";
  }

  function selectContext(point) {
    if (!point.sourceRef) return;
    window.dispatchEvent(new CustomEvent("talk2d:select-context", {
      detail: {
        source_ref: String(point.sourceRef),
        stream_id: String(point.streamId || String(point.sourceRef).split(":", 1)[0] || ""),
        record_id: String(point.record_id || ""),
        title: String(point.title || "Bronrecord"),
        description: String(point.description || ""),
        latitude: Number(point.lat),
        longitude: Number(point.lng),
        layer: String(point.layer || "")
      }
    }));
  }

  function markerContent(point) {
    const content = document.createElement("article");
    content.className = "talk2d-map-popover";

    const title = document.createElement("strong");
    title.textContent = String(point.title || "Bronrecord");
    content.append(title);

    if (point.description) {
      const description = document.createElement("p");
      description.textContent = String(point.description);
      content.append(description);
    }
    if (point.meta) {
      const meta = document.createElement("small");
      meta.textContent = String(point.meta);
      content.append(meta);
    }
    if (point.sourceRef) {
      const record = document.createElement("small");
      record.className = "talk2d-map-popover__record";
      record.textContent = `${String(point.streamId || "Bron")} · Record ${String(point.record_id || point.sourceRef)}`;
      content.append(record);

      const focus = document.createElement("button");
      focus.type = "button";
      focus.className = "talk2d-map-popover__focus";
      focus.dataset.contextSourceRef = String(point.sourceRef);
      focus.textContent = selectedSourceRef === String(point.sourceRef) ? "Focus actief" : "Als focus gebruiken";
      focus.setAttribute("aria-pressed", selectedSourceRef === String(point.sourceRef) ? "true" : "false");
      focus.addEventListener("click", () => selectContext(point));
      content.append(focus);

      const evidence = document.createElement("button");
      evidence.type = "button";
      evidence.className = "talk2d-map-popover__evidence";
      evidence.dataset.sourceRef = String(point.sourceRef);
      evidence.textContent = "Herkomst bekijken";
      evidence.addEventListener("click", () => {
        window.dispatchEvent(new CustomEvent("talk2d:open-evidence", {
          detail: { source_ref: String(point.sourceRef) }
        }));
      });
      content.append(evidence);
    }
    return content;
  }

  async function mount2d(host, points, config) {
    const [{ Map, InfoWindow }, { AdvancedMarkerElement, PinElement }] = await Promise.all([
      google.maps.importLibrary("maps"),
      google.maps.importLibrary("marker")
    ]);
    const center = points[0] || { lat: 52.12, lng: 5.29 };
    const map = new Map(host, {
      center,
      zoom: points.length ? 9 : 7,
      mapId: config.map_id || "DEMO_MAP_ID",
      gestureHandling: "cooperative",
      mapTypeControl: false,
      streetViewControl: false,
      fullscreenControl: true
    });
    host.__talk2dMap = map;
    const infoWindow = new InfoWindow({ maxWidth: 320 });
    const sourceRefs = new Set(points.map((point) => String(point.sourceRef || "")));
    let focusMarker;
    points.slice(0, 500).forEach((point) => {
      const pin = new PinElement({
        background: String(point.color || "#e75b43"),
        borderColor: "#ffffff",
        glyphColor: markerInk(point.color),
        scale: 1.05
      });
      registerMarkerFocus(point, pin.element);
      const marker = new AdvancedMarkerElement({
        map,
        position: { lat: Number(point.lat), lng: Number(point.lng) },
        title: String(point.title || "Bronrecord"),
        gmpClickable: true,
        content: pin.element
      });
      marker.addEventListener("gmp-click", () => {
        infoWindow.close();
        infoWindow.setContent(markerContent(point));
        infoWindow.open({ map, anchor: marker, shouldFocus: false });
      });
    });
    const focusTarget = {
      host,
      render(context) {
        if (focusMarker) {
          focusMarker.map = null;
          focusMarker = undefined;
        }
        const point = contextPoint(context);
        if (!point || sourceRefs.has(point.sourceRef)) return;
        const pin = new PinElement({
          background: "#f7d417",
          borderColor: "#003b5c",
          glyphColor: "#003b5c",
          glyphText: "★",
          scale: 1.25
        });
        pin.element.classList.add("talk2d-map-focus-beacon");
        focusMarker = new AdvancedMarkerElement({
          map,
          position: { lat: point.lat, lng: point.lng },
          title: `Gespreksfocus: ${point.title}`,
          gmpClickable: true,
          zIndex: 1000,
          content: pin.element
        });
        focusMarker.addEventListener("gmp-click", () => {
          infoWindow.close();
          infoWindow.setContent(markerContent(point));
          infoWindow.open({ map, anchor: focusMarker, shouldFocus: false });
        });
        map.panTo({ lat: point.lat, lng: point.lng });
      }
    };
    mapFocusTargets.add(focusTarget);
    focusTarget.render(selectedContext);
  }

  async function mount3d(host, points, config) {
    const [{ Map3DElement, Marker3DInteractiveElement, PopoverElement }, { PinElement }] =
      await Promise.all([
        google.maps.importLibrary("maps3d"),
        google.maps.importLibrary("marker")
      ]);
    const center = points[0] || { lat: 52.12, lng: 5.29 };
    const map = new Map3DElement({
      center: { lat: Number(center.lat), lng: Number(center.lng), altitude: 0 },
      range: points.length > 1 ? 80000 : 20000,
      tilt: 55,
      heading: 0,
      mode: "HYBRID",
      mapId: config.map_id || undefined,
      gestureHandling: "COOPERATIVE"
    });
    map.style.width = "100%";
    map.style.height = "100%";
    const popover = new PopoverElement({ open: false });
    let activeMarker;
    let focusMarker;
    const sourceRefs = new Set(points.map((point) => String(point.sourceRef || "")));
    points.slice(0, 250).forEach((point) => {
      const marker = new Marker3DInteractiveElement({
        position: { lat: Number(point.lat), lng: Number(point.lng), altitude: 0 }
      });
      const pin = new PinElement({
        background: String(point.color || "#e75b43"),
        borderColor: "#ffffff",
        glyphColor: markerInk(point.color),
        scale: 1.05
      });
      registerMarkerFocus(point, pin.element);
      marker.append(pin);
      marker.setAttribute("aria-label", String(point.title || "Bronrecord"));
      marker.addEventListener("gmp-click", () => {
        if (activeMarker === marker && popover.open) {
          popover.open = false;
          activeMarker = undefined;
          return;
        }
        popover.open = false;
        popover.positionAnchor = marker;
        popover.replaceChildren(markerContent(point));
        popover.open = true;
        activeMarker = marker;
      });
      map.append(marker);
    });
    map.append(popover);
    const focusTarget = {
      host,
      render(context) {
        if (focusMarker) {
          focusMarker.remove();
          focusMarker = undefined;
        }
        const point = contextPoint(context);
        if (!point || sourceRefs.has(point.sourceRef)) return;
        focusMarker = new Marker3DInteractiveElement({
          position: { lat: point.lat, lng: point.lng, altitude: 0 }
        });
        const pin = new PinElement({
          background: "#f7d417",
          borderColor: "#003b5c",
          glyphColor: "#003b5c",
          glyphText: "★",
          scale: 1.25
        });
        pin.element.classList.add("talk2d-map-focus-beacon");
        focusMarker.append(pin);
        focusMarker.setAttribute("aria-label", `Gespreksfocus: ${point.title}`);
        focusMarker.addEventListener("gmp-click", () => {
          popover.open = false;
          popover.positionAnchor = focusMarker;
          popover.replaceChildren(markerContent(point));
          popover.open = true;
          activeMarker = focusMarker;
        });
        map.append(focusMarker);
        map.center = { lat: point.lat, lng: point.lng, altitude: 0 };
      }
    };
    mapFocusTargets.add(focusTarget);
    focusTarget.render(selectedContext);
    host.__talk2dMap = map;
    host.replaceChildren(map);
  }

  async function mount(host) {
    if (host.dataset.mounted === "true") return;
    host.dataset.mounted = "true";
    const fallback = host.parentElement?.querySelector(".talk2d-map-fallback");
    try {
      const config = await getConfig();
      await loadGoogle();
      const points = pointsFor(host);
      if (host.dataset.mapMode === "3d") await mount3d(host, points, config);
      else await mount2d(host, points, config);
      host.dataset.status = "ready";
      if (fallback) fallback.hidden = true;
    } catch (error) {
      host.dataset.status = "fallback";
      host.replaceChildren(Object.assign(document.createElement("div"), {
        className: "talk2d-map-loading",
        textContent: "Google Maps niet geconfigureerd; open-data kaartfallback actief."
      }));
      if (fallback) fallback.hidden = false;
    }
  }

  function scan() {
    document.querySelectorAll(".talk2d-google-map").forEach((host) => void mount(host));
  }
  new MutationObserver(scan).observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", scan, { once: true });
  window.addEventListener("talk2d:refresh", () => window.setTimeout(scan, 30));
  window.addEventListener("talk2d:context-changed", (event) => {
    updateMarkerFocus(event.detail?.source_ref || "");
  });
  window.addEventListener("talk2d:select-context", (event) => {
    updateMarkerFocus(event.detail?.source_ref || "");
    updateFocusBeacons(event.detail || null);
  });
  window.addEventListener("talk2d:clear-context", () => {
    updateMarkerFocus("");
    updateFocusBeacons(null);
  });
  window.addEventListener("resize", () => {
    document.querySelectorAll(".talk2d-google-map").forEach((host) => {
      if (host.__talk2dMap && window.google?.maps?.event) {
        window.google.maps.event.trigger(host.__talk2dMap, "resize");
      }
    });
  });
})();
